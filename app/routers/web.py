"""Rutas HTTP de Falcon PDB: login, empresas, declaraciones por período."""
import json
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
from app.hojas import (ARCHIVO_BLOQUE, BLOQUES_TABULARES, COLUMNAS_TODAS, PESTANA, PESTANAS,
                       TITULOS_BLOQUE)
from app.models import ESTADOS, Declaracion, Empresa, Usuario
from app.services import archivos_sunat as arch
from app.services import cargas
from app.services import pdb as svc

AYUDA = json.loads((Path(__file__).resolve().parent.parent / "ayuda_sunat.json").read_text(encoding="utf-8"))
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
MESES = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Setiembre", "Octubre", "Noviembre", "Diciembre"]
Db = Annotated[Session, Depends(get_db)]
User = Annotated[Usuario, Depends(get_current_user)]


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
def inicio(request: Request, db: Db, usuario: User):
    empresas = db.query(Empresa).order_by(Empresa.razon_social).all()
    hoy = date.today()
    return _render(request, "inicio.html", usuario=usuario, empresas=empresas,
                   anio=hoy.year, mes=hoy.month)


@router.post("/empresas")
def crear_empresa(db: Db, usuario: User, ruc: Annotated[str, Form()], razon_social: Annotated[str, Form()]):
    ruc = ruc.strip()
    if len(ruc) == 11 and ruc.isdigit() and not db.query(Empresa).filter_by(ruc=ruc).first():
        db.add(Empresa(ruc=ruc, razon_social=razon_social.strip()))
        db.commit()
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


def _volver(decl_id: int, hoja: str, msg: str = "") -> RedirectResponse:
    return RedirectResponse(f"/declaraciones/{decl_id}?hoja={hoja}" + (f"&msg={msg}" if msg else ""),
                            status_code=303)


@router.get("/declaraciones/{decl_id}", response_class=HTMLResponse)
def ver_declaracion(request: Request, decl_id: int, db: Db, usuario: User, hoja: str = "resumen", msg: str = ""):
    decl = _decl(db, decl_id)
    pestana = PESTANA.get(hoja) or PESTANA["resumen"]
    hallazgos = svc.validar(decl)
    cuentas = {p["clave"]: sum(len(svc.filas_bloque(decl, b)) for b, _ in p["bloques"]) for p in PESTANAS}
    bloques = [{"clave": b, "titulo": t, "columnas": COLUMNAS_TODAS[b], "filas": svc.filas_bloque(decl, b)}
               for b, t in pestana["bloques"]]
    return _render(request, "declaracion.html", usuario=usuario, decl=decl, msg=msg, pestana=pestana,
                   pestanas=PESTANAS, cuentas=cuentas, bloques=bloques, hallazgos=hallazgos,
                   n_errores=sum(h["nivel"] == "error" for h in hallazgos), archivos=arch.archivos(decl),
                   archivo_bloque=ARCHIVO_BLOQUE,
                   ayuda={c[0]: AYUDA[c[0]] for b in bloques for c in b["columnas"] if c[0] in AYUDA})


@router.post("/declaraciones/{decl_id}/bloque/{bloque}")
def guardar_bloque(decl_id: int, bloque: str, db: Db, usuario: User, hoja: Annotated[str, Form()],
                   datos: Annotated[str, Form()]):
    decl = _decl(db, decl_id)
    if bloque not in COLUMNAS_TODAS:
        raise HTTPException(404, "Bloque desconocido")
    try:
        filas = json.loads(datos)
        n = svc.guardar_grilla(db, decl, bloque, filas)
        db.commit()
        msg = f"Guardado: {n} fila(s)."
    except (ValueError, TypeError) as e:
        db.rollback()
        msg = f"Error: {e}"
    return _volver(decl_id, hoja, msg)


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


PESTANA_DE_CARGA = {"tc": "tc", "ventas": "ventas", "compras": "compras", "pagos": "compras",
                    "pagos_bancos": "compras", "duas": "duas"}


@router.post("/declaraciones/{decl_id}/cargar/{tipo}")
async def cargar(decl_id: int, tipo: str, db: Db, usuario: User,
                 archivos: Annotated[list[UploadFile], File()] = []):
    """Llena una hoja desde los documentos de origen (PDF de TC, XML, PDF de DAM) o,
    para `pagos`, desde las compras ya cargadas."""
    decl = _decl(db, decl_id)
    if tipo not in PESTANA_DE_CARGA:
        raise HTTPException(404, "Tipo de carga desconocido")
    try:
        if tipo == "pagos":
            n, avisos = cargas.generar_pagos_desde_compras(db, decl)
        else:
            subidos = [(a.filename or "archivo", await a.read()) for a in archivos if a.filename]
            if not subidos:
                raise ValueError("Seleccione al menos un archivo.")
            if tipo == "tc":
                n, avisos = cargas.cargar_tc_pdf(db, decl, subidos[0][1])
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
    return _volver(decl_id, PESTANA_DE_CARGA[tipo], msg)


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
