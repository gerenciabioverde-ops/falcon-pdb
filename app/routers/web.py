"""Rutas HTTP de Falcon PDB: login, empresas, declaraciones por período."""
import json
import re
from decimal import Decimal
from pathlib import Path
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import crear_token, verify_password
from app.database import get_db
from app.hojas import (ARCHIVO_BLOQUE, TITULO_SUB, BLOQUES_TABULARES, COLUMNAS_TODAS, PESTANA, PESTANAS,
                       TITULOS_BLOQUE)
from sqlalchemy import select
from app.models import ESTADOS, Declaracion, Detraccion, Empresa, Form1662, Usuario
from app.services import detracciones as detr
from app.services import form1662 as f1662
from app.services import archivos_sunat as arch
from app.services import cargas
from app.services import pdb as svc

AYUDA = json.loads((Path(__file__).resolve().parent.parent / "ayuda_sunat.json").read_text(encoding="utf-8"))
AYUDA["REF"] = {"nombre": "N° de DUA (seguimiento)", "tipo": "Texto", "largo": "",
                "texto": "Se arma solo con D1, D2 y D3 (aduana-año-régimen 40-correlativo). Solo para consulta; no va al archivo de SUNAT."}
AYUDA["FAC"] = {"nombre": "Factura(s) de la DUA", "tipo": "Texto", "largo": "",
                "texto": "Escriba SERIE-NÚMERO separadas por coma. Ej.: FFF1-651, FFF1-652.\nAl guardar se enlazan al bloque E de esta DUA:\n"
                         "· las nuevas se crean (si es una sola, con el FOB que falte para completar el de la DUA);\n"
                         "· las que quite se eliminan del bloque E.\nAfecta el archivo .du1; no va en el .dua."}
router = APIRouter()
_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _miles(valor, decimales: int = 2) -> str:
    """12345.6 → '12,345.60' (solo para mostrar; lo guardado y los archivos no llevan comas)."""
    txt = "" if valor is None else str(valor).strip()
    if not _NUM.match(txt):
        return txt
    return f"{Decimal(txt):,.{decimales}f}"


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Setiembre", "Octubre", "Noviembre", "Diciembre"]
Db = Annotated[Session, Depends(get_db)]
User = Annotated[Usuario, Depends(get_current_user)]


templates.env.filters["miles"] = _miles


def _render(request: Request, nombre: str, **ctx):
    return templates.TemplateResponse(request, nombre, {"meses": MESES, "estados": ESTADOS, **ctx})


def _decl(db: Session, decl_id: int) -> Declaracion:
    decl = db.get(Declaracion, decl_id)
    if not decl:
        raise HTTPException(404, "Declaración no encontrada")
    return decl


# ---- auth ------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _render(request, "login.html", error=None)


@router.post("/login")
def login(request: Request, db: Db, username: Annotated[str, Form()], password: Annotated[str, Form()]):
    u = db.query(Usuario).filter(Usuario.username == username.strip()).first()
    if not u or not verify_password(password, u.password_hash):
        return _render(request, "login.html", error="Usuario o contraseña incorrectos")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("pdb_token", crear_token(u.id), httponly=True, samesite="lax")
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("pdb_token")
    return resp


# ---- empresas y declaraciones ---------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def inicio(request: Request, db: Db, usuario: User, empresa_id: int | None = None, anio: int | None = None):
    empresas = db.query(Empresa).order_by(Empresa.razon_social).all()
    empresa = next((e for e in empresas if e.id == empresa_id), None) or (empresas[0] if len(empresas) == 1 else None)
    hoy = date.today()
    anios, meses_info = [], []
    if empresa:
        existentes = {d.anio for d in empresa.declaraciones}
        if anio is None or not 2000 <= anio <= 2100:
            anio = hoy.year if (hoy.year in existentes or not existentes) else max(existentes)
        anios = sorted(existentes | {hoy.year, anio}, reverse=True)
        por_mes = {d.mes: d for d in empresa.declaraciones if d.anio == anio}
        for m in range(1, 13):
            d = por_mes.get(m)
            meses_info.append({"mes": m, "decl": d, **(svc.resumen_declaracion(d) if d else {})})
    return _render(request, "inicio.html", usuario=usuario, empresas=empresas, empresa=empresa, anio=anio,
                   anios=anios, meses_info=meses_info, hoy=hoy,
                   totales={e: sum(1 for m in meses_info if m["decl"] and m["decl"].estado == e) for e in ESTADOS},
                   con_errores=sum(1 for m in meses_info if m.get("errores") and (m["compras"] or m["ventas"] or m["duas"])))


