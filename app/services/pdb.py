"""Lógica de negocio de Falcon PDB: importación, validación y exportación.

Formato de intercambio con Falcon Contable: CSV con ';' y dos bloques
(DUAS bloque D + CDP bloque E), fechas dd/mm/yyyy.
"""
from __future__ import annotations
import csv
import re
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import json
from pathlib import Path

from sqlalchemy.orm import Session, object_session

from app.hojas import COLUMNAS_TODAS
from app.models import Ajuste, Cdp, Declaracion, Dua, HojaFila, TipoCambioDia

PDB_DUA_HEADERS = [
    "D1 Cod.Aduana", "D2 Año DUA", "D3 N° Correlativo",
    "D4 Fecha Embarque", "D5 Fecha Regularización", "D6 Valor FOB US$",
]
PDB_CDP_HEADERS = [
    "E1 Periodo CDP", "E2 Tipo Comp.", "E3 Serie", "E4 Número",
    "E5 Tipo Export.", "DUA", "E7 FOB Facturado US$",
    "E8 FOB Propietario US$", "E9 % Particip.", "E10 RUC Operador",
]
TOLERANCIA_FOB = Decimal("0.05")


def _dec(txt) -> Decimal:
    txt = (str(txt) if txt is not None else "").strip().replace(",", "")
    if not txt:
        return Decimal("0")
    try:
        return Decimal(txt)
    except InvalidOperation:
        raise ValueError(f"Importe inválido: {txt!r}")


# Códigos de aduana válidos (ayuda de D1 del libro de SUNAT).
_AYUDA_D1 = json.loads((Path(__file__).resolve().parent.parent / "ayuda_sunat.json").read_text(encoding="utf-8"))["D1"]["texto"]
ADUANAS = set(re.findall(r"^(\d{3})\s", _AYUDA_D1, re.M))


def _fecha(txt) -> date | None:
    txt = (txt or "").strip()
    if not txt:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha inválida: {txt!r} (use dd/mm/aaaa)")


def _money(x) -> str:
    """Importe para mostrar al usuario: 12,345.60 (los archivos de SUNAT siguen sin comas)."""
    return f"{Decimal(x or 0):,.2f}"


