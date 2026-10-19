"""
Parser de PDF de DAM (Declaración Aduanera de Mercancías) de SUNAT.

Lee el PDF "(A) REGISTRO DE ADUANAS (REGULARIZADA)" y devuelve
(cabecera: dict, series: list[dict]) listos para precargar el form
`/exportaciones/dam/nueva`. El usuario revisa y confirma antes de guardar.

Soporta tanto DAMs de 1 serie como de N series. La cabecera vive en la
primera página; cada serie se delimita por el marcador "MERCANCIAS NNNN/MMMM"
(sección 7.1 Serie/Total). Los contenedores (sección 9) y las referencias
("Ref:" V-/P-/C-) figuran una sola vez a nivel DAM y se reparten por orden a
cada serie.

Usa pdfplumber (no pypdf) por su mejor preservación del layout en estos
formularios de columnas.
"""
from __future__ import annotations

import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

import pdfplumber


# ---------------------------------------------------------------- utilidades

def _texto_paginas(origen) -> list[str]:
    """Devuelve el texto de cada página. `origen` = ruta str o bytes."""
    if isinstance(origen, (bytes, bytearray)):
        fh = io.BytesIO(bytes(origen))
    else:
        fh = origen  # ruta o file-like
    paginas: list[str] = []
    with pdfplumber.open(fh) as pdf:
        for page in pdf.pages:
            paginas.append(page.extract_text() or "")
    return paginas


def _dec(txt: str | None):
    """'24,053.333' -> Decimal('24053.333'). None/'' -> None."""
    if not txt:
        return None
    t = txt.strip().replace(",", "")
    try:
        return Decimal(t)
    except (InvalidOperation, ValueError):
        return None


def _fecha(txt: str | None):
    """'09/01/2026' -> date(2026,1,9). None si no parsea."""
    if not txt:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", txt)
    if not m:
        return None
    d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return date(a, mes, d)
    except ValueError:
        return None


def _val_despues(lineas: list[str], etiqueta: str) -> str | None:
    """Línea de valores que sigue a la primera línea que contiene `etiqueta`."""
    for i, ln in enumerate(lineas):
        if etiqueta in ln:
            for sig in lineas[i + 1:]:
                if sig.strip():
                    return sig.strip()
            return None
    return None


def _buscar(patron: str, texto: str, grupo: int = 1, flags=0):
    m = re.search(patron, texto, flags)
    return m.group(grupo).strip() if m else None


# ------------------------------------------------------------------- cabecera

