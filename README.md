# Falcon PDB

App independiente (FastAPI + Jinja2 + HTMX) para preparar y llevar el control de la declaración **PDB Exportadores** (SUNAT) por período.

## Qué hace
- Empresas y declaraciones por período (año/mes) con estado: Pendiente → Listo → Enviado (fecha de envío y N° de constancia).
- Importa el CSV de DUAS (bloque D + bloque E) que exporta Falcon Contable, y CSV de las hojas VENTAS y COMPRAS.
- Valida antes de enviar: DUA sin facturas, facturas sin DUA, FOB de la DUA vs. suma de facturas, duplicados, fechas.
- Genera los **archivos de texto que carga SUNAT**, con el mismo formato que las macros del libro `PDB Exportador` (campos separados por `|`, formato fijo por columna, ANSI, CRLF):
  `C{ruc}{AAAAMM}.txt` compras · `F…txt` formas de pago · `V…txt` ventas · `{ruc}{AAAAMM}.dua` DUAS bloque D · `.du1` bloque E · `{ruc}.tc` tipo de cambio. Descarga individual o ZIP.
- También exporta los CSV (`;`, UTF-8 con BOM) de DUAS y de cada hoja.

Las hojas COMPRAS (30 col. A1..A30), COMPRAS_PAGO (12 col.), VENTAS (30 col. C1..C30) y TC (fecha, compra, venta) se importan por CSV con encabezado. Las columnas extra (tipo de cambio automático, C31) se ignoran al generar.

## Uso local
```
pip install -r requirements.txt
python seed.py                      # crea la BD y el usuario admin / admin123
uvicorn app.main:app --reload       # http://localhost:8000
```
Variables (`.env`): `DATABASE_URL` (SQLite por defecto; Postgres en producción), `JWT_SECRET_KEY` (definir una larga en producción).
