"""
Parser de XML UBL 2.1 — Factura Electrónica SUNAT.

Extrae los campos del comprobante a partir del XML emitido por el proveedor.
Compatible con factura, boleta, NC, ND, recibo por honorarios.

Formato basado en estándar UBL 2.1 que SUNAT exige:
  https://www.sunat.gob.pe/legislacion/superin/2017/anexoVII-117-2017.pdf

Devuelve un dict con todos los campos extraídos. La conversión a modelo
Comprobante la hace el servicio principal.
"""
from __future__ import annotations
import re
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


# Namespaces UBL 2.1 usados por SUNAT
NS = {
    "":     "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "cbc":  "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac":  "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "ext":  "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
    "ds":   "http://www.w3.org/2000/09/xmldsig#",
    "qdt":  "urn:oasis:names:specification:ubl:schema:xsd:QualifiedDatatypes-2",
    "udt":  "urn:un:unece:uncefact:data:specification:UnqualifiedDataTypesSchemaModule:2",
    # Para Notas de Crédito y Débito el namespace raíz cambia
    "cn":   "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "dn":   "urn:oasis:names:specification:ubl:schema:xsd:DebitNote-2",
}


def _text(elem: ET.Element | None, default: str = "") -> str:
    """Obtiene el texto de un elemento, vacío si None."""
    if elem is None or elem.text is None:
        return default
    return elem.text.strip()


def _decimal(elem: ET.Element | None, default: Decimal = Decimal("0")) -> Decimal:
    """Convierte el texto de un elemento a Decimal con 2 decimales."""
    if elem is None or elem.text is None or not elem.text.strip():
        return default
    try:
        return Decimal(elem.text.strip()).quantize(Decimal("0.01"))
    except Exception:
        return default


def _decimal_raw(elem: ET.Element | None, default: Decimal = Decimal("0")) -> Decimal:
    """Convierte sin redondear (para cantidades, precios unitarios)."""
    if elem is None or elem.text is None or not elem.text.strip():
        return default
    try:
        return Decimal(elem.text.strip())
    except Exception:
        return default


