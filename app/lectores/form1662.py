"""Lector de la «Constancia Formulario 1662» (PDF de SUNAT): IGV de servicios prestados por no domiciliados."""
from __future__ import annotations
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pdfplumber


def parsear_form1662(pdf: bytes) -> dict:
    try:
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            texto = "\n".join((p.extract_text() or "") for p in doc.pages)
    except Exception as e:                                        # PDF dañado
        raise ValueError(f"no se pudo leer el PDF ({e})")
    campos = {}
    for ln in texto.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            campos.setdefault(k.strip().lower(), v.strip())
    if "1662" not in campos.get("número de formulario", "") and "formulario - 1662" not in texto.lower():
        raise ValueError("no es una constancia del Formulario 1662")
    orden = campos.get("número de orden", "")
    comp = campos.get("serie y número de comprobante", "")
    serie, _, numero = (x.strip() for x in comp.partition(" - "))
    if not orden or not numero:
        raise ValueError("faltan el número de orden o el comprobante")
    try:
        m = re.search(r"\d[\d,]*(?:\.\d+)?", campos.get("importe pagado", ""))
        importe = Decimal(m.group().replace(",", "")) if m else Decimal("0")
    except InvalidOperation:
        importe = Decimal("0")
    try:
        fecha = datetime.strptime(campos.get("fecha de pago", "")[:10], "%d/%m/%Y").date()
    except ValueError:
        fecha = None
    return {"nro_orden": orden, "periodo": campos.get("periodo", ""), "serie": serie, "numero": numero,
            "importe": importe, "fecha_pago": fecha, "banco": campos.get("banco", ""),
            "nro_operacion": campos.get("número de operación", "")}
