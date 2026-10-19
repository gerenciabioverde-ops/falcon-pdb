"""Carga automática de las hojas del PDB desde los documentos de origen:

- TC        ← PDF mensual de SUNAT
- VENTAS    ← XML de comprobantes (UBL 2.1) emitidos por la empresa
- COMPRAS   ← XML de comprobantes recibidos por la empresa
- PAGOS     ← derivados de COMPRAS (replica las fórmulas del libro)
- DUAS      ← PDF de la DAM (Registro de Aduanas)

Cada carga devuelve (cantidad, avisos). Los avisos son observaciones que el usuario
debe revisar; no detienen la carga.
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from app.catalogos import BANCOS, MEDIOS_PAGO
from app.lectores.bancos import leer_libro_bancos
from app.lectores.dam_pdf import parsear_dam_pdf
from app.lectores.tc_pdf import ImportadorTCError, parsear_pdf_sunat
from app.lectores.xml_cpe import NS, parsear_xml_cpe
from app.models import Cdp, Declaracion, Dua, HojaFila, TipoCambioDia
from app.services.pdb import _dec, _fecha, registrar_tc_historial
from sqlalchemy.orm import object_session

CERO = Decimal("0")


def _f(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _num(x) -> str:
    """Importe con 2 decimales; vacío si es cero (como DarFormato con '*')."""
    x = Decimal(x or 0)
    return "" if x == 0 else f"{x.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _sin_ceros(s) -> str:
    s = (str(s) if s is not None else "").strip()
    return s.lstrip("0") or s


def _filas(decl: Declaracion, hoja: str) -> list[HojaFila]:
    return [f for f in decl.filas if f.hoja == hoja]


def _reemplazar(db: Session, decl: Declaracion, hoja: str, filas: list[list[str]]) -> None:
    for f in _filas(decl, hoja):
        decl.filas.remove(f)
    db.flush()
    for i, celdas in enumerate(filas):
        decl.filas.append(HojaFila(hoja=hoja, orden=i, celdas=celdas))


# ------------------------------------------------------------------ TC (PDF SUNAT)

def _fecha_consulta_pdf(pdf: bytes) -> date | None:
    """El PDF de SUNAT trae «Consultado el dd/mm/aaaa»: ese día es el último con datos posibles."""
    try:
        from io import BytesIO
        from pypdf import PdfReader
        texto = "\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages)
    except Exception:  # noqa: BLE001
        return None
    m = re.search(r"Consultado el\s+(\d{2}/\d{2}/\d{4})", texto)
    return _fecha(m.group(1)) if m else None


def cargar_tc_pdf(db: Session, pdf: bytes) -> tuple[int, list[str], tuple[int, int]]:
    """PDF mensual de SUNAT → historial interno de tipo de cambio. El mes y el año se leen del
    propio PDF. No se guardan días posteriores a la fecha de consulta del PDF ni a hoy: SUNAT
    aún no los publica, así que serían datos de otro mes/año. Devuelve (días, avisos, (año, mes))."""
    try:
        registros, meta = parsear_pdf_sunat(pdf)
    except ImportadorTCError as e:
        raise ValueError(str(e)) from None
    limite = min(x for x in (_fecha_consulta_pdf(pdf), date.today()) if x)
    validos = [r for r in registros if r["fecha"] <= limite]
    avisos = []
    if len(validos) < len(registros):
        avisos.append(f"Se omitieron {len(registros) - len(validos)} día(s) posteriores al {_f(limite)} "
                      "(fecha de consulta del PDF): SUNAT aún no los publica.")
    if not validos:
        raise ValueError(f"El PDF no tiene días publicados hasta el {_f(limite)}.")
    n = registrar_tc_historial(db, [(r["fecha"], r["compra"], r["venta"]) for r in validos])
    return n, avisos, (meta["anio_detectado"], meta["mes_detectado"])


def tc_venta(decl: Declaracion, fecha: date | None) -> Decimal | None:
    """TC venta vigente a `fecha`: último publicado hasta esa fecha (máx. 10 días antes) en el
    historial común; si no hay, el de la hoja TC del período."""
    if not fecha:
        return None
    db = object_session(decl)
    if db is not None:
        r = (db.query(TipoCambioDia).filter(TipoCambioDia.fecha <= fecha)
             .order_by(TipoCambioDia.fecha.desc()).first())
        if r and r.venta > 0 and (fecha - r.fecha).days <= 10:
            return r.venta
    mejor = None
    for f in _filas(decl, "TC"):
        try:
            d = _fecha(f.celdas[0])
            v = _dec(f.celdas[2])
        except (ValueError, IndexError):
            continue
        if d and d <= fecha and v > 0 and (mejor is None or d > mejor[0]):
            mejor = (d, v)
    return mejor[1] if mejor else None


# ------------------------------------------------------------------ XML (ventas / compras)

def _referencia(raw_xml: str) -> tuple[str, str, str]:
    """(tipo, serie, número) del documento que modifica una NC/ND, si el XML lo trae."""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return "", "", ""
    ref = root.find(".//cac:BillingReference/cac:InvoiceDocumentReference", NS)
    if ref is None:
        return "", "", ""
    id_ = (ref.findtext("cbc:ID", default="", namespaces=NS) or "").strip()
    tipo = (ref.findtext("cbc:DocumentTypeCode", default="", namespaces=NS) or "").strip()
    serie, _, num = id_.partition("-")
    if not tipo and serie:
        tipo = {"F": "01", "B": "03"}.get(serie[:1].upper(), "")
    return _sin_ceros(tipo), serie, _sin_ceros(num)


def _tipo_persona(td: str) -> str:
    td = (td or "").strip()
    return "2" if td == "6" else ("1" if td == "1" else "3")


def _a_soles(decl: Declaracion, monto: Decimal, moneda: str, fecha, avisos: list[str], etiqueta: str) -> Decimal:
    if moneda == "PEN" or not monto:
        return monto
    tc = tc_venta(decl, fecha)
    if tc is None:
        avisos.append(f"{etiqueta}: en {moneda} y no hay tipo de cambio para {_f(fecha)}; cargue primero la hoja TC.")
        return monto
    return monto * tc


def _fila_venta(decl: Declaracion, c: dict, avisos: list[str]) -> list[str]:
    es_export = c["op_exportacion"] > 0
    usd = c["moneda"] != "PEN"
    td = (c["tipo_doc_receptor"] or "").strip()
    etiqueta = f"{c['serie']}-{c['numero']}"
    if es_export:
        base = c["op_exportacion"]                       # exportación: moneda de origen
        igv = c["igv"]
    else:
        base = sum((_a_soles(decl, v, c["moneda"], c["fecha_emision"], avisos, etiqueta)
                    for v in (c["op_gravadas"], c["op_exoneradas"], c["op_inafectas"])), CERO)
        igv = _a_soles(decl, c["igv"], c["moneda"], c["fecha_emision"], avisos, etiqueta)
    isc = _a_soles(decl, c["isc"], c["moneda"], c["fecha_emision"], avisos, etiqueta) if not es_export else c["isc"]
    otros = c["icbper"] + (c["otros_cargos"] or CERO)
    rt, rs, rn = _referencia(c.get("raw_xml", "")) if c["tipo_comprobante"] in ("07", "08") else ("", "", "")
    doc_cli = c["ruc_receptor"]
    if td in ("", "0", "-"):                         # no domiciliado sin documento: como en el libro
        td, doc_cli = "-", (doc_cli if doc_cli.strip("0") else "00000001")
    return [
        "2" if es_export else "1",                   # C1
        _sin_ceros(c["tipo_comprobante"]) or "1",    # C2
        _f(c["fecha_emision"]),                      # C3
        c["serie"], _sin_ceros(c["numero"]),         # C4, C5
        _tipo_persona(td), td,                       # C6, C7
        doc_cli, c["razon_social_receptor"],         # C8, C9
        "", "", "", "",                              # C10-C13
        "2" if usd else "1",                         # C14
        "1" if (not es_export and igv > 0) else "2", "1",   # C15-C16: 1 gravada con IGV · 2 no gravada (incl. exportación)
        _num(base), _num(isc), _num(igv), _num(otros),   # C17-C20
        "", "", "", "",                              # C21-C24
        rt, rs, rn, "", _num(base) if rt else "",
        _num(igv) if (rt and not es_export) else "",   # C25-C30 (29-30: importe de la nota; ventas externas sin IGV)
    ]


def _fila_compra(decl: Declaracion, c: dict, avisos: list[str]) -> list[str]:
    td = (c["tipo_doc_emisor"] or "6").strip()
    etiqueta = f"{c['serie']}-{c['numero']}"
    conv = lambda v: _a_soles(decl, v, c["moneda"], c["fecha_emision"], avisos, etiqueta)  # noqa: E731
    base = conv(c["op_gravadas"]) or conv(c["op_exoneradas"] + c["op_inafectas"])
    otros = conv(c["icbper"] + (c["otros_cargos"] or CERO))
    rt, rs, rn = _referencia(c.get("raw_xml", "")) if c["tipo_comprobante"] in ("07", "08") else ("", "", "")
    return [
        "1",                                         # A1 tipo de compra
        _sin_ceros(c["tipo_comprobante"]) or "1",    # A2
        _f(c["fecha_emision"]),                      # A3
        c["serie"], _sin_ceros(c["numero"]),         # A4, A5
        _tipo_persona(td), td,                       # A6, A7
        c["ruc_emisor"], c["razon_social_emisor"],   # A8, A9
        "", "", "", "",                              # A10-A13
        "2" if c["moneda"] != "PEN" else "1",        # A14
        "1" if c["igv"] > 0 else "4", "1",           # A15-A16: 1 gravada→operaciones gravadas · 4 no gravada
        _num(base), _num(conv(c["isc"])), _num(conv(c["igv"])), _num(otros),   # A17-A20
        "1" if c["tiene_detraccion"] else "",        # A21
        "", "", "",                                  # A22-A24
        rt, rs, rn, "", _num(base) if rt else "", _num(conv(c["igv"])) if rt else "",   # A25-A30 (29-30: importe de la nota)
    ]


def _clave_fila(celdas: list[str], i_tipo: int, i_serie: int, i_num: int) -> tuple:
    return (_sin_ceros(celdas[i_tipo]), celdas[i_serie], _sin_ceros(celdas[i_num]))


def _cargar_xmls(db: Session, decl: Declaracion, archivos: list[tuple[str, bytes]], hoja: str,
                 lado: str) -> tuple[int, list[str]]:
    """lado = 'emisor' (ventas: la empresa emite) o 'receptor' (compras: la empresa recibe)."""
    avisos: list[str] = []
    nuevas: dict[tuple, list[str]] = {}
    for nombre, contenido in archivos:
        if b"ApplicationResponse" in contenido[:3000]:
            m = re.search(rb"<cbc:ReferenceID>([^<]+)<", contenido) or re.search(rb"numero ([A-Z0-9]+-\d+)", contenido)
            ref = m.group(1).decode() if m else "un comprobante"
            avisos.append(f"{nombre}: es la constancia de recepción (CDR) de {ref}, no el comprobante; "
                          f"suba el XML del comprobante (el que no empieza con «R-»).")
            continue
        try:
            c = parsear_xml_cpe(contenido)
        except ValueError as e:
            avisos.append(f"{nombre}: {e}")
            continue
        ruc_empresa = c["ruc_emisor"] if lado == "emisor" else c["ruc_receptor"]
        if ruc_empresa != decl.empresa.ruc:
            avisos.append(f"{nombre}: el {'emisor' if lado == 'emisor' else 'receptor'} es {ruc_empresa}, "
                          f"no la empresa {decl.empresa.ruc}; se omitió.")
            continue
        fe = c["fecha_emision"]
        if fe and (fe.year, fe.month) != (decl.anio, decl.mes):
            avisos.append(f"{c['serie']}-{c['numero']}: emitido en {_f(fe)}, fuera del período {decl.periodo}.")
        fila = (_fila_venta if hoja == "VENTAS" else _fila_compra)(decl, c, avisos)
        nuevas[_clave_fila(fila, 1, 3, 4)] = fila
    if not nuevas:
        if not avisos:
            avisos.append("No se cargó ningún comprobante.")
        return 0, avisos
    existentes = {_clave_fila(f.celdas, 1, 3, 4): f.celdas for f in _filas(decl, hoja) if len(f.celdas) > 4}
    existentes.update(nuevas)
    ordenadas = sorted(existentes.values(), key=lambda r: (_sin_cero_fecha(r[2]), r[3], r[4]))
    _completar_referencias(ordenadas)
    _reemplazar(db, decl, hoja, ordenadas)
    return len(nuevas), avisos


def _completar_referencias(filas: list[list[str]]) -> None:
    """En las notas de crédito/débito, si el comprobante que modifican está en la misma hoja,
    completa su fecha de emisión (el XML de la nota no la trae). La base y el IGV de la
    referencia (A29/A30) son los de la propia nota, no los del documento original."""
    por_clave = {(_sin_ceros(f[1]), f[3].upper(), _sin_ceros(f[4])): f for f in filas}
    for f in filas:
        if f[1] not in ("7", "07", "8", "08") or not f[25] or f[27]:
            continue
        orig = por_clave.get((_sin_ceros(f[24]) or "1", f[25].upper(), _sin_ceros(f[26])))
        if orig:
            f[27] = orig[2]


def _sin_cero_fecha(txt: str):
    try:
        return _fecha(txt) or date.min
    except ValueError:
        return date.min


def cargar_ventas_xml(db, decl, archivos):
    return _cargar_xmls(db, decl, archivos, "VENTAS", "emisor")


def cargar_compras_xml(db, decl, archivos):
    return _cargar_xmls(db, decl, archivos, "COMPRAS", "receptor")


# ------------------------------------------------------------------ Forma de pago (desde compras)

def generar_pagos_desde_compras(db: Session, decl: Declaracion) -> tuple[int, list[str]]:
    """Una fila de forma de pago por cada compra. Replica las fórmulas del libro:
    fecha de operación = fecha de emisión; monto S/. = SUM(base..otros) si es compra
    tipo 1 (o tipo 2 con destino 5), o (IGV/0,18 + ISC+IGV+otros) × TC si no.
    Conserva medio de pago / banco / N° de operación ya digitados."""
    avisos: list[str] = []
    previos = {(f.celdas[1], f.celdas[2], _sin_ceros(f.celdas[3])): f.celdas
               for f in _filas(decl, "COMPRAS_PAGO") if len(f.celdas) > 9}
    filas: list[list[str]] = []
    for f in _filas(decl, "COMPRAS"):
        a = (f.celdas + [""] * 30)[:30]
        if not any(a):
            continue
        base, isc, igv, otros = (_dec(a[i]) for i in (16, 17, 18, 19))
        if a[0] == "1" or (a[0] == "2" and a[15] == "5"):
            monto = base + isc + igv + otros
        else:
            tc = Decimal("1")
            if a[13] not in ("", "1"):
                tc = tc_venta(decl, _sin_cero_fecha(a[2])) or CERO
                if tc == 0:
                    avisos.append(f"{a[3]}-{a[4]}: sin tipo de cambio para {a[2]}; monto en cero.")
            monto = (igv / Decimal("0.18") + isc + igv + otros) * tc
        prev = previos.get((a[1], a[3], _sin_ceros(a[4])), [])
        prev = (prev + [""] * 12)[:12]
        filas.append([a[0], a[1], a[3], a[4], a[5], a[6], a[7],
                      prev[7], prev[8], prev[9], a[2], _num(monto)])
    if not filas:
        raise ValueError("No hay compras: cargue primero los XML de compras.")
    _reemplazar(db, decl, "COMPRAS_PAGO", filas)
    sin_medio = sum(1 for r in filas if not r[7])
    if sin_medio:
        avisos.append(f"{sin_medio} pago(s) sin medio de pago, banco ni N° de operación: complételos en la grilla.")
    return len(filas), avisos


# ------------------------------------------------------------------ Forma de pago (desde libros de bancos)

def _clave_cpe(texto: str) -> tuple[str, str]:
    """'FN01 - 01161271' → ('FN01', '1161271'); sin serie reconocible → ('', '')."""
    serie, sep, num = (texto or "").partition("-")
    if not sep:
        return "", ""
    return serie.strip().upper(), _sin_ceros(num.strip())


def _sin_tildes(t: str) -> str:
    return t.lower().translate(str.maketrans("áéíóú", "aeiou")).strip()


def cargar_pagos_bancos(db: Session, decl: Declaracion, archivos: list[tuple[str, bytes]]) -> tuple[int, list[str]]:
    """Busca en los libros de bancos el pago de cada compra y llena la forma de pago:
    medio de pago, banco, N° de operación (cheque o N° de movimiento) y fecha de operación.
    Una fila por pago (los pagos parciales generan varias). Compras sin pago quedan con los
    datos del comprobante y el medio/banco vacíos."""
    compras = [(_filas_compra(f), f) for f in _filas(decl, "COMPRAS")]
    compras = [(a, f) for a, f in compras if any(a)]
    if not compras:
        raise ValueError("No hay compras: cargue primero los XML de compras.")
    avisos: list[str] = []

    pagos: dict[tuple, list[dict]] = {}
    egresos = 0
    for nombre, contenido in archivos:
        try:
            libro = leer_libro_bancos(contenido)
        except ValueError as e:
            avisos.append(f"{nombre}: {e}")
            continue
        banco = BANCOS.get((libro["entidad"] or "").strip().upper(), "")
        if not banco:
            avisos.append(f"{nombre}: entidad «{libro['entidad']}» sin código de banco en app/catalogos.py.")
        for m in libro["movimientos"]:
            try:
                egreso = _dec(m.get("egreso"))
            except ValueError:
                continue
            serie, num = _clave_cpe(m.get("comprobante", ""))
            if egreso <= 0 or not serie:
                continue
            egresos += 1
            tipo = _sin_tildes(m.get("tipo_pago", ""))
            medio = MEDIOS_PAGO.get(tipo, "")
            if not medio:
                avisos.append(f"Tipo de pago «{m.get('tipo_pago')}» sin código de medio de pago en app/catalogos.py.")
            pagos.setdefault((serie, num), []).append({
                "doc": (m.get("ruc") or "").strip(), "medio": medio, "banco": banco,
                "operacion": (m.get("cheque") if tipo == "cheque" and m.get("cheque") else m.get("movimiento", "")),
                "fecha": _fecha(m.get("fecha", "")), "egreso": egreso, "moneda": libro["moneda"],
            })

    filas: list[list[str]] = []
    usados: set[tuple] = set()
    sin_pago = 0
    for a, _hf in compras:
        serie, num = a[3].strip().upper(), _sin_ceros(a[4])
        cand = pagos.get((serie, num), [])
        mismos = [p for p in cand if not p["doc"] or not a[7] or p["doc"] == a[7].strip()]
        if cand and not mismos:
            avisos.append(f"{a[3]}-{a[4]}: hay pagos con ese número pero a otro RUC/documento; se omitieron.")
        usados.add((serie, num))
        base = [a[0], a[1], a[3], a[4], a[5], a[6], a[7]]
        if not mismos:
            sin_pago += 1
            filas.append(base + ["", "", "", a[2], _num(_monto_compra(decl, a, [])) ])
            continue
        suma = CERO
        for p in mismos:
            monto = p["egreso"] if p["moneda"] == "PEN" else p["egreso"] * (tc_venta(decl, _sin_cero_fecha(a[2])) or CERO)
            if p["moneda"] != "PEN" and monto == 0:
                avisos.append(f"{a[3]}-{a[4]}: pago en USD sin tipo de cambio para {a[2]}; monto en cero.")
            suma += monto
            filas.append(base + [p["medio"], p["banco"], p["operacion"], _f(p["fecha"]) or a[2], _num(monto)])
        esperado = _monto_compra(decl, a, avisos)
        if esperado and abs(suma - esperado) > Decimal("1.00"):
            avisos.append(f"{a[3]}-{a[4]}: pagos S/ {suma:.2f} ≠ comprobante S/ {esperado:.2f}.")
    if sin_pago:
        avisos.append(f"{sin_pago} compra(s) sin pago en los libros de bancos (quedan con medio/banco vacíos).")
    ignorados = egresos - sum(len(v) for k, v in pagos.items() if k in usados)
    if ignorados > 0:
        avisos.append(f"{ignorados} egreso(s) del banco no corresponden a compras de esta declaración.")
    _reemplazar(db, decl, "COMPRAS_PAGO", filas)
    return len(filas), avisos


def _filas_compra(f: HojaFila) -> list[str]:
    return (f.celdas + [""] * 30)[:30]


def _monto_compra(decl: Declaracion, a: list[str], avisos: list[str]) -> Decimal:
    """Monto S/. del comprobante según la fórmula AR del libro."""
    base, isc, igv, otros = (_dec(a[i]) for i in (16, 17, 18, 19))
    if a[0] == "1" or (a[0] == "2" and a[15] == "5"):
        return base + isc + igv + otros
    tc = Decimal("1")
    if a[13] not in ("", "1"):
        tc = tc_venta(decl, _sin_cero_fecha(a[2])) or CERO
    return (igv / Decimal("0.18") + isc + igv + otros) * tc


# ------------------------------------------------------------------ DAM (PDF)

def dam_a_filas(cab: dict, series: list[dict]) -> tuple[Dua, list[Cdp]]:
    """Convierte lo leído de una DAM en la fila del bloque D y las filas del bloque E."""
    partes = (cab.get("no_declaracion") or "").split("-")
    if len(partes) < 4:
        raise ValueError("No se pudo leer el N° de declaración de la DAM.")
    aduana, anio, correlativo = partes[0], partes[1], partes[-1]
    clave = f"{aduana}{anio}{correlativo}"
    cdps = []
    for s in series:
        fe = s.get("fecha_factura") or cab.get("fecha_embarque")
        fob = s.get("fob_usd") or CERO
        cdps.append(Cdp(
            periodo_cdp=fe.strftime("%Y%m") if fe else "",
            tipo_comprobante=s.get("factura_tipo") or "01",
            serie=s.get("factura_serie") or "",
            numero=(s.get("factura_numero") or "").lstrip("0") or (s.get("factura_numero") or ""),
            tipo_exportacion="1", dua_key=clave, fob_facturado=fob, fob_propietario=fob,
            pct_participacion=Decimal("100"), ruc_operador="",
        ))
    por_factura: dict[tuple, Cdp] = {}
    for c in cdps:                                   # varias series pueden ser de la misma factura
        k = (c.tipo_comprobante, c.serie, c.numero)
        if k in por_factura:
            por_factura[k].fob_facturado += c.fob_facturado
            por_factura[k].fob_propietario += c.fob_propietario
        else:
            por_factura[k] = c
    cdps = list(por_factura.values())
    dua = Dua(codigo_aduana=aduana, anio_dua=anio, correlativo=correlativo,
              fecha_embarque=cab.get("fecha_embarque"), fecha_regularizacion=cab.get("fecha_regularizacion"),
              fob_usd=sum((c.fob_facturado for c in cdps), CERO))
    return dua, cdps


def cargar_dam_pdf(db: Session, decl: Declaracion, archivos: list[tuple[str, bytes]]) -> tuple[int, list[str]]:
    avisos: list[str] = []
    n = 0
    for nombre, contenido in archivos:
        try:
            cab, series = parsear_dam_pdf(contenido)
            if not cab or not series:
                raise ValueError("no se reconoció como PDF de DAM (Registro de Aduanas)")
            dua, cdps = dam_a_filas(cab, series)
        except Exception as e:  # noqa: BLE001 - el PDF puede fallar de muchas formas
            avisos.append(f"{nombre}: {e}")
            continue
        for d in [d for d in decl.duas if d.clave == dua.clave]:
            decl.duas.remove(d)
        for c in [c for c in decl.cdps if c.dua_key == dua.clave]:
            decl.cdps.remove(c)
        db.flush()
        decl.duas.append(dua)
        decl.cdps.extend(cdps)
        n += 1
        fr = dua.fecha_regularizacion
        if fr and (fr.year, fr.month) != (decl.anio, decl.mes):
            avisos.append(f"{nombre}: la regularización ({_f(fr)}) cae fuera del período {decl.periodo}.")
        if not cab.get("fecha_embarque"):
            avisos.append(f"{nombre}: no se leyó la fecha de embarque.")
    return n, avisos
