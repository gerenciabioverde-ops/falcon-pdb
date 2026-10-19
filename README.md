# Falcon PDB

App independiente (FastAPI + Jinja2 + HTMX) para preparar y llevar el control de la declaración **PDB Exportadores** (SUNAT) por período.

## Qué hace
- Empresas y declaraciones por período (año/mes) con estado: Pendiente → Listo → Enviado (fecha de envío y N° de constancia).
- Importa el CSV de DUAS (bloque D + bloque E) que exporta Falcon Contable, y CSV de las hojas VENTAS y COMPRAS.
- Valida antes de enviar: DUA sin facturas, facturas sin DUA, FOB de la DUA vs. suma de facturas, duplicados, fechas.
- Genera los **archivos de texto que carga SUNAT**, con el mismo formato que las macros del libro `PDB Exportador` (campos separados por `|`, formato fijo por columna, ANSI, CRLF):
  `C{ruc}{AAAAMM}.txt` compras · `F…txt` formas de pago · `V…txt` ventas · `{ruc}{AAAAMM}.dua` DUAS bloque D · `.du1` bloque E · `{ruc}.tc` tipo de cambio. Descarga individual o ZIP.
- También exporta los CSV (`;`, UTF-8 con BOM) de DUAS y de cada hoja.

Las columnas y formatos de cada hoja salen de `app/hojas_cols.py` (extraído del libro). Los CSV de las hojas llevan encabezado (se omite al importar). Extensiones: `.swp`, `.ser`, `.hos`, `.t12` (EXP. DEF.), `.exp` (SIN DETALLE), `.pet` (TURISTAS).

## Pantalla
Una declaración se ve como el libro Excel: pestañas INICIO (validación, archivos, estado), TC, COMPRAS, VENTAS, DUAS, SWAP, SERVICIOS, HOSPEDAJE, SIN DETALLE, EXP. DEF. y TURISTAS. Cada pestaña es una grilla editable con los códigos y títulos de columna del libro (importar/exportar CSV, agregar/quitar filas, guardar).

## Uso local
```
pip install -r requirements.txt
python seed.py                      # crea la BD y el usuario admin / admin123
uvicorn app.main:app --reload       # http://localhost:8000
```
Variables (`.env`): `DATABASE_URL` (SQLite por defecto; Postgres en producción), `JWT_SECRET_KEY` (definir una larga en producción).
