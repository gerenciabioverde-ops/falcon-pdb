"""Lógica de negocio de Falcon PDB: importación, validación y exportación.

Formato de intercambio con Falcon Contable: CSV con ';' y dos bloques
(DUAS bloque D + CDP bloque E), fechas dd/mm/yyyy.
"""
from __future__ import annotations
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.hojas import COLUMNAS_TODAS
from app.models import Cdp, Declaracion, Dua, HojaFila

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
    for f in [f for f in decl.filas if f.hoja == hoja]:
        decl.filas.remove(f)
    db.flush()
    for i, celdas in enumerate(filas[1:]):
        decl.filas.append(HojaFila(hoja=hoja, orden=i, celdas=celdas))
    return len(filas) - 1


# ------------------------------------------------------------------ grillas (pestañas)

def _txt_fecha(d) -> str:
    return _ddmmyyyy(d)


def filas_bloque(decl: Declaracion, bloque: str) -> list[list[str]]:
    """Filas de un bloque como listas de texto, para mostrar/editar en la grilla."""
    if bloque == "DUAS_D":
        return [[d.codigo_aduana, d.anio_dua, d.correlativo, _txt_fecha(d.fecha_embarque),
                 _txt_fecha(d.fecha_regularizacion), f"{d.fob_usd:.2f}"] for d in decl.duas]
    if bloque == "DUAS_E":
        return [[c.periodo_cdp, c.tipo_comprobante, c.serie, c.numero, c.tipo_exportacion, c.dua_key,
                 f"{c.fob_facturado:.2f}", f"{c.fob_propietario:.2f}", f"{c.pct_participacion:.2f}",
                 c.ruc_operador] for c in decl.cdps]
    return [f.celdas for f in decl.filas if f.hoja == bloque]


def guardar_grilla(db: Session, decl: Declaracion, bloque: str, filas: list[list[str]]) -> int:
    """Reemplaza el contenido de un bloque con las filas de la grilla (filas vacías se omiten).
    Lanza ValueError con el N° de fila si un dato no es válido."""
    ncols = len(COLUMNAS_TODAS[bloque])
    filas = [([str(c).strip() for c in f] + [""] * ncols)[:ncols] for f in filas if any(str(c).strip() for c in f)]
    if bloque == "DUAS_D":
        nuevas = []
        for n, f in enumerate(filas, start=1):
            try:
                nuevas.append(Dua(codigo_aduana=f[0], anio_dua=f[1], correlativo=f[2],
                                  fecha_embarque=_fecha(f[3]), fecha_regularizacion=_fecha(f[4]),
                                  fob_usd=_dec(f[5])))
            except ValueError as e:
                raise ValueError(f"Fila {n}: {e}") from None
        decl.duas.clear()
        db.flush()
        decl.duas.extend(nuevas)
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


# ------------------------------------------------------------------ validación

def validar(decl: Declaracion) -> list[dict]:
    """Devuelve hallazgos [{nivel: 'error'|'aviso', texto}] antes de enviar el PDB."""
    h: list[dict] = []

    def err(t): h.append({"nivel": "error", "texto": t})
    def avi(t): h.append({"nivel": "aviso", "texto": t})

    if not decl.duas:
        err("No hay DUAs cargadas (bloque D).")
    if not decl.cdps:
        err("No hay facturas de exportación cargadas (bloque E).")

    claves = {d.clave: d for d in decl.duas}
    if len(claves) != len(decl.duas):
        err("Hay DUAs repetidas (misma aduana, año y correlativo).")

    fob_cdp: dict[str, Decimal] = {}
    vistos = set()
    for c in decl.cdps:
        id_cdp = f"{c.serie}-{c.numero}"
        if (c.tipo_comprobante, c.serie, c.numero) in vistos:
            err(f"Factura {id_cdp} duplicada.")
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
        if d.clave not in fob_cdp:
            err(f"DUA {d.clave} sin facturas en el bloque E.")
        elif abs(fob_cdp[d.clave] - (d.fob_usd or 0)) > TOLERANCIA_FOB:
            err(f"DUA {d.clave}: FOB {d.fob_usd} ≠ suma de facturas {fob_cdp[d.clave]}.")
        if not d.fecha_embarque:
            err(f"DUA {d.clave}: falta fecha de embarque.")
        if not d.fecha_regularizacion:
            avi(f"DUA {d.clave}: sin fecha de regularización.")
        elif d.fecha_embarque and d.fecha_regularizacion < d.fecha_embarque:
            err(f"DUA {d.clave}: regularización anterior al embarque.")

    ventas = {(f.celdas[3].strip().upper(), f.celdas[4].strip().lstrip("0")): f.celdas
              for f in decl.filas if f.hoja == "VENTAS" and len(f.celdas) > 4}
    en_dam = {(c.serie.strip().upper(), c.numero.strip().lstrip("0")) for c in decl.cdps}
    if ventas:
        for serie, num in sorted(en_dam - set(ventas)):
            avi(f"Factura {serie}-{num} de la DAM no figura en la hoja VENTAS.")
        for (serie, num), celdas in sorted(ventas.items()):
            if celdas[0] == "2" and (serie, num) not in en_dam and decl.cdps:
                avi(f"Exportación {serie}-{num} de VENTAS no figura en las DUAS (bloque E).")

    for f in decl.filas:
        if f.hoja == "COMPRAS" and len(f.celdas) > 23 and f.celdas[20].strip() == "1":
            if not f.celdas[21].strip() or not f.celdas[22].strip():
                avi(f"Compra {f.celdas[3]}-{f.celdas[4]} con detracción: falta el código de la tasa (A22) o la constancia (A23).")

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
    for f in decl.filas:
        if f.hoja == hoja:
            w.writerow(f.celdas)
    return buf.getvalue()
