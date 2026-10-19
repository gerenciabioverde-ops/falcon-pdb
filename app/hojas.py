"""Pestañas del PDB (espejo del libro Excel) y los bloques de datos de cada una.

Un bloque es una grilla: clave de almacenamiento (HojaFila.hoja, o DUAS_D/DUAS_E que
viven en las tablas Dua/Cdp) y sus columnas (código, título, formato).
"""
from app.hojas_cols import COLUMNAS

COLUMNAS_DUAS_D = [
    ("D1", "CÓDIGO ADUANA", "000"), ("D2", "AÑO DE LA DUA", "0000"), ("D3", "N° CORRELATIVO", "000000"),
    ("D4", "FECHA DE EMBARQUE", "FECHA"), ("D5", "FECHA DE REGULARIZACIÓN DE LA DUA", "FECHA"),
    ("D6", "VALOR FOB EN US$", "0.00"),
    ("REF", "N° DE DUA (seguimiento)", "@"), ("REF", "FACTURA(S) (seguimiento)", "@"),   # solo visualización
]
COLUMNAS_DUAS_E = [
    ("E1", "PERIODO DEL CDP", "@"), ("E2", "TIPO DE COMPROBANTE", "00"),
    ("E3", "N° DE SERIE DEL COMPROBANTE", "0000"), ("E4", "N° DE DOCUMENTO DEL COMPROBANTE", "0000000000"),
    ("E5", "TIPO DE EXPORTACIÓN", "00"), ("E6", "DUA (aduana+año+correlativo)", "0000000000000"),
    ("E7", "VALOR FOB FACTURADO EN US$", "0.00"), ("E8", "VALOR FOB PROPIETARIO", "0.00"),
    ("E9", "% DE PARTICIPACIÓN", "0.00"), ("E10", "RUC DEL OPERADOR", "@"),
]
COLUMNAS_TODAS = {**COLUMNAS, "DUAS_D": COLUMNAS_DUAS_D, "DUAS_E": COLUMNAS_DUAS_E}

# Pestañas en el orden del libro. `archivo` = (prefijo, extensión, lleva período en el nombre).
PESTANAS = [
    {"clave": "resumen", "nombre": "INICIO", "bloques": []},
    {"clave": "tc", "nombre": "TC", "archivo": ("", ".tc", False),
     "bloques": [("TC", "Tipo de cambio")]},
    {"clave": "compras", "nombre": "COMPRAS",
     "bloques": [("COMPRAS", "Comprobantes de compras"), ("COMPRAS_PAGO", "Forma de pago")]},
    {"clave": "ventas", "nombre": "VENTAS", "bloques": [("VENTAS", "Comprobantes de ventas")]},
    {"clave": "duas", "nombre": "DUAS",
     "bloques": [("DUAS_D", "Bloque D · una fila por DUA"),
                 ("DUAS_E", "Bloque E · exportación con DUA's (01 al 05) · una fila por factura")]},
    {"clave": "swap", "nombre": "SWAP", "bloques": [("SWAP", "Tipo 06 · Constancia de ejecución SWAP")]},
    {"clave": "servicios", "nombre": "SERVICIOS", "bloques": [("SERVICIOS", "Tipo 07 · Servicios")]},
    {"clave": "hospedaje", "nombre": "HOSPEDAJE", "bloques": [("HOSPEDAJE", "Tipo 08 · Hospedaje a no domiciliados")]},
    {"clave": "sin_detalle", "nombre": "SIN DETALLE", "bloques": [("SIN_DETALLE", "Tipos 09, 10 y 11")]},
    {"clave": "exp_def", "nombre": "EXP. DEF.", "bloques": [("EXP_DEF", "Tipo 12 · Temporal a definitiva")]},
    {"clave": "turistas", "nombre": "TURISTAS", "bloques": [("TURISTAS", "Tipo 13 · Relación de turistas (TaxFree)")]},
]
PESTANA = {p["clave"]: p for p in PESTANAS}

# Archivo que genera cada bloque tabular: (prefijo, extensión, lleva período).
ARCHIVO_BLOQUE = {
    "COMPRAS": ("C", ".txt", True), "COMPRAS_PAGO": ("F", ".txt", True), "VENTAS": ("V", ".txt", True),
    "TC": ("", ".tc", False), "SWAP": ("", ".swp", True), "SERVICIOS": ("", ".ser", True),
    "HOSPEDAJE": ("", ".hos", True), "EXP_DEF": ("", ".t12", True),
    "SIN_DETALLE": ("", ".exp", True), "TURISTAS": ("", ".pet", True),
}
TITULOS_BLOQUE = {b: t for p in PESTANAS for b, t in p["bloques"]}
BLOQUES_TABULARES = list(ARCHIVO_BLOQUE)