@router.post("/empresas")
def crear_empresa(db: Db, usuario: User, ruc: Annotated[str, Form()], razon_social: Annotated[str, Form()]):
    ruc = ruc.strip()
    if len(ruc) == 11 and ruc.isdigit() and not db.query(Empresa).filter_by(ruc=ruc).first():
        e = Empresa(ruc=ruc, razon_social=razon_social.strip())
        db.add(e)
        db.commit()
        return RedirectResponse(f"/?empresa_id={e.id}", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.post("/declaraciones")
def crear_declaracion(db: Db, usuario: User, empresa_id: Annotated[int, Form()],
                      anio: Annotated[int, Form()], mes: Annotated[int, Form()]):
    if not 1 <= mes <= 12:
        return RedirectResponse("/", status_code=303)
    decl = db.query(Declaracion).filter_by(empresa_id=empresa_id, anio=anio, mes=mes).first()
    if not decl:
        decl = Declaracion(empresa_id=empresa_id, anio=anio, mes=mes)
        db.add(decl)
        db.commit()
    return RedirectResponse(f"/declaraciones/{decl.id}", status_code=303)


def _volver(decl_id: int, hoja: str, msg: str = "", mes: str = "", sub: str = "") -> RedirectResponse:
    return RedirectResponse(f"/declaraciones/{decl_id}?hoja={hoja}" + (f"&mes={mes}" if mes else "")
                            + (f"&sub={sub}" if sub else "") + (f"&msg={msg}" if msg else ""), status_code=303)


def _mes_tc(txt: str) -> tuple[int, int] | None:
    """'2026-09' → (2026, 9); cualquier otra cosa → None (se usa el mes del período)."""
    try:
        a, m = txt.split("-")
        a, m = int(a), int(m)
        return (a, m) if 2000 <= a <= 2100 and 1 <= m <= 12 else None
    except ValueError:
        return None


@router.get("/declaraciones/{decl_id}", response_class=HTMLResponse)
def ver_declaracion(request: Request, decl_id: int, db: Db, usuario: User, hoja: str = "resumen", msg: str = "",
                    mes: str = "", sub: str = ""):
    decl = _decl(db, decl_id)
    pestana = PESTANA.get(hoja) or PESTANA["resumen"]
    hallazgos = svc.validar(decl)
    cuentas = {p["clave"]: sum(len(svc.filas_bloque(decl, b)) for b, _ in p["bloques"]) for p in PESTANAS}
    tc_sel = _mes_tc(mes) or (decl.anio, decl.mes)           # mes del TC que se está viendo
    subs, sub_sel = [], None
    visibles = pestana["bloques"]
    if pestana.get("subpestanas"):                      # p. ej. COMPRAS: «Comprobantes» y «Forma de pago»
        claves = [b for b, _ in pestana["bloques"]]
        sub_sel = sub if sub in claves else claves[0]
        subs = [{"clave": b, "titulo": TITULO_SUB.get(b, t), "n": len(svc.filas_bloque(decl, b))} for b, t in pestana["bloques"]]
        visibles = [(b, t) for b, t in pestana["bloques"] if b == sub_sel]
    detracciones, ligadas, forms1662, compras_1662 = [], set(), [], set()
    if pestana["clave"] == "compras":                   # 3.ª sub-pestaña: constancias de detracción (HTML SUNAT)
        detracciones = list(db.scalars(select(Detraccion).where(Detraccion.empresa_id == decl.empresa_id)
                                       .order_by(Detraccion.fecha_pago.desc(), Detraccion.id.desc())))
        ligadas = detr.enlazadas(db, decl.empresa)
        subs.append({"clave": "DETRACCIONES", "titulo": "Detracciones", "n": len(detracciones)})
        forms1662 = list(db.scalars(select(Form1662).where(Form1662.empresa_id == decl.empresa_id)
                                    .order_by(Form1662.fecha_pago.desc(), Form1662.id.desc())))
        compras_1662 = f1662.enlazadas(db, decl.empresa)
        subs.append({"clave": "FORM1662", "titulo": "Form. 1662", "n": len(forms1662)})
        if sub in ("DETRACCIONES", "FORM1662"):
            sub_sel, visibles = sub, []
    bloques = [{"clave": b, "titulo": t, "columnas": COLUMNAS_TODAS[b], "filas": svc.filas_bloque(decl, b, tc_sel)}
               for b, t in visibles]
    for b in bloques:
        b["extra"] = svc.dam_de_ventas(db, decl) if b["clave"] == "VENTAS" else None
    if pestana["clave"] == "tc":
        bloques[0]["titulo"] = f"Tipo de cambio · {MESES[tc_sel[1]]} {tc_sel[0]}"
    archivos = arch.archivos(decl)
    ruc, per = decl.empresa.ruc, decl.periodo
    nombre_de = {b: f"{p}{ruc}{per if cp else ''}{e}" for b, (p, e, cp) in ARCHIVO_BLOQUE.items()}
    nombre_de.update({"DUAS_D": f"{ruc}{per}.dua", "DUAS_E": f"{ruc}{per}.du1"})
    archivo_de = {b: n for b, n in nombre_de.items() if n in archivos}
    return _render(request, "declaracion.html", usuario=usuario, decl=decl, msg=msg, pestana=pestana, archivo_de=archivo_de,
                   pestanas=PESTANAS, cuentas=cuentas, bloques=bloques, hallazgos=hallazgos,
                   n_errores=sum(h["nivel"] == "error" for h in hallazgos), archivos=archivos,
                   archivo_bloque=ARCHIVO_BLOQUE, tc_sel=tc_sel, subs=subs, sub_sel=sub_sel,
                   detracciones=detracciones, ligadas=ligadas, forms1662=forms1662, compras_1662=compras_1662,
                   tc_meses=svc.meses_tc(db) if pestana["clave"] == "tc" else [],
                   ayuda={c[0]: AYUDA[c[0]] for b in bloques for c in b["columnas"] if c[0] in AYUDA})


@router.post("/declaraciones/{decl_id}/bloque/{bloque}")
def guardar_bloque(decl_id: int, bloque: str, db: Db, usuario: User, hoja: Annotated[str, Form()],
                   datos: Annotated[str, Form()], mes: Annotated[str, Form()] = "", sub: Annotated[str, Form()] = ""):
    decl = _decl(db, decl_id)
    if bloque not in COLUMNAS_TODAS:
        raise HTTPException(404, "Bloque desconocido")
    try:
        filas = json.loads(datos)
        antes = len(decl.cdps)
        n = svc.guardar_grilla(db, decl, bloque, filas, _mes_tc(mes))
        db.commit()
        msg = f"Guardado: {n} fila(s)."
        if bloque == "DUAS_D" and len(decl.cdps) != antes:
            d = len(decl.cdps) - antes
            msg += f" Bloque E: {abs(d)} factura(s) {'agregada(s)' if d > 0 else 'quitada(s)'}" + (" (revise el FOB en «Bloque E · Facturas»)." if d > 0 else ".")
    except (ValueError, TypeError) as e:
        db.rollback()
        msg = f"Error: {e}"
    return _volver(decl_id, hoja, msg, mes, sub)


@router.post("/declaraciones/{decl_id}/detracciones")
async def subir_detracciones(decl_id: int, request: Request, db: Db, usuario: User):
    decl = _decl(db, decl_id)
    form = await request.form(max_files=2000, max_fields=2000)
    subidos = [(a.filename, await a.read()) for a in form.getlist("archivos") if getattr(a, "filename", None)]
    if not subidos:
        return _volver(decl_id, "compras", "Error: seleccione al menos una constancia HTML.", "", "DETRACCIONES")
    n, avisos = detr.cargar_constancias(db, decl.empresa, subidos)
    aplicadas = detr.aplicar_a_compras(db, decl.empresa)
    db.commit()
    msg = (f"Constancias cargadas: {n}. Compras actualizadas: {aplicadas} (en todos los períodos)."
           + (f" Avisos ({len(avisos)}): " + " · ".join(avisos[:3]) if avisos else ""))
    return _volver(decl_id, "compras", msg if n else "Error: " + " · ".join(avisos[:3]), "", "DETRACCIONES")


@router.post("/declaraciones/{decl_id}/detracciones/aplicar")
def aplicar_detracciones(decl_id: int, db: Db, usuario: User):
    decl = _decl(db, decl_id)
    n = detr.aplicar_a_compras(db, decl.empresa)
    db.commit()
    return _volver(decl_id, "compras", f"Compras actualizadas con su detracción: {n} (todos los períodos).", "", "DETRACCIONES")


@router.post("/declaraciones/{decl_id}/detracciones/{det_id}/eliminar")
def eliminar_detraccion(decl_id: int, det_id: int, db: Db, usuario: User):
    decl = _decl(db, decl_id)
    d = db.get(Detraccion, det_id)
    if d and d.empresa_id == decl.empresa_id:
        db.delete(d)
        db.commit()
    return _volver(decl_id, "compras", "Constancia eliminada (las compras ya rellenadas conservan sus datos).", "", "DETRACCIONES")


@router.post("/declaraciones/{decl_id}/form1662")
async def subir_form1662(decl_id: int, request: Request, db: Db, usuario: User):
    decl = _decl(db, decl_id)
    form = await request.form(max_files=2000, max_fields=2000)
    subidos = [(a.filename, await a.read()) for a in form.getlist("archivos") if getattr(a, "filename", None)]
    if not subidos:
        return _volver(decl_id, "compras", "Error: seleccione al menos un PDF del Formulario 1662.", "", "FORM1662")
    n, avisos = f1662.cargar_constancias(db, decl.empresa, subidos)
    aplicadas = f1662.aplicar_a_compras(db, decl.empresa)
    db.commit()
    msg = (f"Formularios 1662 cargados: {n}. Compras actualizadas (IGV A19): {aplicadas}, en todos los períodos."
           + (f" Avisos ({len(avisos)}): " + " · ".join(avisos[:3]) if avisos else ""))
    return _volver(decl_id, "compras", msg if n else "Error: " + " · ".join(avisos[:3]), "", "FORM1662")


@router.post("/declaraciones/{decl_id}/form1662/aplicar")
def aplicar_form1662(decl_id: int, db: Db, usuario: User):
    decl = _decl(db, decl_id)
    n = f1662.aplicar_a_compras(db, decl.empresa)
    db.commit()
    return _volver(decl_id, "compras", f"Compras actualizadas con el IGV del Formulario 1662: {n} (todos los períodos).", "", "FORM1662")


@router.post("/declaraciones/{decl_id}/form1662/{fid}/eliminar")
def eliminar_form1662(decl_id: int, fid: int, db: Db, usuario: User):
    decl = _decl(db, decl_id)
    f = db.get(Form1662, fid)
    if f and f.empresa_id == decl.empresa_id:
        db.delete(f)
        db.commit()
    return _volver(decl_id, "compras", "Formulario 1662 eliminado (las compras ya rellenadas conservan su IGV).", "", "FORM1662")


@router.post("/declaraciones/{decl_id}/importar-duas")
async def importar_duas(decl_id: int, db: Db, usuario: User, archivo: Annotated[UploadFile, File()]):
    decl = _decl(db, decl_id)
    try:
        nd, nc = svc.importar_duas_csv(db, decl, await archivo.read())
        db.commit()
        msg = f"Importadas {nd} DUA(s) y {nc} factura(s)."
    except ValueError as e:
        db.rollback()
        msg = f"Error: {e}"
    return _volver(decl_id, "duas", msg)


@router.post("/declaraciones/{decl_id}/importar-hoja")
async def importar_hoja(decl_id: int, db: Db, usuario: User, hoja: Annotated[str, Form()],
                        pestana: Annotated[str, Form()], archivo: Annotated[UploadFile, File()]):
    decl = _decl(db, decl_id)
    if hoja not in BLOQUES_TABULARES:
        return _volver(decl_id, pestana, "Error: hoja desconocida")
    try:
        n = svc.importar_hoja_csv(db, decl, hoja, await archivo.read())
        db.commit()
        msg = f"{TITULOS_BLOQUE[hoja]}: {n} fila(s) importadas."
    except ValueError as e:
        db.rollback()
        msg = f"Error: {e}"
    return _volver(decl_id, pestana, msg)


SUB_DE_CARGA = {"compras": "COMPRAS", "pagos": "COMPRAS_PAGO", "pagos_bancos": "COMPRAS_PAGO", "duas": "DUAS_D"}
PESTANA_DE_CARGA = {"tc": "tc", "ventas": "ventas", "compras": "compras", "pagos": "compras",
                    "pagos_bancos": "compras", "duas": "duas"}


@router.post("/declaraciones/{decl_id}/cargar/{tipo}")
async def cargar(decl_id: int, tipo: str, request: Request, db: Db, usuario: User):
    """Llena una hoja desde los documentos de origen (PDF de TC, XML, PDF de DAM) o,
    para `pagos`, desde las compras ya cargadas."""
    decl = _decl(db, decl_id)
    mes_ver = ""
    if tipo not in PESTANA_DE_CARGA:
        raise HTTPException(404, "Tipo de carga desconocido")
    try:
        if tipo == "pagos":
            n, avisos = cargas.generar_pagos_desde_compras(db, decl)
        else:
            try:                                   # una carpeta puede traer cientos de archivos
                form = await request.form(max_files=20000, max_fields=20000)
            except TypeError:                      # Starlette antiguo, sin esos parámetros
                form = await request.form()
            subidos = [(a.filename, await a.read()) for a in form.getlist("archivos")
                       if getattr(a, "filename", None)]
            if not subidos:
                raise ValueError("Seleccione al menos un archivo.")
            if tipo == "tc":
                n, avisos, ultimo = 0, [], None
                for nombre, contenido in subidos:
                    try:
                        k, av, ultimo = cargas.cargar_tc_pdf(db, contenido)
                    except ValueError as e:
                        avisos.append(f"{nombre}: {e}")
                        continue
                    n += k
                    avisos.append(f"{MESES[ultimo[1]]} {ultimo[0]}: {k} día(s).")
                mes_ver = f"{ultimo[0]}-{ultimo[1]:02d}" if ultimo else ""
            elif tipo == "ventas":
                n, avisos = cargas.cargar_ventas_xml(db, decl, subidos)
            elif tipo == "compras":
                n, avisos = cargas.cargar_compras_xml(db, decl, subidos)
            elif tipo == "pagos_bancos":
                n, avisos = cargas.cargar_pagos_bancos(db, decl, subidos)
            else:
                n, avisos = cargas.cargar_dam_pdf(db, decl, subidos)
        db.commit()
        msg = f"Cargadas {n} fila(s)." + ("" if not avisos else
              f" Avisos ({len(avisos)}): " + " · ".join(avisos[:4]) + (" …" if len(avisos) > 4 else ""))
        if n == 0:
            msg = "Error: " + (" · ".join(avisos[:4]) or "no se cargó nada")
    except ValueError as e:
        db.rollback()
        msg = f"Error: {e}"
    return _volver(decl_id, PESTANA_DE_CARGA[tipo], msg, mes_ver, SUB_DE_CARGA.get(tipo, ""))


@router.post("/declaraciones/{decl_id}/estado")
def cambiar_estado(decl_id: int, db: Db, usuario: User, estado: Annotated[str, Form()],
                   fecha_envio: Annotated[str, Form()] = "", nro_constancia: Annotated[str, Form()] = "",
                   observaciones: Annotated[str, Form()] = ""):
    decl = _decl(db, decl_id)
    if estado not in ESTADOS:
        return _volver(decl_id, "resumen")
    if estado == "ENVIADO" and any(h["nivel"] == "error" for h in svc.validar(decl)):
        return _volver(decl_id, "resumen", "Error: corrija los errores de validación antes de marcar como enviado")
    decl.estado = estado
    decl.fecha_envio = svc._fecha(fecha_envio) if (fecha_envio and estado == "ENVIADO") else None
    decl.nro_constancia = nro_constancia.strip() or None
    decl.observaciones = observaciones.strip() or None
    db.commit()
    return _volver(decl_id, "resumen", "Estado actualizado")


@router.post("/declaraciones/{decl_id}/eliminar")
def eliminar(decl_id: int, db: Db, usuario: User):
    db.delete(_decl(db, decl_id))
    db.commit()
    return RedirectResponse("/", status_code=303)


def _csv_resp(contenido: str, nombre: str) -> Response:
    return Response(contenido.encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/declaraciones/{decl_id}/archivos.zip")
def archivos_zip(decl_id: int, db: Db, usuario: User):
    d = _decl(db, decl_id)
    return Response(arch.zip_archivos(d), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="PDB_{d.empresa.ruc}_{d.periodo}.zip"'})


@router.get("/declaraciones/{decl_id}/archivos/{nombre}")
def archivo_sunat(decl_id: int, nombre: str, db: Db, usuario: User):
    d = _decl(db, decl_id)
    lineas = arch.archivos(d).get(nombre)
    if lineas is None:
        raise HTTPException(404, "Archivo sin datos")
    return Response(arch.contenido(lineas), media_type="text/plain; charset=windows-1252",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/declaraciones/{decl_id}/exportar/duas.csv")
def exportar_duas(decl_id: int, db: Db, usuario: User):
    d = _decl(db, decl_id)
    return _csv_resp(svc.duas_a_csv(d), f"PDB_DUAS_{d.empresa.ruc}_{d.periodo}.csv")


@router.get("/declaraciones/{decl_id}/exportar/{hoja}.csv")
def exportar_hoja(decl_id: int, hoja: str, db: Db, usuario: User):
    d = _decl(db, decl_id)
    hoja = hoja.upper()
    if hoja not in BLOQUES_TABULARES:
        raise HTTPException(404, "Hoja desconocida")
    return _csv_resp(svc.hoja_a_csv(d, hoja), f"PDB_{hoja}_{d.empresa.ruc}_{d.periodo}.csv")