def _parsear_cabecera(p1: str) -> dict:
    lineas = [l for l in p1.splitlines()]
    cab: dict = {}

    # Aduana + booking:  "PAITA 046 Nro. Booking: 263799042 [Ref: ...]"
    m = re.search(r"^([A-ZÁ-Ú][A-ZÁ-Ú ]+?)\s+(\d{3})\s+Nro\.\s*Booking:\s*(\S+)",
                  p1, re.MULTILINE)
    if m:
        cab["aduana_nombre"] = m.group(1).strip()
        cab["aduana_codigo"] = m.group(2)
        cab["no_booking"] = m.group(3).strip()

    # Línea de datos:  "2026/000026 40 01 0-1 2026-000388 05/01/2026 046-2026-40-000388"
    m = re.search(r"^(\d{4}/\d{6})\s+(\d+)\s+(\d+)\s+\S+\s+\S+\s+\d{2}/\d{2}/\d{4}\s+"
                  r"(\d{3}-\d{4}-\d{2}-\d{6})", p1, re.MULTILINE)
    if m:
        cab["no_orden"] = m.group(1)
        cab["destinacion"] = m.group(2)
        cab["modalidad"] = m.group(3)
        cab["no_declaracion"] = m.group(4)

    cab["fecha_numeracion"] = _fecha(_buscar(r"Fch\.Numeración:\s*(\d{2}/\d{2}/\d{4})", p1))
    # Fecha de regularización: en las DUAs de exportación "(REGULARIZADA)" la
    # sección 12.2 viene en blanco y la regularización coincide con la numeración
    # del documento regularizado (por eso numeración cae tras el embarque). Se usa
    # esa fecha; el usuario puede corregirla a mano si tuviera una distinta.
    cab["fecha_regularizacion"] = cab["fecha_numeracion"]
    cab["canal"] = _buscar(r"CANAL\s+([A-ZÁ-Ú]+)", p1)

    # 3.1 Empresa Transporte - RUC
    cab["ruc_transporte"] = _buscar(r"Empresa Transporte\s*-\s*RUC:\s*(\d+)", p1)
    # Nombre transporte + manifiesto + vía (línea tras "3 TRANSPORTE")
    t = _val_despues(lineas, "3 TRANSPORTE")
    if t:
        mt = re.match(r"^(.+?)\s+\d{3,5}\s+(\d{3}-\d{4}-\d{3})\s+([A-ZÁ-Ú]+)", t)
        if mt:
            cab["empresa_transporte"] = mt.group(1).strip()
            cab["no_manifiesto"] = mt.group(2)
            cab["via_transporte"] = mt.group(3)

    # 3.4 fecha de embarque
    cab["fecha_embarque"] = _fecha(_val_despues(lineas, "3.4 Fch.Term.Desc/Embar"))

    # 4 ALMACEN
    alm = _val_despues(lineas, "4 ALMACEN")
    if alm:
        cab["almacen_nombre"] = re.sub(r"\s+\d{3,5}\s*$", "", alm).strip()

    # 5 TRANSACCION:  "[BANCO ... 011] Pago al Contado 1"
    tr = _val_despues(lineas, "5 TRANSACCION")
    if tr:
        mb = re.match(r"^(.+?)\s+\d{3}\s+(Pago al \w+)", tr)
        if mb:
            cab["banco"] = mb.group(1).strip()
            cab["modalidad_pago"] = mb.group(2).strip()
        else:
            mp = re.search(r"(Pago al \w+)", tr)
            if mp:
                cab["modalidad_pago"] = mp.group(1).strip()

    # 6 VALOR ADUANA — 3 cifras: FOB, Seguro/Deducibles, Valor Aduana
    va = _val_despues(lineas, "6 VALOR ADUANA")
    if va:
        nums = re.findall(r"[\d,]+\.\d+", va)
        if len(nums) >= 2:
            cab["fob_total_usd"] = _dec(nums[0])
            cab["valor_aduana_usd"] = _dec(nums[-1])
            cab["seguro_deducibles_usd"] = _dec(nums[1] if len(nums) == 3 else
                                                 nums[2] if len(nums) >= 5 else None)

    # 8.1 Agente de aduanas + código (línea con "AGENTES DE ADUANAS")
    for ln in lineas:
        if "AGENTES DE ADUANA" in ln.upper():
            ma = re.match(r"^(.+?)\s+(\d{3,5})\s*$", ln.strip())
            if ma:
                cab["agente_aduana_nombre"] = ma.group(1).strip()
                cab["agente_aduana_codigo"] = ma.group(2)
            break
    cab["agente_aduana_fecha"] = _fecha(_val_despues(lineas, "8.3"))

    return cab


# ------------------------------------------------------------ refs y contenedores

def _parsear_refs(p1: str) -> dict:
    """
    Refs del bloque 'Ref:'. Venta (V) y pedido (P) son comunes a la DAM;
    los contratos (C) varían por serie. El layout parte el 'Ref:' en varias
    líneas (la línea de booking se intercala), así que se recolectan por
    separado en vez de emparejar V-P-C por proximidad.
    """
    venta = _buscar(r"\b(V\d{4,})\b", p1)
    pedido = _buscar(r"\b(P\d{4,})\b", p1)
    contratos: list[str] = []
    for m in re.finditer(r"\b(C\d{4,})\b", p1):
        c = m.group(1)
        if c not in contratos:
            contratos.append(c)
    return {"venta": venta, "pedido": pedido, "contratos": contratos}


def _parsear_contenedores(p1: str) -> list[dict]:
    """Sección 9: [{no, precinto, tipo}] — 'TCNU2487531 004VA727241 1X40'."""
    conts = []
    for m in re.finditer(r"^([A-Z]{4}\d{6,7})\s+(\S+)\s+(\d+\s*X\s*\d+)\s*$",
                         p1, re.MULTILINE):
        conts.append({
            "no": m.group(1).strip(),
            "precinto": m.group(2).strip(),
            "tipo": m.group(3).replace(" ", ""),
        })
    return conts