def _ddmmyyyy(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _leer_csv(contenido: bytes) -> list[list[str]]:
    texto = contenido.decode("utf-8-sig", errors="replace")
    delim = ";" if texto.count(";") >= texto.count(",") else ","
    return [[c.strip() for c in fila] for fila in csv.reader(io.StringIO(texto), delimiter=delim)]


# ------------------------------------------------------------------ importación

def importar_duas_csv(db: Session, decl: Declaracion, contenido: bytes) -> tuple[int, int]:
    """Reemplaza DUAs y CDPs de la declaración con los del CSV (formato Falcon Contable).
    Devuelve (n_duas, n_cdps). Lanza ValueError con el N° de fila si algo no cuadra."""
    filas = _leer_csv(contenido)
    duas: list[Dua] = []
    cdps: list[Cdp] = []
    bloque = None
    for n, fila in enumerate(filas, start=1):
        if not any(fila):
            continue
        primera = fila[0].upper()
        if primera.startswith("DUAS"):
            bloque = "D_enc"
            continue
        if primera.startswith("CDP"):
            bloque = "E_enc"
            continue
        if bloque == "D_enc":
            bloque = "D"
            continue          # fila de encabezados
        if bloque == "E_enc":
            bloque = "E"
            continue
        try:
            if bloque == "D":
                if len(fila) < 6:
                    raise ValueError("Se esperaban 6 columnas en el bloque DUA")
                duas.append(Dua(
                    codigo_aduana=fila[0], anio_dua=fila[1], correlativo=fila[2],
                    fecha_embarque=_fecha(fila[3]), fecha_regularizacion=_fecha(fila[4]),
                    fob_usd=_dec(fila[5]),
                ))
            elif bloque == "E":
                if len(fila) < 10:
                    raise ValueError("Se esperaban 10 columnas en el bloque CDP")
                cdps.append(Cdp(
                    periodo_cdp=fila[0], tipo_comprobante=fila[1], serie=fila[2], numero=fila[3],
                    tipo_exportacion=fila[4], dua_key=fila[5], fob_facturado=_dec(fila[6]),
                    fob_propietario=_dec(fila[7]), pct_participacion=_dec(fila[8]) or Decimal("100"),
                    ruc_operador=fila[9],
                ))
            else:
                raise ValueError("Fila fuera de un bloque «DUAS» o «CDP»")
        except ValueError as e:
            raise ValueError(f"Fila {n}: {e}") from None
    if not duas and not cdps:
        raise ValueError("El archivo no tiene bloques DUAS/CDP con datos.")
    decl.duas.clear()
    decl.cdps.clear()
    db.flush()
    decl.duas.extend(duas)
    decl.cdps.extend(cdps)
    return len(duas), len(cdps)


def importar_hoja_csv(db: Session, decl: Declaracion, hoja: str, contenido: bytes) -> int:
    """Reemplaza las filas de una hoja tabular (primera fila = encabezados, se omite)."""
    filas = [f for f in _leer_csv(contenido) if any(f)]
    if len(filas) < 2:
        raise ValueError("El archivo debe tener encabezados y al menos una fila de datos.")
    if hoja == "TC":
        return registrar_tc_historial(db, _parsear_tc(filas[1:]))     # agrega/actualiza, no borra
    for f in [f for f in decl.filas if f.hoja == hoja]:
        decl.filas.remove(f)
    db.flush()
    for i, celdas in enumerate(filas[1:]):
        decl.filas.append(HojaFila(hoja=hoja, orden=i, celdas=celdas))
    return len(filas) - 1


# ------------------------------------------------------------------ grillas (pestañas)

def _txt_fecha(d) -> str:
    return _ddmmyyyy(d)


def _tc_a_filas(registros) -> list[list[str]]:
    return [[_ddmmyyyy(r.fecha), f"{r.compra:.3f}", f"{r.venta:.3f}"] for r in registros]


def _parsear_tc(filas: list[list[str]]) -> list[tuple]:
    """[fecha, compra, venta] en texto → [(date, Decimal, Decimal)]; ValueError con N° de fila."""
    salida = []
    for n, f in enumerate(filas, start=1):
        f = (list(f) + ["", "", ""])[:3]
        try:
            fecha = _fecha(f[0])
            if not fecha:
                raise ValueError("falta la fecha")
            compra, venta = _dec(f[1]), _dec(f[2])
            if compra <= 0 or venta <= 0:
                raise ValueError("el tipo de cambio debe ser mayor que cero")
        except ValueError as e:
            raise ValueError(f"Fila {n}: {e}") from None
        salida.append((fecha, compra, venta))
    return salida


def registrar_tc_historial(db: Session, registros: list[tuple]) -> int:
    """Agrega o actualiza fechas en el historial común de tipo de cambio."""
    for fecha, compra, venta in registros:
        fila = db.get(TipoCambioDia, fecha)
        if fila:
            fila.compra, fila.venta = compra, venta
        else:
            db.add(TipoCambioDia(fecha=fecha, compra=compra, venta=venta))
    db.flush()
    return len(registros)


def purgar_tc_futuros(db: Session) -> int:
    """Borra del historial los días posteriores a hoy: SUNAT aún no los publica, así que son datos
    de otro mes/año cargados por error. Devuelve cuántos borró."""
    n = db.query(TipoCambioDia).filter(TipoCambioDia.fecha > date.today()).delete(synchronize_session=False)
    db.flush()
    return n


def normalizar_ventas_sin_documento(db: Session) -> int:
    """Ventas con tipo de documento «-»: deja en blanco su N° de documento (regla de SUNAT).
    También completa C31 (tipo de exportación = 1) en las ventas externas que no lo tenían."""
    n = 0
    for f in db.query(HojaFila).filter(HojaFila.hoja == "VENTAS").all():
        c = list(f.celdas) + [""] * max(0, 31 - len(f.celdas))
        cambio = len(c) != len(f.celdas)
        if str(c[6]).strip() == "-" and str(c[7]).strip():
            c[7], cambio = "", True
        if str(c[0]).strip().lstrip("0") == "2" and not str(c[30]).strip():
            c[30], cambio = "1", True
        if cambio:
            f.celdas = c
            n += 1
    if n:
        db.flush()
    return n


def sembrar_historial(db: Session) -> None:
    """Al arrancar: copia al historial los TC que ya estaban en declaraciones (solo fechas faltantes)
    y quita los días futuros."""
    purgar_tc_futuros(db)
    normalizar_ventas_sin_documento(db)
    existentes = {r[0] for r in db.query(TipoCambioDia.fecha).all()}
    nuevos = []
    for f in db.query(HojaFila).filter(HojaFila.hoja == "TC").all():
        try:
            (fecha, c, v), = _parsear_tc([f.celdas])
        except ValueError:
            continue
        if fecha not in existentes and fecha <= date.today():
            existentes.add(fecha)
            nuevos.append((fecha, c, v))
    if nuevos:
        registrar_tc_historial(db, nuevos)
    db.commit()


def _tc_del_mes(db: Session, anio: int, mes: int) -> list[TipoCambioDia]:
    ini = date(anio, mes, 1)
    fin = date(anio + (mes == 12), mes % 12 + 1, 1)
    return (db.query(TipoCambioDia).filter(TipoCambioDia.fecha >= ini, TipoCambioDia.fecha < fin)
            .order_by(TipoCambioDia.fecha).all())


def meses_tc(db: Session) -> list[tuple[int, int, int]]:
    """[(año, mes, n° de días)] de los meses con tipo de cambio guardado, del más reciente al más antiguo."""
    cuenta: dict[tuple[int, int], int] = {}
    for (f,) in db.query(TipoCambioDia.fecha).all():
        cuenta[(f.year, f.month)] = cuenta.get((f.year, f.month), 0) + 1
    return [(a, m, n) for (a, m), n in sorted(cuenta.items(), reverse=True)]


def filas_bloque(decl: Declaracion, bloque: str, mes: tuple[int, int] | None = None) -> list[list[str]]:
    """Filas de un bloque como listas de texto, para mostrar/editar en la grilla.
    El tipo de cambio (TC) vive en el historial interno y se muestra por mes (por defecto el del período)."""
    if bloque == "TC":
        anio, m = mes or (decl.anio, decl.mes)
        return _tc_a_filas(_tc_del_mes(object_session(decl), anio, m))
    if bloque == "DUAS_D":
        facturas: dict[str, list[str]] = {}
        for c in decl.cdps:
            sin = not c.serie.strip() or not c.numero.strip()
            facturas.setdefault(c.dua_key, []).append(MARCA_SIN_FACTURA if sin else f"{c.serie}-{c.numero}")
        return [[d.codigo_aduana, d.anio_dua, d.correlativo, _txt_fecha(d.fecha_embarque),
                 _txt_fecha(d.fecha_regularizacion), f"{d.fob_usd:.2f}",
                 f"{d.codigo_aduana}-{d.anio_dua}-40-{d.correlativo}",       # N° de DUA (solo seguimiento)
                 ", ".join(facturas.get(d.clave, []))]                       # facturas (solo seguimiento)
                for d in decl.duas]
    if bloque == "DUAS_E":
        return [[c.periodo_cdp, c.tipo_comprobante, c.serie, c.numero, c.tipo_exportacion, c.dua_key,
                 f"{c.fob_facturado:.2f}", f"{c.fob_propietario:.2f}", f"{c.pct_participacion:.2f}",
                 c.ruc_operador] for c in decl.cdps]
    if bloque == "VENTAS":                               # C32 «ESTADO»: vacío se muestra como VÁLIDO
        filas = [(list(f.celdas) + [""] * 32)[:32] for f in decl.filas if f.hoja == bloque]
        for c in filas:
            c[31] = "ANULADO" if c[31].strip().upper() == "ANULADO" else "VÁLIDO"
        return filas
    return [f.celdas for f in decl.filas if f.hoja == bloque]


def anulado_en_cero(celdas: list[str]) -> list[str]:
    """Comprobante ANULADO: C17-C20 en 0.00 y los importes de referencia de notas (C29-C30) en 0.00 si los tenían."""
    c = list(celdas)
    for i in (16, 17, 18, 19, 28, 29):
        c[i] = "0.00" if i < 20 or str(c[i]).strip() else ""
    return c


def es_anulado(celdas) -> bool:
    return len(celdas) > 31 and str(celdas[31]).strip().upper() == "ANULADO"


MARCA_SIN_FACTURA = "(sin factura)"
_FACT = re.compile(r"^([A-Za-z0-9]{1,10})\s*-\s*0*(\d+)$")


def _parsear_facturas(texto: str, n_fila: int) -> list[tuple[str, str]]:
    """'FFF1-651, FFF1-652' → [('FFF1','651'), ('FFF1','652')]. ValueError si algún elemento no es SERIE-NÚMERO."""
    salida: list[tuple[str, str]] = []
    texto = (texto or "").replace(MARCA_SIN_FACTURA, ",")      # «(sin factura)FFF1-639» → «, FFF1-639»
    for trozo in re.split(r"[;,\n]+", texto):
        trozo = trozo.strip()
        if not trozo or trozo.startswith("(") or not trozo.strip("-–— "):
            continue                                   # vacío, «(sin factura)» o un guion suelto
        m = _FACT.match(trozo)
        if not m:
            raise ValueError(f"Fila {n_fila}: factura «{trozo}» no válida; use SERIE-NÚMERO (ej.: FFF1-651), separadas por coma.")
        par = (m.group(1).upper(), m.group(2))
        if par not in salida:
            salida.append(par)
    return salida


def _enlazar_facturas(db: Session, decl: Declaracion, dua: Dua, deseadas: list[tuple[str, str]]) -> None:
    """Deja en el bloque E las facturas indicadas para esa DUA: conserva las que ya estaban (con sus importes),
    quita las que ya no figuran y agrega las nuevas. Las filas SIN serie/número (factura que no se pudo leer
    de la DAM) se llenan primero con las nuevas, conservando su FOB; las que sobren quedan para completarse."""
    clave = dua.clave
    propias = [c for c in decl.cdps if c.dua_key == clave]
    vacias = [c for c in propias if not c.serie.strip() or not c.numero.strip()]
    por_par = {(c.serie.strip().upper(), c.numero.strip().lstrip("0")): c
               for c in propias if c.serie.strip() and c.numero.strip()}
    for par, c in list(por_par.items()):
        if par not in deseadas:
            decl.cdps.remove(c)
    nuevas = [par for par in deseadas if par not in por_par]
    for serie, numero in list(nuevas):
        if vacias:                                   # completar una fila vacía (conserva su FOB)
            c = vacias.pop(0)
            c.serie, c.numero = serie, numero
            nuevas.remove((serie, numero))
    if not nuevas:
        return
    fob_asignado = sum((c.fob_facturado or Decimal("0")) for c in propias if c in decl.cdps)
    pendiente = (dua.fob_usd or Decimal("0")) - fob_asignado
    fob = pendiente if (len(nuevas) == 1 and pendiente > 0) else Decimal("0")
    periodo = dua.fecha_embarque.strftime("%Y%m") if dua.fecha_embarque else decl.periodo
    for serie, numero in nuevas:
        decl.cdps.append(Cdp(periodo_cdp=periodo, tipo_comprobante="01", serie=serie, numero=numero,
                             tipo_exportacion="1", dua_key=clave, fob_facturado=fob, fob_propietario=fob,
                             pct_participacion=Decimal("100"), ruc_operador=""))


def guardar_grilla(db: Session, decl: Declaracion, bloque: str, filas: list[list[str]],
                   mes: tuple[int, int] | None = None) -> int:
    """Reemplaza el contenido de un bloque con las filas de la grilla (filas vacías se omiten).
    Lanza ValueError con el N° de fila si un dato no es válido."""
    ncols = len(COLUMNAS_TODAS[bloque])
    filas = [([str(c).strip() for c in f] + [""] * ncols)[:ncols] for f in filas if any(str(c).strip() for c in f)]
    for j, (_c, _t, fmt) in enumerate(COLUMNAS_TODAS[bloque]):
        if fmt.startswith("0."):                     # importes: sin separador de miles
            for f in filas:
                f[j] = f[j].replace(",", "")
    if bloque == "VENTAS":
        for f in filas:
            if f[6] == "-":
                f[7] = ""                        # sin documento → N° de documento en blanco
            f[31] = "ANULADO" if f[31].strip().upper() in ("ANULADO", "ANULADA") else "VÁLIDO"
            if f[31] == "ANULADO":
                f[:] = anulado_en_cero(f)            # anulado → importes en cero también en la hoja
    if bloque == "TC":
        registros = _parsear_tc(filas)
        anio, m = mes or (decl.anio, decl.mes)
        for r in _tc_del_mes(db, anio, m):          # lo que se quitó de la grilla se borra del mes
            db.delete(r)
        db.flush()
        registrar_tc_historial(db, registros)
        return len(registros)
    if bloque == "DUAS_D":
        nuevas = []
        for n, f in enumerate(filas, start=1):
            try:
                nuevas.append(Dua(codigo_aduana=f[0], anio_dua=f[1], correlativo=f[2],
                                  fecha_embarque=_fecha(f[3]), fecha_regularizacion=_fecha(f[4]),
                                  fob_usd=_dec(f[5])))
            except ValueError as e:
                raise ValueError(f"Fila {n}: {e}") from None
        deseadas = [(d, _parsear_facturas(f[7], n)) for n, (d, f) in enumerate(zip(nuevas, filas), start=1)]
        decl.duas.clear()
        db.flush()
        decl.duas.extend(nuevas)
        for dua, lista in deseadas:
            _enlazar_facturas(db, decl, dua, lista)
    elif bloque == "DUAS_E":
        nuevas = []
        for n, f in enumerate(filas, start=1):
            try:
                nuevas.append(Cdp(periodo_cdp=f[0], tipo_comprobante=f[1], serie=f[2], numero=f[3],
                                  tipo_exportacion=f[4], dua_key=f[5], fob_facturado=_dec(f[6]),
                                  fob_propietario=_dec(f[7]), pct_participacion=_dec(f[8]) or Decimal("100"),
                                  ruc_operador=f[9]))
            except ValueError as e:
                raise ValueError(f"Fila {n}: {e}") from None
        decl.cdps.clear()
        db.flush()
        decl.cdps.extend(nuevas)
    else:
        for f in [f for f in decl.filas if f.hoja == bloque]:
            decl.filas.remove(f)
        db.flush()
        for i, celdas in enumerate(filas):
            decl.filas.append(HojaFila(hoja=bloque, orden=i, celdas=celdas))
    return len(filas)


def _fecha_periodo(txt) -> str:
    """'28/09/2026' → '202609' (vacío si no es una fecha)."""
    try:
        d = _fecha(str(txt))
    except ValueError:
        return ""
    return f"{d.year}{d.month:02d}" if d else ""


def _safe_fecha(txt):
    try:
        return _fecha(txt)
    except ValueError:
        return None


# ------------------------------------------------------------------ validación

def validar(decl: Declaracion) -> list[dict]:
    """Devuelve hallazgos [{nivel: 'error'|'aviso', texto}] antes de enviar el PDB."""
    h: list[dict] = []

    def err(t, periodo=None): h.append({"nivel": "error", "texto": t, "periodo": periodo})
    def avi(t, periodo=None): h.append({"nivel": "aviso", "texto": t, "periodo": periodo})

    if not decl.duas:
        err("No hay DUAs cargadas (bloque D).")
    if not decl.cdps:
        err("No hay facturas de exportación cargadas (bloque E).")

    claves = {d.clave: d for d in decl.duas}
    if len(claves) != len(decl.duas):
        err("Hay DUAs repetidas (misma aduana, año y correlativo).")

    fob_cdp: dict[str, Decimal] = {}
    vistos = set()
    sin_numero = [c for c in decl.cdps if not c.serie.strip() or not c.numero.strip()]
    if sin_numero:
        duas_afectadas = sorted({c.dua_key or "(sin DUA)" for c in sin_numero})
        err(f"Bloque E: {len(sin_numero)} fila(s) sin serie o sin número de factura (DUA: {', '.join(duas_afectadas[:4])}"
            f"{'…' if len(duas_afectadas) > 4 else ''}). Complételas o quítelas.")
    for c in decl.cdps:
        id_cdp = f"{c.serie}-{c.numero}"
        if not c.serie.strip() or not c.numero.strip():
            continue                                             # ya avisado arriba
        if (c.tipo_comprobante, c.serie, c.numero) in vistos:
            err(f"Factura {id_cdp} duplicada en el bloque E (DUA {c.dua_key}).", c.periodo_cdp or None)
        vistos.add((c.tipo_comprobante, c.serie, c.numero))
        if c.dua_key not in claves:
            err(f"Factura {id_cdp}: la DUA «{c.dua_key}» no existe en el bloque D.")
        else:
            fob_cdp[c.dua_key] = fob_cdp.get(c.dua_key, Decimal("0")) + (c.fob_facturado or 0)
        if (c.fob_propietario or 0) > (c.fob_facturado or 0) + TOLERANCIA_FOB:
            err(f"Factura {id_cdp}: FOB propietario mayor que FOB facturado.")
        if len(c.periodo_cdp or "") != 6:
            avi(f"Factura {id_cdp}: período CDP «{c.periodo_cdp}» no tiene formato AAAAMM.")

    for d in decl.duas:
        if d.fecha_embarque and (d.fecha_embarque.year, d.fecha_embarque.month) != (decl.anio, decl.mes):
            avi(f"DUA {d.codigo_aduana}-{d.anio_dua}-{d.correlativo}: embarcada el {_ddmmyyyy(d.fecha_embarque)}, fuera del período "
                f"{decl.periodo}. No se incluye en el .dua (ni sus facturas en el .du1): va en el PDB de su mes de embarque.")
            continue
        if d.codigo_aduana not in ADUANAS:
            err(f"DUA {d.codigo_aduana}-{d.anio_dua}-{d.correlativo}: el código de aduana «{d.codigo_aduana}» no está en la lista de SUNAT (ej.: 046 Paita, 118 Marítima del Callao).")
        if d.clave not in fob_cdp:
            err(f"DUA {d.clave} sin facturas en el bloque E.")
        elif abs(fob_cdp[d.clave] - (d.fob_usd or 0)) > TOLERANCIA_FOB:
            err(f"DUA {d.clave}: FOB US$ {_money(d.fob_usd)} ≠ suma de facturas US$ {_money(fob_cdp[d.clave])}.")
        if not d.fecha_embarque:
            err(f"DUA {d.clave}: falta fecha de embarque.")
        elif (d.fecha_embarque.year, d.fecha_embarque.month) != (decl.anio, decl.mes):
            err(f"DUA {d.codigo_aduana}-{d.anio_dua}-{d.correlativo}: embarcada el {_ddmmyyyy(d.fecha_embarque)}, "
                f"fuera del período {decl.periodo} (SUNAT exige que el mes de embarque sea el del período).")
        if not d.fecha_regularizacion:
            avi(f"DUA {d.clave}: sin fecha de regularización.")
        elif d.fecha_embarque and d.fecha_regularizacion < d.fecha_embarque:
            err(f"DUA {d.clave}: regularización anterior al embarque.")

    clave = lambda serie, num: (str(serie).strip().upper(), str(num).strip().lstrip("0"))  # noqa: E731
    ventas = {clave(f.celdas[3], f.celdas[4]): f.celdas for f in decl.filas
              if f.hoja == "VENTAS" and len(f.celdas) > 4 and not es_anulado(f.celdas)}
    en_dam = {clave(c.serie, c.numero): c for c in decl.cdps if c.serie.strip() and c.numero.strip()}
    # La factura puede emitirse en un mes y embarcarse en otro: se busca también en los demás períodos.
    otras_ventas, otras_dam = set(), set()
    for d in decl.empresa.declaraciones:
        if d.id == decl.id:
            continue
        otras_ventas |= {clave(f.celdas[3], f.celdas[4]) for f in d.filas
                         if f.hoja == "VENTAS" and len(f.celdas) > 4 and not es_anulado(f.celdas)}
        otras_dam |= {clave(c.serie, c.numero) for c in d.cdps}
    # SUNAT: la factura de exportación debe emitirse antes del embarque (el aplicativo valida esa fecha).
    emision = dict(ventas)
    for d in decl.empresa.declaraciones:
        if d.id != decl.id:
            emision.update({clave(f.celdas[3], f.celdas[4]): f.celdas for f in d.filas
                            if f.hoja == "VENTAS" and len(f.celdas) > 4 and not es_anulado(f.celdas)})
    for k, c in sorted(en_dam.items(), key=lambda kv: (kv[1].periodo_cdp or "", kv[0])):
        dua, venta = claves.get(c.dua_key), emision.get(k)
        fe = _safe_fecha(venta[2]) if venta else None
        if dua and dua.fecha_embarque and fe and fe > dua.fecha_embarque:
            avi(f"Factura {k[0]}-{k[1]} emitida el {_ddmmyyyy(fe)}, después del embarque de la DUA {c.dua_key} "
                f"({_ddmmyyyy(dua.fecha_embarque)}): SUNAT exige emitirla antes del embarque; el aplicativo puede rechazarla.",
                c.periodo_cdp or None)
    if ventas or otras_ventas:
        for k, c in sorted(en_dam.items(), key=lambda kv: (kv[1].periodo_cdp or "", kv[0])):
            if k not in ventas and k not in otras_ventas:
                avi(f"Factura {k[0]}-{k[1]} de la DAM (DUA {c.dua_key}) no figura en VENTAS de ningún período.",
                    c.periodo_cdp or None)
    if decl.cdps or otras_dam:
        for k, celdas in sorted(ventas.items(), key=lambda kv: (_fecha_periodo(kv[1][2]), kv[0])):
            if celdas[0].strip().lstrip("0") == "2" and k not in en_dam and k not in otras_dam:
                avi(f"Exportación {k[0]}-{k[1]} (emitida el {celdas[2]}) sin DAM cargada en ningún período.",
                    _fecha_periodo(celdas[2]) or None)

    for f in decl.filas:
        if f.hoja == "COMPRAS" and len(f.celdas) > 23 and f.celdas[20].strip() == "1":
            if not f.celdas[21].strip() or not f.celdas[22].strip():
                avi(f"Compra {f.celdas[3]}-{f.celdas[4]} con detracción: falta el código de la tasa (A22) o la constancia (A23).")

    # Un comprobante de compra se registra en UN solo período (puede ser posterior al de su fecha de emisión).
    def _id_compra(c): return (str(c[7]).strip(), str(c[1]).strip().lstrip("0"), *clave(c[3], c[4]))
    ya: dict[tuple, str] = {}
    for d in sorted(decl.empresa.declaraciones, key=lambda x: (x.anio, x.mes)):
        for f in d.filas:
            if f.hoja != "COMPRAS" or len(f.celdas) < 8:
                continue
            k = _id_compra(f.celdas)
            if k in ya and (d.id == decl.id or ya[k] == decl.periodo):
                otro = d.periodo if ya[k] == decl.periodo else ya[k]
                err(f"Compra {f.celdas[3]}-{f.celdas[4]} (RUC {k[0]}) repetida: ya está registrada en el período {otro}. "
                    "Un comprobante se registra en un solo período.", decl.periodo)
            ya.setdefault(k, d.periodo)

    notas = {"COMPRAS": ("7", "8", "87", "88", "97", "98"), "VENTAS": ("7", "8")}
    for f in decl.filas:
        if f.hoja in notas and len(f.celdas) > 27 and f.celdas[1].strip().lstrip("0") in notas[f.hoja]:
            fref, fnota = _safe_fecha(f.celdas[27]), _safe_fecha(f.celdas[2])
            if fref and fnota and fref > fnota:
                avi(f"{f.celdas[3]}-{f.celdas[4]}: la fecha del documento de referencia es posterior a la de la nota.")
            if not all(x.strip() for x in f.celdas[24:28]):
                avi(f"{'Compra' if f.hoja == 'COMPRAS' else 'Venta'} {f.celdas[3]}-{f.celdas[4]} (nota): "
                    "complete el documento de referencia (tipo, serie, número y fecha).")

    hojas = {f.hoja for f in decl.filas}
    for hoja in ("VENTAS", "COMPRAS"):
        if hoja not in hojas:
            avi(f"La hoja {hoja} está vacía.")
    return h


# ------------------------------------------------------------------ exportación

def duas_a_csv(decl: Declaracion) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["DUAS — Bloque D (una fila por DUA)"])
    w.writerow(PDB_DUA_HEADERS)
    for d in decl.duas:
        w.writerow([d.codigo_aduana, d.anio_dua, d.correlativo, _ddmmyyyy(d.fecha_embarque),
                    _ddmmyyyy(d.fecha_regularizacion), f"{d.fob_usd:.2f}"])
    w.writerow([])
    w.writerow(["CDP — Bloque E (una fila por factura de exportación)"])
    w.writerow(PDB_CDP_HEADERS)
    for c in decl.cdps:
        w.writerow([c.periodo_cdp, c.tipo_comprobante, c.serie, c.numero, c.tipo_exportacion,
                    c.dua_key, f"{c.fob_facturado:.2f}", f"{c.fob_propietario:.2f}",
                    f"{c.pct_participacion:.0f}", c.ruc_operador])
    return buf.getvalue()


