"""Lector del PDF mensual de tipo de cambio de SUNAT (compra/venta por día).

Portado de Falcon Contable (services/importar_tipos_cambio.py::parsear_pdf_sunat).
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO
import re

MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7,
    "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}


class ImportadorTCError(Exception):
    pass


def parsear_pdf_sunat(
    pdf_bytes: bytes,
    moneda_default: str = "USD",
    mes_override: int | None = None,
    anio_override: int | None = None,
) -> tuple[list[dict], dict]:
    """
    Parsea un PDF de SUNAT con TC mensual.
    Devuelve (lista_tcs, metadata) donde metadata contiene mes/año detectados.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportadorTCError("pypdf no está instalado")

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        texto = ""
        for page in reader.pages:
            texto += page.extract_text() + "\n"
    except Exception as e:
        raise ImportadorTCError(f"No se pudo leer el PDF: {e}")

    # Detectar mes y año del título
    mes = mes_override
    anio = anio_override

    if not mes or not anio:
        # Buscar patrón "ENERO - 2026" o "ENERO 2026"
        for nombre, num in MESES.items():
            patron = re.compile(rf"\b{nombre}\b\s*[-–]?\s*(\d{{4}})", re.IGNORECASE)
            m = patron.search(texto)
            if m:
                mes = mes or num
                anio = anio or int(m.group(1))
                break

        # Fallback: buscar formato MM/YYYY o MM-YYYY
        if not mes or not anio:
            m = re.search(r"\b(0?[1-9]|1[0-2])[/\-](\d{4})\b", texto)
            if m:
                mes = mes or int(m.group(1))
                anio = anio or int(m.group(2))

    if not mes or not anio:
        raise ImportadorTCError(
            "No se pudo detectar el mes/año en el PDF. "
            "Especifícalos manualmente en el formulario."
        )

    # Validar
    if not (1 <= mes <= 12):
        raise ImportadorTCError(f"Mes inválido detectado: {mes}")
    if not (2000 <= anio <= 2100):
        raise ImportadorTCError(f"Año inválido detectado: {anio}")

    # Parsear las tuplas (día, compra, venta)
    # El texto del PDF SUNAT viene con cada celda en una línea separada,
    # o bien en líneas con varios valores. El patrón clave: número entero
    # seguido de dos decimales con 3-4 cifras decimales.
    registros = {}

    # Estrategia 1: Líneas separadas (caso común de pypdf)
    # Buscamos secuencias [num_entero, decimal, decimal] seguidos
    tokens = re.findall(r"\d+\.\d{3,4}|\b\d{1,2}\b", texto)

    i = 0
    while i + 2 < len(tokens):
        try:
            t1, t2, t3 = tokens[i], tokens[i+1], tokens[i+2]
            # t1 debe ser entero (sin punto), t2 y t3 decimales
            if "." not in t1 and "." in t2 and "." in t3:
                dia = int(t1)
                compra = Decimal(t2)
                venta = Decimal(t3)

                if 1 <= dia <= 31 and 0 < compra < 100 and 0 < venta < 100:
                    try:
                        fecha = date(anio, mes, dia)
                        # Si ya existe, mantener el primero (evita duplicados de tablas repetidas)
                        if fecha not in registros:
                            registros[fecha] = (compra, venta)
                    except ValueError:
                        pass  # día inválido para ese mes
                i += 3
                continue
        except (InvalidOperation, ValueError, IndexError):
            pass
        i += 1

    if not registros:
        raise ImportadorTCError(
            "No se pudieron extraer tipos de cambio del PDF. "
            "Verifica que sea un PDF de SUNAT con formato estándar."
        )

    moneda = moneda_default.upper()

    resultado = [
        {
            "fecha": fecha,
            "moneda": moneda,
            "compra": compra.quantize(Decimal("0.0001")),
            "venta": venta.quantize(Decimal("0.0001")),
        }
        for fecha, (compra, venta) in sorted(registros.items())
    ]

    metadata = {
        "mes_detectado": mes,
        "anio_detectado": anio,
        "moneda": moneda,
        "total_registros": len(resultado),
        "formato": "PDF SUNAT",
    }

    return resultado, metadata
