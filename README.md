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

## Carga automática
- **TC** ← PDF mensual de tipo de cambio de SUNAT.
- **VENTAS / COMPRAS** ← XML UBL de los comprobantes (varios a la vez). Ventas = la empresa es emisor; compras = receptor. Los montos en USD de compras/ventas internas se convierten a soles con el TC venta de la hoja TC; las exportaciones quedan en USD.
- **Forma de pago** ← se genera desde COMPRAS (fecha de operación, monto S/. con las fórmulas del libro); conserva el medio de pago, banco y N° de operación ya escritos.
- **Forma de pago (opción 2)** ← libros de bancos («Detalle de los movimientos», uno por cuenta, MN y ME): busca cada compra por serie-número y RUC y llena medio de pago, banco, N° de operación (N° de cheque, o N° de movimiento si es transferencia), fecha de operación y monto S/. (los pagos en USD se convierten con el TC venta de la fecha del comprobante). Una fila por pago. Los códigos de medio de pago y de banco están en `app/catalogos.py` y deben revisarse.
- **DUAS** ← PDF de la DAM (Registro de Aduanas): llena el bloque D y una fila del bloque E por serie. Parsers portados de Falcon Contable (`app/lectores/`).

## Pantalla
Una declaración se ve como el libro Excel: pestañas INICIO (validación, archivos, estado), TC, COMPRAS, VENTAS, DUAS, SWAP, SERVICIOS, HOSPEDAJE, SIN DETALLE, EXP. DEF. y TURISTAS. Cada pestaña es una grilla editable con los códigos y títulos de columna del libro (importar/exportar CSV, agregar/quitar filas, guardar).

## Windows
Doble click en `instalar.bat` (primera vez) y luego `iniciar.bat`: abre http://localhost:8001 (usuario `admin`, clave `admin123`). `detener.bat` cierra el servidor. `actualizar.bat` baja la última versión desde GitHub y actualiza las dependencias (no toca la base de datos). Si la carpeta está en OneDrive, excluya `.venv` y `falcon_pdb.db` de la sincronización.

## Uso local
```
pip install -r requirements.txt
python seed.py                      # crea la BD y el usuario admin / admin123
uvicorn app.main:app --reload       # http://localhost:8000
```
En producción con PostgreSQL use `pip install -r requirements-prod.txt` (agrega `psycopg2-binary`; en local con SQLite no hace falta).

Variables (`.env`): `DATABASE_URL` (SQLite por defecto; Postgres en producción), `JWT_SECRET_KEY` (definir una larga en producción).
