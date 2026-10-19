"""Detracciones: carga de constancias (HTML SUNAT) y enlace a los comprobantes de COMPRAS."""
from __future__ import annotations
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.lectores.detraccion import parsear_constancia
from app.models import Declaracion, Detraccion, Empresa

_AYUDA = json.loads((Path(__file__).resolve().parent.parent / "ayuda_sunat.json").read_text(encoding="utf-8"))
# Códigos de la tasa (A22): «03501   1.5% Bienes Exonerados Del Igv» → ("03501", 1.5)
CODIGOS = [(m.group(1), Decimal(m.group(2))) for m in
           (re.match(r"^(\d{5})\s+([\d.]+)\s*%", ln) for ln in _AYUDA["A22"]["texto"].splitlines()) if m]


def _sin_ceros(s) -> str:
    s = str(s or "").strip()
    return s.lstrip("0") or s


def _dec(x) -> Decimal:
    try:
        return Decimal(str(x or "0").replace(",", ""))
    except InvalidOperation:
        return Decimal("0")


def codigo_tasa(bien: str, monto: Decimal, total: Decimal) -> str:
    """Código de 5 dígitos para A22: los del bien o servicio y, si hay varios, el de tasa más cercana."""
    cands = [(c, t) for c, t in CODIGOS if c[:3] == bien.zfill(3)]
    if not cands:
        return ""
    if len(cands) == 1 or not total:
        return cands[0][0]
    tasa = monto / total * 100
    return min(cands, key=lambda ct: abs(ct[1] - tasa))[0]


def cargar_constancias(db: Session, empresa: Empresa, archivos: list[tuple[str, bytes]]) -> tuple[int, list[str]]:
    n, avisos = 0, []
    for nombre, contenido in archivos:
        try:
            d = parsear_constancia(contenido)
        except ValueError as e:
            avisos.append(f"{nombre}: {e}")
            continue
        if d["ruc_proveedor"] and d["ruc_proveedor"] == empresa.ruc:
            avisos.append(f"{nombre}: el proveedor es la propia empresa (¿es una constancia de ventas?)")
        fila = db.scalar(select(Detraccion).where(Detraccion.empresa_id == empresa.id,
                                                  Detraccion.constancia == d["constancia"]))
        if fila is None:
            fila = Detraccion(empresa_id=empresa.id, constancia=d["constancia"])
            db.add(fila)
        for k, v in d.items():
            setattr(fila, k, v)
        db.flush()                       # la misma constancia repetida en el envío se actualiza, no se duplica
        n += 1
    return n, avisos


def aplicar_a_compras(db: Session, empresa: Empresa) -> int:
    """Rellena A21-A23 en las compras de TODOS los períodos de la empresa. Devuelve filas actualizadas."""
    dets = list(db.scalars(select(Detraccion).where(Detraccion.empresa_id == empresa.id)))
    por_clave = {(d.ruc_proveedor, _sin_ceros(d.tipo_comprobante), d.serie.upper(), _sin_ceros(d.numero)): d
                 for d in dets}
    if not por_clave:
        return 0
    n = 0
    for decl in db.scalars(select(Declaracion).where(Declaracion.empresa_id == empresa.id)):
        for f in decl.filas:
            if f.hoja != "COMPRAS" or len(f.celdas) < 23:
                continue
            c = f.celdas
            d = por_clave.get((str(c[7]).strip(), _sin_ceros(c[1]), str(c[3]).strip().upper(), _sin_ceros(c[4])))
            if not d:
                continue
            total = _dec(c[16]) + _dec(c[18]) + _dec(c[19])
            nuevo = list(c)
            nuevo[20], nuevo[21], nuevo[22] = "1", codigo_tasa(d.bien_servicio, d.monto, total), d.constancia
            if nuevo != c:
                f.celdas = nuevo
                n += 1
    return n


def enlazadas(db: Session, empresa: Empresa) -> dict[str, list[str]]:
    """N° de constancia → períodos de declaración donde figura en COMPRAS (A23)."""
    r: dict[str, list[str]] = {}
    for d in db.scalars(select(Declaracion).where(Declaracion.empresa_id == empresa.id)):
        for f in d.filas:
            if f.hoja == "COMPRAS" and len(f.celdas) > 22 and f.celdas[22]:
                r.setdefault(str(f.celdas[22]), []).append(d.periodo)
    return r
