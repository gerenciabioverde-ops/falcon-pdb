# Falcon PDB

App independiente (FastAPI + Jinja2 + HTMX) para preparar y llevar el control de la declaración **PDB Exportadores** (SUNAT) por período.

## Qué hace
- Empresas y declaraciones por período (año/mes) con estado: Pendiente → Listo → Enviado (fecha de envío y N° de constancia).
- Importa el CSV de DUAS (bloque D + bloque E) que exporta Falcon Contable, y CSV de las hojas VENTAS y COMPRAS.
- Valida antes de enviar: DUA sin facturas, facturas sin DUA, FOB de la DUA vs. suma de facturas, duplicados, fechas.
- Exporta los CSV (`;`, UTF-8 con BOM) para pegar en el aplicativo PDB.

## Uso local
```
pip install -r requirements.txt
python seed.py                      # crea la BD y el usuario admin / admin123
uvicorn app.main:app --reload       # http://localhost:8000
```
Variables (`.env`): `DATABASE_URL` (SQLite por defecto; Postgres en producción), `JWT_SECRET_KEY` (definir una larga en producción).