# --------------------------------------------------------------------- series

_FACTURA_DESC = re.compile(r"FACTURA\s*(?:N[°º.]*\s*)?([A-Za-z]{1,4}\d{0,4})\s*[-–]\s*(\d{1,12})", re.IGNORECASE)
_NOMBRE_FACT = re.compile(r"^([A-Za-z]{1,4}\d{1,4})\s*-\s*(\d{1,12})((?:_\d{1,12})*)(?=_|\.|$)")


def facturas_del_nombre(nombre: str | None) -> list[tuple[str, str]]:
    """Facturas que indica el nombre del archivo: 'FFF1-000628_046-2026-40-034819.pdf' → [('FFF1','000628')];
    'FFF1-000651_652_046-….pdf' → [('FFF1','000651'), ('FFF1','652')]."""
    if not nombre:
        return []
    base = re.split(r"[\\/]", nombre)[-1].strip()
    m = _NOMBRE_FACT.match(base)
    if not m:
        return []
    serie = m.group(1).upper()
    numeros = [m.group(2)] + [n for n in m.group(3).split("_") if n]
    return [(serie, n) for n in numeros]


_FACTURA = re.compile(r"\b([A-Za-z]{1,4}\d{0,4})\s*-\s*(\d{1,12})\b\s+(\d{2}/\d{2}/\d{4})")


def _buscar_factura(bloque: str):
    """(serie-número, fecha) de la factura de la serie. Busca en la zona «No Factura / Fecha» (7.36) cualquier
    serie (FFF1, F001, E001…); si no la encuentra ahí, prueba en todo el bloque."""
    i = bloque.find("No Factura")
    for zona in ((bloque[i:i + 500],) if i >= 0 else ()) + (bloque,):
        for m in _FACTURA.finditer(zona):
            return f"{m.group(1).upper()}-{m.group(2)}", m.group(3)
    return None


def _parsear_serie(bloque: str) -> dict:
    lineas = [l for l in bloque.splitlines()]
    s: dict = {}

    m = re.search(r"MERCANCIAS\s+(\d{4})/(\d{4})", bloque)
    if m:
        s["no_serie"] = int(m.group(1))
        s["total_series"] = int(m.group(2))

    # 7.12  cant.bult / peso neto / peso bruto: "24.000 BUL 24,000.000 24,053.333 ..."
    v712 = _val_despues(lineas, "7.12 Cant.Bult")
    if v712:
        nums = re.findall(r"[\d,]+\.\d+", v712)
        if len(nums) >= 3:
            s["cant_bultos"] = _dec(nums[0])
            s["peso_neto_kg"] = _dec(nums[1])
            s["peso_bruto_kg"] = _dec(nums[2])

    # 7.19  partida arancelaria + país destino (último token)
    v719 = _val_despues(lineas, "7.19 Partida Nac")
    if v719:
        mp = re.match(r"^(\d{8,12})", v719)
        if mp:
            s["partida_arancelaria"] = mp.group(1)
        toks = v719.split()
        if toks:
            s["pais_destino_codigo"] = toks[-1]

    # 7.29/7.30  FOB US$ (primera cifra de la línea de valores)
    v729 = _val_despues(lineas, "7.29 FOB Mon.Trans")
    if v729:
        mf = re.search(r"[\d,]+\.\d+", v729)
        if mf:
            s["fob_usd"] = _dec(mf.group(0))

    # Descripción 1..5 + ICO MARK + factura
    desc = []
    for ln in lineas:
        md = re.match(r"^([1-5])\.\s*(.+)$", ln.strip())
        if md and len(desc) < 5:
            desc.append(md.group(2).strip())
    if desc:
        s["descripcion"] = "\n".join(desc)
    bloque_desc = "\n".join(desc)
    s["ico_mark"] = _buscar(r"ICO MARK:\s*([\d\-]+)", bloque) or \
        _buscar(r"ICO MARK:\s*([\d\-]+)", bloque_desc)
    mfact = _buscar_factura(bloque)
    if not mfact:                                 # la DAM la menciona en la descripción: «/ FACTURA FFF1-000628/»
        md = _FACTURA_DESC.search(bloque)
        if md:
            mfact = (f"{md.group(1).upper()}-{md.group(2)}", None)
            s["factura_origen"] = "descripción de la DAM"
    if mfact:
        raw = mfact[0]                             # ej. "FFF1-00000551"
        s["no_factura"] = raw
        s["fecha_factura"] = _fecha(mfact[1]) if mfact[1] else None
        # Desagregado para el PDB y el cruce con el Registro de Ventas.
        ser, num = raw.split("-", 1)
        s["factura_serie"] = ser.strip()
        s["factura_numero"] = num.strip()
    # Comprobante de exportación: factura (tipo 01) en dólares (café).
    s.setdefault("factura_tipo", "01")
    s.setdefault("moneda", "USD")

    # Consignatario
    s["consignatario_nombre"] = _buscar(r"CONSIG:\s*(.+)", bloque)
    s["consignatario_direccion"] = _buscar(r"DIRECC:\s*(.+)", bloque)

    return s


