"""Catálogos SUNAT usados al llenar la forma de pago desde el libro de bancos.

REVISAR/COMPLETAR con las tablas oficiales del PDB (Medios de pago y Entidades
financieras) según los bancos que use la empresa. Lo que no esté aquí se deja en
blanco y se avisa al cargar.
"""
# Tipo de pago del libro de bancos (minúsculas, sin tildes) → código de medio de pago.
MEDIOS_PAGO = {
    "transferencia": "003",   # Transferencia de fondos
    "cheque": "007",          # Cheque con cláusula «no negociable»
    "deposito": "001",        # Depósito en cuenta
    "efectivo": "009",        # Efectivo, en los demás casos
}

# Entidad financiera del libro (mayúsculas) → código de banco.
BANCOS = {
    "BBVA": "11",
    "BCP": "02",
}


# N° de documento que se escribe en VENTAS (C8) cuando el cliente no tiene documento (tipo «-»).
# SUNAT rechazó «00000001» ("El número de documento (sin documento) no es válido"); se prueba con
# las alternativas desde la pestaña VENTAS. Clave → texto que ve el usuario.
DOC_SIN_DOCUMENTO = {"00000000": "00000000 (ceros)", "00000001": "00000001", "": "En blanco"}
DOC_SIN_DOCUMENTO_DEFECTO = "00000000"
