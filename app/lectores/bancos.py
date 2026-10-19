"""Lector del libro de bancos («DETALLE DE LOS MOVIMIENTOS», un archivo por cuenta).

El sistema exporta HTML con extensión .xls; también se acepta un .xlsx real.
Devuelve {"entidad", "cuenta", "moneda", "movimientos": [dict]} con un movimiento por
fila de detalle (ingresos y egresos).
"""
from __future__ import annotations
import io
import zipfile
from html.parser import HTMLParser

COLUMNAS = {
    "fecha operación": "fecha", "numero de movimiento": "movimiento", "tipo pago": "tipo_pago",
    "número de cheque": "cheque", "tipo de comprobante": "tipo_comprobante",
    "serie y número comprobante": "comprobante", "concepto": "concepto", "número de ruc": "ruc",
    "nombre o razón social": "nombre", "ingreso": "ingreso", "egreso": "egreso",
}


class _Tablas(HTMLParser):
    def __init__(self):
        super().__init__()
        self.filas: list[list[str]] = []
        self._fila = None
        self._celda = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._fila = []
        elif tag in ("td", "th"):
            self._celda = ""

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._celda is not None and self._fila is not None:
            self._fila.append(" ".join(self._celda.split()))
            self._celda = None
        elif tag == "tr" and self._fila is not None:
            self.filas.append(self._fila)
            self._fila = None

    def handle_data(self, data):
        if self._celda is not None:
            self._celda += data


def _filas_archivo(contenido: bytes) -> list[list[str]]:
    if contenido[:2] == b"PK" and zipfile.is_zipfile(io.BytesIO(contenido)):
        import openpyxl
        ws = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True).active
        return [["" if v is None else (v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else str(v)).strip()
                 for v in fila] for fila in ws.iter_rows(values_only=True)]
    texto = contenido.decode("utf-8-sig", errors="replace")
    if "<" not in texto[:2000]:
        raise ValueError("Formato no soportado: se esperaba el libro de bancos exportado (.xls HTML o .xlsx).")
    p = _Tablas()
    p.feed(texto)
    return p.filas


def leer_libro_bancos(contenido: bytes) -> dict:
    filas = _filas_archivo(contenido)
    meta = {}
    for f in filas[:10]:
        if len(f) >= 2 and f[0].endswith(":"):
            meta[f[0].rstrip(":").lower()] = f[1]
    i_enc = next((i for i, f in enumerate(filas) if f and f[0].lower() == "fecha operación"), None)
    if i_enc is None:
        raise ValueError("No se encontró la fila de encabezados («Fecha operación»).")
    idx = {COLUMNAS[c.lower()]: j for j, c in enumerate(filas[i_enc]) if c.lower() in COLUMNAS}
    movimientos = []
    for f in filas[i_enc + 1:]:
        if len(f) < len(filas[i_enc]) or not f[0][:4].isdigit():
            continue                                  # saldo inicial, totales, filas cortas
        m = {k: f[j] for k, j in idx.items()}
        if m.get("comprobante") or m.get("egreso"):
            movimientos.append(m)
    cuenta = meta.get("código de la cuenta", "")
    moneda = "USD" if "dolar" in cuenta.lower() or "dólar" in cuenta.lower() else "PEN"
    return {"entidad": meta.get("entidad financiera", ""), "cuenta": cuenta, "moneda": moneda,
            "movimientos": movimientos}
