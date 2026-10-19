"""Formulario 1662: carga de constancias (PDF SUNAT) y paso del IGV pagado a COMPRAS (A19)."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lectores.form1662 import parsear_form1662
from app.models import Declaracion, Empresa, Form1662


def _clave(serie, numero) -> tuple[str, str]:
    n = str(numero or "").strip().upper()
    return str(serie or "").strip().upper(), n.lstrip("0") or n


def _dec(x) -> Decimal:
    try:
        return Decimal(str(x or "0").replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def cargar_constancias(db: Session, empresa: Empresa, archivos: list[tuple[str, bytes]]) -> tuple[int, list[str]]:
    n, avisos = 0, []
    for nombre, contenido in archivos:
        try:
            d = parsear_form1662(contenido)
        except ValueError as e:
            avisos.append(f"{nombre}: {e}")
            continue
        fila = db.scalar(select(Form1662).where(Form1662.empresa_id == empresa.id, Form1662.nro_orden == d["nro_orden"]))
        if fila is None:
            fila = Form1662(empresa_id=empresa.id, nro_orden=d["nro_orden"])
            db.add(fila)
        for k, v in d.items():
            setattr(fila, k, v)
        db.flush()
        n += 1
    return n, avisos


def aplicar_a_compras(db: Session, empresa: Empresa) -> int:
    """Pone el IGV pagado (A19) en la compra del mismo comprobante, en cualquier período. Devuelve filas cambiadas."""
    pagos: dict[tuple, Decimal] = {}
    for f in db.scalars(select(Form1662).where(Form1662.empresa_id == empresa.id)):
        k = _clave(f.serie, f.numero)
        pagos[k] = pagos.get(k, Decimal("0")) + (f.importe or 0)
    if not pagos:
        return 0
    n = 0
    for decl in db.scalars(select(Declaracion).where(Declaracion.empresa_id == empresa.id)):
        for f in decl.filas:
            if f.hoja != "COMPRAS" or len(f.celdas) < 20:
                continue
            igv = pagos.get(_clave(f.celdas[3], f.celdas[4]))
            if igv is None or _dec(f.celdas[18]) == igv:
                continue
            nuevo = list(f.celdas)
            nuevo[18] = f"{igv:.2f}"
            f.celdas = nuevo
            n += 1
    return n


def enlazadas(db: Session, empresa: Empresa) -> set[tuple[str, str]]:
    return {_clave(f.celdas[3], f.celdas[4]) for d in db.scalars(select(Declaracion).where(Declaracion.empresa_id == empresa.id))
            for f in d.filas if f.hoja == "COMPRAS" and len(f.celdas) > 4}
