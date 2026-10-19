"""Lector de la «Constancia de depósito» de detracciones (HTML que descarga SUNAT)."""
from __future__ import annotations
import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

_FILA = re.compile(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.S | re.I)


def _limpio(t: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", t))).strip()


def parsear_constancia(contenido: bytes) -> dict:
    """Devuelve los datos de la constancia o levanta ValueError si el archivo no lo es."""
    try:
        texto = contenido.decode("utf-8")
    except UnicodeDecodeError:
        texto = contenido.decode("cp1252", errors="replace")
    campos = {_limpio(a).lower().rstrip(":"): _limpio(b) for a, b in _FILA.findall(texto)}

    def buscar(*claves: str) -> str:
        for k, v in campos.items():
            if all(c in k for c in claves):
                return v
        return ""

    constancia = buscar("número de constancia") or buscar("numero de constancia")
    if not constancia:
        raise ValueError("no es una constancia de depósito de detracción")
    comp = buscar("número de comprobante")
    partes = comp.split()
    serie, numero = (partes[0], partes[-1]) if len(partes) >= 2 else ("", comp)
    try:
        monto = Decimal(re.sub(r"[^0-9.]", "", buscar("monto del dep")) or "0")
    except InvalidOperation:
        monto = Decimal("0")
    try:
        fecha = datetime.strptime(buscar("fecha y hora")[:10], "%d/%m/%Y").date()
    except ValueError:
        fecha = None
    return {
        "constancia": constancia,
        "ruc_proveedor": buscar("ruc del proveedor"),
        "proveedor": buscar("raz", "proveedor"),
        "tipo_comprobante": buscar("tipo de comprobante")[:2],
        "serie": serie, "numero": numero,
        "bien_servicio": buscar("bien")[:3],
        "monto": monto, "fecha_pago": fecha,
        "periodo_tributario": buscar("periodo tributario"),
        "nro_operacion": buscar("número de operaci") or buscar("numero de operaci"),
    }
