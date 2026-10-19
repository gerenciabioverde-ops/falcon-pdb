"""Genera los archivos de texto que carga SUNAT, replicando las macros del aplicativo
PDB Exportadores (libro Excel): una línea por fila, campos separados por «|» y con «|»
final, cada columna con el formato de su fila 1 en el libro, codificación ANSI (cp1252),
saltos de línea CRLF.

Nombres (RUC + período AAAAMM, salvo el .tc que va solo con el RUC):
  C{ruc}{per}.txt  compras · F{ruc}{per}.txt  formas de pago · V{ruc}{per}.txt  ventas
  {ruc}{per}.dua   DUAS bloque D · {ruc}{per}.du1  DUAS bloque E · {ruc}.tc  tipo de cambio
"""
from __future__ import annotations
import io
import re
import zipfile
from decimal import ROUND_HALF_UP, Decimal

from app.hojas import ARCHIVO_BLOQUE, COLUMNAS
from app.models import Declaracion
from app.services.pdb import _fecha

F = "FECHA"   # dd/mm/yyyy
FMT_DUA = ["000", "0000", "000000", F, F, "0.00"]
FMT_DU1 = ["@", "00", "0000", "0000000000", "00", "0000000000000", "0.00", "0.00", "0.00", "@"]

_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def dar_formato(valor, fmt: str) -> str:
    """Equivalente a DarFormato() del libro."""
    if fmt in (F, "FECHA8") and hasattr(valor, "strftime"):
        return valor.strftime("%d/%m/%Y" if fmt == F else "%Y%m%d")
    txt = "" if valor is None else str(valor).strip()
    if fmt == "FECHA8":
        try:
            d = _fecha(txt)
        except ValueError:
            return txt
        return d.strftime("%Y%m%d") if d else ""
    if fmt == F:
        try:
            d = _fecha(txt)
        except ValueError:
            return txt
        return d.strftime("%d/%m/%Y") if d else ""
    if fmt == "0.00*":
        if _num(txt) == 0:
            return ""
        fmt = "0.00"
    elif fmt == "0**":
        if _num(txt) == 0:
            txt = "0"
        fmt = "0"
    elif fmt == "0000***":
        fmt = "0000" if len(txt) <= 4 else "0000000000" if len(txt) <= 10 else "0000000000000"
    if fmt == "@" or not _NUM.match(txt):
        return txt
    if "." in fmt:
        dec = len(fmt.split(".")[1])
        return str(Decimal(txt).quantize(Decimal(1).scaleb(-dec), rounding=ROUND_HALF_UP))
    n = int(Decimal(txt).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return str(abs(n)).zfill(len(fmt)) if n >= 0 else "-" + str(abs(n)).zfill(len(fmt))


def _num(txt: str) -> Decimal:
    m = re.match(r"^\s*-?\d*\.?\d+", txt.replace(",", ""))
    return Decimal(m.group(0)) if m else Decimal(0)


def _linea(celdas, formatos: list[str]) -> str:
    celdas = list(celdas) + [""] * (len(formatos) - len(celdas))
    return "|".join(dar_formato(c, f) for c, f in zip(celdas, formatos)) + "|"


def _filas_hoja(decl: Declaracion, hoja: str, formatos: list[str]) -> list[str]:
    return [_linea(f.celdas, formatos) for f in decl.filas if f.hoja == hoja and any(f.celdas)]


def lineas_duas(decl: Declaracion) -> list[str]:
    return [_linea([d.codigo_aduana, d.anio_dua, d.correlativo, d.fecha_embarque,
                    d.fecha_regularizacion, d.fob_usd], FMT_DUA) for d in decl.duas]


def lineas_cdps(decl: Declaracion) -> list[str]:
    return [_linea([c.periodo_cdp, c.tipo_comprobante, c.serie, c.numero, c.tipo_exportacion,
                    c.dua_key, c.fob_facturado, c.fob_propietario, c.pct_participacion,
                    c.ruc_operador], FMT_DU1) for c in decl.cdps]


def archivos(decl: Declaracion) -> dict[str, list[str]]:
    """{nombre_archivo: [líneas]} — solo los que tienen datos."""
    ruc, per = decl.empresa.ruc, decl.periodo
    todo = {f"{ruc}{per}.dua": lineas_duas(decl), f"{ruc}{per}.du1": lineas_cdps(decl)}
    for bloque, (pref, ext, con_per) in ARCHIVO_BLOQUE.items():
        nombre = f"{pref}{ruc}{per if con_per else ''}{ext}"
        todo[nombre] = _filas_hoja(decl, bloque, [c[2] for c in COLUMNAS[bloque]])
    return {n: l for n, l in todo.items() if l}


def contenido(lineas: list[str]) -> bytes:
    return ("\r\n".join(lineas) + "\r\n").encode("cp1252", errors="replace")


def zip_archivos(decl: Declaracion) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, lineas in archivos(decl).items():
            z.writestr(nombre, contenido(lineas))
    return buf.getvalue()