def hoja_a_csv(decl: Declaracion, hoja: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    for celdas in filas_bloque(decl, hoja):
        w.writerow(celdas)
    return buf.getvalue()


def obtener_ajuste(db: Session, clave: str, defecto: str = "") -> str:
    a = db.get(Ajuste, clave)
    return a.valor if a else defecto


def guardar_ajuste(db: Session, clave: str, valor: str) -> None:
    a = db.get(Ajuste, clave)
    if a:
        a.valor = valor
    else:
        db.add(Ajuste(clave=clave, valor=valor))
    db.flush()


def resumen_declaracion(decl: Declaracion) -> dict:
    """Cifras para la tarjeta del período en la pantalla de inicio."""
    cuenta = lambda h: sum(1 for f in decl.filas if f.hoja == h and any(f.celdas))  # noqa: E731
    hallazgos = validar(decl)
    return {
        "compras": cuenta("COMPRAS"), "ventas": cuenta("VENTAS"), "duas": len(decl.duas),
        "errores": sum(h["nivel"] == "error" for h in hallazgos),
        "avisos": sum(h["nivel"] == "aviso" for h in hallazgos),
    }


_NOTAS_CREDITO = {"7", "87", "97"}


def reporte(decl: Declaracion) -> dict:
    """Cifras de lo que se presenta a SUNAT en este PDB (para la pestaña REPORTE)."""
    cero = Decimal("0")

    def importe(c, i):
        return _dec(c[i]) if len(c) > i else cero

    def sumar(filas, es_nota):
        t = {"base": cero, "igv": cero, "otros": cero}
        for c in filas:
            s = -1 if es_nota(c) else 1
            t["base"] += s * importe(c, 16)
            t["igv"] += s * importe(c, 18)
            t["otros"] += s * (importe(c, 17) + importe(c, 19))
        t["total"] = t["base"] + t["igv"] + t["otros"]
        return t

    compras = [f.celdas for f in decl.filas if f.hoja == "COMPRAS" and any(f.celdas)]
    nota_c = lambda c: str(c[1]).strip().lstrip("0") in _NOTAS_CREDITO  # noqa: E731
    con_igv = [c for c in compras if importe(c, 18) > 0]
    pagos = [f.celdas for f in decl.filas if f.hoja == "COMPRAS_PAGO" and any(f.celdas)]
    ventas_todas = [f.celdas for f in decl.filas if f.hoja == "VENTAS" and any(f.celdas)]
    anuladas = [c for c in ventas_todas if es_anulado(c)]
    validas = [c for c in ventas_todas if not es_anulado(c)]
    externas = [c for c in validas if str(c[0]).strip().lstrip("0") == "2"]
    nota_v = lambda c: str(c[1]).strip().lstrip("0") in {"7"}  # noqa: E731
    fuera = {d.clave for d in decl.duas if d.fecha_embarque and (d.fecha_embarque.year, d.fecha_embarque.month) != (decl.anio, decl.mes)}
    duas = [d for d in decl.duas if d.clave not in fuera]
    cdps = [c for c in decl.cdps if c.dua_key not in fuera]
    otras = {t: sum(1 for f in decl.filas if f.hoja == h and any(f.celdas))
             for h, t in (("SWAP", "SWAP (tipo 06)"), ("SERVICIOS", "Servicios (tipo 07)"), ("HOSPEDAJE", "Hospedaje (tipo 08)"),
                          ("SIN_DETALLE", "Sin detalle (tipos 09-11)"), ("EXP_DEF", "Exp. temporal a definitiva (tipo 12)"),
                          ("TURISTAS", "Turistas (tipo 13)"))}
    def fecha_orden(c):
        return _safe_fecha(c[2]) or date.min

    def nombre(c):
        return f"{str(c[3]).strip()}-{str(c[4]).strip().lstrip('0') or str(c[4]).strip()}"

    lista_compras = [{"fecha": c[2], "tipo": c[1], "doc": nombre(c), "ruc": c[7], "nombre": c[8],
                      "moneda": "US$" if str(c[13]).strip() == "2" else "S/",
                      "base": importe(c, 16), "igv": importe(c, 18), "otros": importe(c, 17) + importe(c, 19),
                      "total": importe(c, 16) + importe(c, 17) + importe(c, 18) + importe(c, 19),
                      "detraccion": c[22] if len(c) > 22 else "", "nota": nota_c(c)}
                     for c in sorted(compras, key=fecha_orden) if len(c) > 19]
    lista_ventas = [{"fecha": c[2], "tipo": c[1], "doc": nombre(c), "ruc": c[7], "nombre": c[8],
                     "moneda": "US$" if str(c[13]).strip() == "2" else "S/", "externa": str(c[0]).strip().lstrip("0") == "2",
                     "base": importe(c, 16), "igv": importe(c, 18), "total": importe(c, 16) + importe(c, 17) + importe(c, 18) + importe(c, 19),
                     "anulado": es_anulado(c), "nota": nota_v(c)}
                    for c in sorted(ventas_todas, key=fecha_orden) if len(c) > 19]
    lista_pagos = [{"fecha": c[10], "tipo": c[1], "doc": f"{str(c[2]).strip()}-{str(c[3]).strip().lstrip('0') or str(c[3]).strip()}",
                    "ruc": c[6], "medio": c[7], "banco": c[8], "operacion": c[9], "monto": importe(c, 11)}
                   for c in sorted(pagos, key=lambda c: (_safe_fecha(c[10]) or date.min)) if len(c) > 11]
    return {
        "lista_compras": lista_compras, "lista_ventas": lista_ventas, "lista_pagos": lista_pagos,
        "compras": {"n": len(compras), "con_igv": len(con_igv), "sin_igv": len(compras) - len(con_igv),
                    "con_detraccion": sum(1 for c in compras if len(c) > 22 and str(c[20]).strip() == "1"),
                    **sumar(compras, nota_c)},
        "pagos": {"n": len(pagos), "monto": sum((importe(c, 11) for c in pagos), cero)},
        "ventas": {"n": len(ventas_todas), "validas": len(validas), "anuladas": len(anuladas), "externas": len(externas),
                   **sumar(validas, nota_v)},
        "duas": {"n": len(duas), "excluidas": len(fuera), "fob": sum((d.fob_usd or cero for d in duas), cero),
                 "sin_regularizar": sum(1 for d in duas if not d.fecha_regularizacion)},
        "cdps": {"n": len(cdps), "fob": sum((c.fob_facturado or cero for c in cdps), cero)},
        "tc": len(_tc_del_mes(object_session(decl), decl.anio, decl.mes)),
        "otras": {k: v for k, v in otras.items() if v},
    }


def dam_de_ventas(db: Session, decl: Declaracion) -> list[dict]:
    """Para cada fila de VENTAS, la DAM que la sustenta (si la hay), buscando en todos los períodos de
    la empresa: la factura puede emitirse en un mes y embarcarse/regularizarse en otro. Solo para ver."""
    por_factura: dict[tuple, dict] = {}
    for d in decl.empresa.declaraciones:
        duas = {u.clave: u for u in d.duas}
        for c in d.cdps:
            u = duas.get(c.dua_key)
            if u:
                por_factura[(c.serie.strip().upper(), c.numero.strip().lstrip("0"))] = {
                    "dua": f"{u.codigo_aduana}-{u.anio_dua}-40-{u.correlativo}",
                    "regularizada": u.fecha_regularizacion, "embarque": u.fecha_embarque, "periodo": d.periodo}
    salida = []
    for celdas in filas_bloque(decl, "VENTAS"):
        c = (list(celdas) + [""] * 31)[:31]
        if es_anulado(celdas):
            salida.append({"estado": "anu"})
            continue
        if c[0].strip().lstrip("0") != "2":
            salida.append({"estado": "na"})                          # venta interna: no lleva DAM
            continue
        info = por_factura.get((c[3].strip().upper(), c[4].strip().lstrip("0")))
        if not info:
            salida.append({"estado": "sin"})
        else:
            salida.append({"estado": "ok" if info["regularizada"] else "pend", **info})
    return salida