def _fecha(s: str) -> date | None:
    """Parsea fecha en formato ISO (YYYY-MM-DD)."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _moneda_iso_a_codigo(iso: str) -> str:
    """Convierte código ISO (PEN, USD) — ya está en formato correcto."""
    return (iso or "PEN").strip().upper()


def _identificar_tipo_comprobante(root: ET.Element) -> str:
    """
    Determina el tipo de comprobante.
      Invoice + InvoiceTypeCode 01 → 01 Factura
      Invoice + InvoiceTypeCode 03 → 03 Boleta
      CreditNote                   → 07
      DebitNote                    → 08
    """
    tag = root.tag.split("}")[-1]  # quitar namespace

    if tag == "CreditNote":
        return "07"
    if tag == "DebitNote":
        return "08"

    # Invoice — leer InvoiceTypeCode
    code = root.find("cbc:InvoiceTypeCode", NS)
    if code is not None and code.text:
        c = code.text.strip()
        # 01=Factura, 03=Boleta, R1=Recibo honorarios
        if c == "01":
            return "01"
        if c == "03":
            return "03"
        return c

    return "01"


def _parsear_serie_numero(id_completo: str) -> tuple[str, str]:
    """
    Separa 'FA01-00017175' en ('FA01', '00017175').
    """
    if not id_completo:
        return "", ""
    s = id_completo.strip()
    if "-" in s:
        partes = s.split("-", 1)
        return partes[0], partes[1]
    # Si no hay guion, intentar adivinar (raro pero posible)
    m = re.match(r"^([A-Z]{1,4}\d*)-?(\d+)$", s)
    if m:
        return m.group(1), m.group(2)
    return s, ""


def _extraer_party(party_elem: ET.Element | None) -> dict[str, Any]:
    """
    Extrae datos de un Party (emisor o receptor):
      RUC, tipo doc, razón social.
    """
    if party_elem is None:
        return {"ruc": "", "tipo_doc": "", "razon_social": ""}

    # PartyIdentification > ID (con schemeID = tipo de doc)
    pid = party_elem.find(".//cac:PartyIdentification/cbc:ID", NS)
    ruc = _text(pid)
    tipo_doc = pid.attrib.get("schemeID", "6") if pid is not None else "6"

    # PartyLegalEntity > RegistrationName (razón social)
    rs = party_elem.find(".//cac:PartyLegalEntity/cbc:RegistrationName", NS)
    razon_social = _text(rs)

    # Si no, PartyName/Name (boleta de persona natural)
    if not razon_social:
        nm = party_elem.find(".//cac:PartyName/cbc:Name", NS)
        razon_social = _text(nm)

    return {
        "ruc": ruc,
        "tipo_doc": tipo_doc,
        "razon_social": razon_social,
    }


def _extraer_detraccion(root: ET.Element) -> dict[str, Any]:
    """
    Extrae datos de detracción del XML.

    En UBL 2.1, la detracción aparece en <cac:PaymentTerms> con ID="Detraccion"
    (o "Detracción"). Trae:
      - PaymentMeansID: código del bien/servicio (037, 022, ...)
      - PaymentPercent: porcentaje (12.00)
      - Amount: monto en moneda de la factura

    Y en <cac:PaymentMeans> con PaymentMeansCode="999" trae el N° de cuenta destino.
    """
    resultado = {
        "tiene_detraccion": False,
        "codigo": None,
        "porcentaje": None,
        "monto_me": None,
        "monto_pen": None,
        "cuenta_destino": None,
    }

    # 1) Buscar PaymentTerms con ID="Detraccion"
    for pt in root.findall(".//cac:PaymentTerms", NS):
        id_el = pt.find("cbc:ID", NS)
        if id_el is None:
            continue
        id_text = (id_el.text or "").strip().lower()
        if "detrac" not in id_text:
            continue

        # Encontramos un PaymentTerms de detracción
        resultado["tiene_detraccion"] = True

        means_id = pt.find("cbc:PaymentMeansID", NS)
        if means_id is not None and means_id.text:
            resultado["codigo"] = means_id.text.strip()

        pct = pt.find("cbc:PaymentPercent", NS)
        if pct is not None and pct.text:
            try:
                resultado["porcentaje"] = Decimal(pct.text.strip())
            except Exception:
                pass

        amt = pt.find("cbc:Amount", NS)
        if amt is not None and amt.text:
            try:
                monto = Decimal(amt.text.strip()).quantize(Decimal("0.01"))
                cur = (amt.attrib.get("currencyID") or "PEN").upper()
                # SUNAT exige que el monto de detracción esté en PEN
                if cur == "PEN":
                    resultado["monto_pen"] = monto
                else:
                    resultado["monto_me"] = monto
            except Exception:
                pass

        break

    # 2) Buscar PaymentMeans con código de detracción para extraer cuenta destino
    for pm in root.findall(".//cac:PaymentMeans", NS):
        # ID del PaymentMeans con name="Detraccion"
        id_el = pm.find("cbc:ID", NS)
        if id_el is None:
            continue
        id_text = (id_el.text or "").strip().lower()
        if "detrac" not in id_text:
            continue

        cuenta_el = pm.find(".//cac:PayeeFinancialAccount/cbc:ID", NS)
        if cuenta_el is not None and cuenta_el.text:
            resultado["cuenta_destino"] = cuenta_el.text.strip()
        break

    return resultado


def _extraer_lineas(root: ET.Element, tipo_comprobante: str) -> list[dict[str, Any]]:
    """
    Extrae las líneas del comprobante.
    Para Invoice → cac:InvoiceLine
    Para CreditNote → cac:CreditNoteLine
    Para DebitNote → cac:DebitNoteLine
    """
    lineas = []
    if tipo_comprobante == "07":
        line_tag = "cac:CreditNoteLine"
        qty_tag = "cbc:CreditedQuantity"
    elif tipo_comprobante == "08":
        line_tag = "cac:DebitNoteLine"
        qty_tag = "cbc:DebitedQuantity"
    else:
        line_tag = "cac:InvoiceLine"
        qty_tag = "cbc:InvoicedQuantity"

    for ln in root.findall(f".//{line_tag}", NS):
        numero = _text(ln.find("cbc:ID", NS), "0")
        try:
            numero_int = int(numero)
        except ValueError:
            numero_int = len(lineas) + 1

        # Cantidad
        qty_el = ln.find(qty_tag, NS)
        cantidad = _decimal_raw(qty_el, Decimal("1"))
        unidad = qty_el.attrib.get("unitCode", "") if qty_el is not None else ""

        # Item — descripción y código
        descripcion = _text(ln.find("cac:Item/cbc:Description", NS))
        codigo = _text(ln.find("cac:Item/cac:SellersItemIdentification/cbc:ID", NS))

        # Precio unitario (sin IGV)
        precio_unitario = _decimal_raw(ln.find("cac:Price/cbc:PriceAmount", NS))

        # Valor de venta de la línea (sin IGV)
        valor_venta = _decimal(ln.find("cbc:LineExtensionAmount", NS))

        # IGV de la línea (puede haber varios TaxSubtotal — sumamos solo el IGV)
        igv_linea = Decimal("0")
        for ts in ln.findall(".//cac:TaxSubtotal", NS):
            tax_id = _text(ts.find(".//cac:TaxScheme/cbc:ID", NS))
            if tax_id == "1000":  # IGV
                igv_linea += _decimal(ts.find("cbc:TaxAmount", NS))

        # Tipo de afectación al IGV
        afect = _text(ln.find(".//cac:TaxCategory/cbc:TaxExemptionReasonCode", NS))

        importe_total_linea = valor_venta + igv_linea

        lineas.append({
            "numero_linea": numero_int,
            "codigo": codigo or None,
            "descripcion": descripcion[:500] if descripcion else "",
            "unidad": unidad or None,
            "cantidad": cantidad,
            "precio_unitario": precio_unitario,
            "valor_venta": valor_venta,
            "igv_linea": igv_linea,
            "importe_total_linea": importe_total_linea,
            "tipo_afectacion_igv": afect or None,
        })

    return lineas


def _extraer_bases_imponibles(root: ET.Element) -> dict[str, Decimal]:
    """
    Extrae las bases imponibles desagregadas (gravadas, exoneradas, inafectas, exportación)
    desde TaxTotal/TaxSubtotal.

    Códigos SUNAT (TaxCategory/ID o TaxExemptionReasonCode):
      1000  IGV (gravada)
      9997  Exonerada
      9998  Inafecta
      9995  Exportación
      7152  Otros (ICBPER bolsas)
    """
    bases = {
        "op_gravadas": Decimal("0"),
        "op_exoneradas": Decimal("0"),
        "op_inafectas": Decimal("0"),
        "op_exportacion": Decimal("0"),
        "op_gratuitas": Decimal("0"),
        "igv": Decimal("0"),
        "icbper": Decimal("0"),
        "isc": Decimal("0"),
    }

    # Solo el TaxTotal a NIVEL DOCUMENTO (hijo directo del root). Con ".//" se
    # sumaba también el TaxTotal de cada línea → duplicaba las bases (se vio en
    # las liquidaciones SelfBilledInvoice, que traen impuesto por línea y por doc).
    subtotales = root.findall("cac:TaxTotal/cac:TaxSubtotal", NS)
    if not subtotales:  # fallback: algún XML pone el TaxTotal anidado en un wrapper
        for tt in root.findall(".//cac:TaxTotal", NS):
            subtotales = tt.findall("cac:TaxSubtotal", NS)
            if subtotales:
                break
    for ts in subtotales:
        # TaxableAmount = base imponible
        base = _decimal(ts.find("cbc:TaxableAmount", NS))
        # TaxAmount = impuesto
        impuesto = _decimal(ts.find("cbc:TaxAmount", NS))
        # TaxScheme/ID — el código del tipo de impuesto
        tax_scheme_id = _text(ts.find(".//cac:TaxScheme/cbc:ID", NS))
        # TaxCategory/ID — más específico
        tax_cat_id = _text(ts.find("cac:TaxCategory/cbc:ID", NS))

        if tax_scheme_id == "1000":
            # IGV (gravada)
            bases["op_gravadas"] += base
            bases["igv"] += impuesto
        elif tax_scheme_id == "9997":
            bases["op_exoneradas"] += base
        elif tax_scheme_id == "9998":
            bases["op_inafectas"] += base
        elif tax_scheme_id == "9995":
            bases["op_exportacion"] += base
        elif tax_scheme_id == "9996":
            bases["op_gratuitas"] += base
        elif tax_scheme_id in ("7152", "7251"):
            bases["icbper"] += impuesto
        elif tax_scheme_id == "2000":
            bases["isc"] += impuesto

    return bases


def parsear_xml_cpe(contenido: bytes | str) -> dict[str, Any]:
    """
    Parsea un XML UBL 2.1 (factura, boleta, NC o ND) y devuelve un dict con
    todos los campos. La asignación al modelo Comprobante la hace el servicio.

    Lanza ValueError si el XML no es válido o no es un CPE reconocido.
    """
    if isinstance(contenido, bytes):
        try:
            texto = contenido.decode("utf-8")
        except UnicodeDecodeError:
            texto = contenido.decode("latin-1", errors="replace")
    else:
        texto = contenido

    # Algunos XMLs vienen dentro de un wrapper <ar:applicationResponse>
    # u otros — buscamos el Invoice/CreditNote directo
    try:
        root = ET.fromstring(texto)
    except ET.ParseError as e:
        raise ValueError(f"XML inválido: {e}")

    # Tags raíz reconocidos. SelfBilledInvoice = Liquidación de Compra (tipo 04),
    # donde el comprador (Falcon) auto-factura al productor sin RUC.
    _ROOTS_CPE = ("Invoice", "CreditNote", "DebitNote", "SelfBilledInvoice")

    # Si el root es applicationResponse u otro contenedor, buscar el CPE anidado
    if root.tag.split("}")[-1] not in _ROOTS_CPE:
        for tag in _ROOTS_CPE:
            child = root.find(f".//{{{NS['']}}}{tag}")
            if child is None:
                child = root.find(f".//{{{NS['cn']}}}{tag}")
            if child is None:
                child = root.find(f".//{{{NS['dn']}}}{tag}")
            if child is not None:
                root = child
                break

    tag = root.tag.split("}")[-1]
    if tag not in _ROOTS_CPE:
        raise ValueError(f"No es un CPE válido (tag raíz: {tag})")

    # ===== Identificación =====
    tipo_comprobante = _identificar_tipo_comprobante(root)
    id_completo = _text(root.find("cbc:ID", NS))
    serie, numero = _parsear_serie_numero(id_completo)

    fecha_emision = _fecha(_text(root.find("cbc:IssueDate", NS)))
    fecha_vencimiento = _fecha(_text(root.find("cbc:DueDate", NS)))
    moneda = _moneda_iso_a_codigo(_text(root.find("cbc:DocumentCurrencyCode", NS)))

    # ===== Emisor (Supplier) y Receptor (Customer) =====
    sup = root.find(".//cac:AccountingSupplierParty/cac:Party", NS)
    cust = root.find(".//cac:AccountingCustomerParty/cac:Party", NS)
    emisor = _extraer_party(sup)
    receptor = _extraer_party(cust)

    # ===== Bases imponibles e impuestos =====
    bases = _extraer_bases_imponibles(root)

    # ===== Totales (LegalMonetaryTotal o RequestedMonetaryTotal según tipo) =====
    if tipo_comprobante == "07":
        total_elem = root.find(".//cac:LegalMonetaryTotal", NS)
    elif tipo_comprobante == "08":
        total_elem = root.find(".//cac:RequestedMonetaryTotal", NS)
    else:
        total_elem = root.find(".//cac:LegalMonetaryTotal", NS)

    if total_elem is not None:
        importe_total = _decimal(total_elem.find("cbc:PayableAmount", NS))
        if importe_total == 0:
            importe_total = _decimal(total_elem.find("cbc:TaxInclusiveAmount", NS))
    else:
        importe_total = Decimal("0")

    descuento_global = Decimal("0")
    otros_cargos = Decimal("0")
    if total_elem is not None:
        descuento_global = _decimal(total_elem.find("cbc:AllowanceTotalAmount", NS))
        otros_cargos = _decimal(total_elem.find("cbc:ChargeTotalAmount", NS))

    # ===== Detracción =====
    detr = _extraer_detraccion(root)

    # Si la factura está en USD pero la detracción solo viene en PEN,
    # calculamos el monto USD a partir del total y porcentaje
    if detr["tiene_detraccion"] and moneda != "PEN":
        if not detr["monto_me"] and detr["porcentaje"]:
            detr["monto_me"] = (
                importe_total * Decimal(detr["porcentaje"]) / 100
            ).quantize(Decimal("0.01"))

    # ===== Líneas =====
    lineas = _extraer_lineas(root, tipo_comprobante)

    # ===== Periodo tributario (YYYYMM) =====
    periodo = ""
    if fecha_emision:
        periodo = f"{fecha_emision.year}{fecha_emision.month:02d}"

    return {
        "tipo_comprobante": tipo_comprobante,
        "serie": serie,
        "numero": numero,
        "fecha_emision": fecha_emision,
        "fecha_vencimiento": fecha_vencimiento,
        "periodo_tributario": periodo,
        "moneda": moneda,

        "ruc_emisor": emisor["ruc"],
        "tipo_doc_emisor": emisor["tipo_doc"] or "6",
        "razon_social_emisor": emisor["razon_social"],

        "ruc_receptor": receptor["ruc"],
        "tipo_doc_receptor": receptor["tipo_doc"],
        "razon_social_receptor": receptor["razon_social"],

        "op_gravadas": bases["op_gravadas"],
        "op_exoneradas": bases["op_exoneradas"],
        "op_inafectas": bases["op_inafectas"],
        "op_exportacion": bases["op_exportacion"],
        "op_gratuitas": bases["op_gratuitas"],
        "igv": bases["igv"],
        "icbper": bases["icbper"],
        "isc": bases["isc"],
        "descuento_global": descuento_global,
        "otros_cargos": otros_cargos,
        "importe_total": importe_total,

        "tiene_detraccion": detr["tiene_detraccion"],
        "detraccion_codigo": detr["codigo"],
        "detraccion_porcentaje": detr["porcentaje"],
        "detraccion_monto_me": detr["monto_me"] if moneda != "PEN" else None,
        "detraccion_monto_pen": detr["monto_pen"],
        "detraccion_cuenta_destino": detr["cuenta_destino"],

        "lineas": lineas,

        "raw_xml": texto,
        "formato_origen": "xml",
    }
