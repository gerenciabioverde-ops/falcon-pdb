"""Verificación rápida (sin navegador) de que las pantallas traen el JavaScript que usan.

Uso:  .venv\\Scripts\\python scripts\\verificar_pantalla.py
Crea una BD temporal, recorre las pestañas y comprueba que cada función JS esté definida.
Existe porque una edición de la plantilla ya llegó a borrar por accidente las funciones del modo «Editar».
"""
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "verificar.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.hojas import PESTANAS  # noqa: E402
from app.main import app  # noqa: E402
from seed import *  # noqa: E402,F401,F403  (crea la BD y el usuario admin)

FUNCIONES = ["alternarEdicion", "abrirEdicion", "marcarCambio", "agregarFila", "enviarGrilla",
             "archivosDe", "resumenXml", "enviarXml", "sinMiles", "conMiles"]

fallas = []
with TestClient(app) as c:
    c.post("/login", data={"username": "admin", "password": "admin123"})
    c.post("/empresas", data={"ruc": "20604776440", "razon_social": "PRUEBA"})
    c.post("/declaraciones", data={"empresa_id": 1, "anio": 2026, "mes": 10})
    for p in PESTANAS:
        r = c.get(f"/declaraciones/1?hoja={p['clave']}")
        if r.status_code != 200:
            fallas.append(f"{p['nombre']}: respuesta {r.status_code}")
            continue
        for f in FUNCIONES:
            if not re.search(rf"function\s+{f}\s*\(", r.text):
                fallas.append(f"{p['nombre']}: falta la función JS {f}")
print("\n".join(fallas) if fallas else "OK: todas las pestañas traen sus funciones JS.")
sys.exit(1 if fallas else 0)