def _bloques_series(texto_total: str) -> list[str]:
    """Parte el texto en bloques, uno por marcador 'MERCANCIAS NNNN/MMMM'."""
    indices = [m.start() for m in re.finditer(r"MERCANCIAS\s+\d{4}/\d{4}", texto_total)]
    bloques = []
    for i, ini in enumerate(indices):
        fin = indices[i + 1] if i + 1 < len(indices) else len(texto_total)
        bloques.append(texto_total[ini:fin])
    return bloques


# ----------------------------------------------------------------- API pública

def parsear_dam_pdf(origen, nombre: str | None = None) -> tuple[dict, list[dict]]:
    """
    Parsea un PDF de DAM. `origen` = ruta (str), bytes o file-like.
    Devuelve (cabecera, series). Campos ausentes vienen como None.
    """
    paginas = _texto_paginas(origen)
    if not paginas:
        return {}, []
    p1 = paginas[0]
    texto_total = "\n".join(paginas)

    cabecera = _parsear_cabecera(p1)
    refs = _parsear_refs(p1)
    contenedores = _parsear_contenedores(p1)

    series = [_parsear_serie(b) for b in _bloques_series(texto_total)]
    series = [s for s in series if s.get("no_serie")]
    series.sort(key=lambda s: s["no_serie"])

    # Series sin factura legible: se toma del nombre del archivo (convención «FFF1-000628_046-2026-40-034819.pdf»).
    vacias = [s for s in series if not (s.get("factura_serie") and s.get("factura_numero"))]
    nombres = facturas_del_nombre(nombre)
    if vacias and nombres and (len(nombres) == len(vacias) or len(nombres) == 1):
        for i, s in enumerate(vacias):
            serie, numero = nombres[i] if len(nombres) == len(vacias) else nombres[0]
            s.update({"no_factura": f"{serie}-{numero}", "factura_serie": serie, "factura_numero": numero,
                      "factura_origen": "nombre del archivo"})

    # Refs -> cada serie (venta/pedido comunes; contrato por orden de serie)
    contratos = refs.get("contratos") or []
    for i, s in enumerate(series):
        s["ref_venta"] = refs.get("venta")
        s["ref_pedido"] = refs.get("pedido")
        if i < len(contratos):
            s["ref_contrato"] = contratos[i]
        elif len(contratos) == 1:
            s["ref_contrato"] = contratos[0]

    # Contenedores: 1:1 por orden si coinciden las cantidades; si hay 1 serie,
    # todos a esa serie; en otro caso todos a la primera (el usuario reasigna).
    if series:
        if len(contenedores) == len(series):
            for s, c in zip(series, contenedores):
                s["contenedores"] = [c]
        elif len(series) == 1:
            series[0]["contenedores"] = contenedores
        else:
            series[0]["contenedores"] = contenedores
            for s in series[1:]:
                s.setdefault("contenedores", [])

    return cabecera, series
