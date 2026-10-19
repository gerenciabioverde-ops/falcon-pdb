"""Lógica de negocio de Falcon PDB: importación, validación y exportación.

Formato de intercambio con Falcon Contable: CSV con ';' y dos bloques
(DUAS bloque D + CDP bloque E), fechas dd/mm/yyyy.
"""
from __future__ import annotations
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session, object_session

from app.hojas import COLUMNAS_TODAS
from app.models import Ajuste, Cdp, Declaracion, Dua, HojaFila, TipoCambioDia

PDB_DUA_HEADERS = [
    "D1 Cod.Aduana", "D2 Año DUA", "D3 N° Correlativo",
    "D4 Fecha Embarque", "D5 Fecha Regularización", "D6 Valor FOB US$",
]
PDB_CDP_HEADERS = [
    "E1 Periodo CDP", "E2 Tipo Comp.", "E3 Serie", "E4 Número",
    "E5 Tipo Export.", "DUA", "E7 FOB Facturado US$",
    "E8 FOB Propietario US$", "E9 % Particip.", "E10 RUC Operador",
]
TOLERANCIA_FOB = Decimal("0.05")


def _dec(txt) -> Decimal:
    txt = (str(txt) if txt is not None else "").strip().replace(",", "")
    if not txt:
        return Decimal("0")
    try:
        return Decimal(txt)
    except InvalidOperation:
        raise ValueError(f"Importe inválido: {txt!r}")


def _fecha(txt) -> date | None:
    txt = (txt or "").strip()
    if not txt:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha inválida: {txt!r} (use dd/mm/aaaa)")


def _ddmmyyyy(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _leer_csv(contenido: bytes) -> list[list[str]]:
    texto = contenido.decode("utf-8-sig", errors="replace")
    delim = ";" if texto.count(";") >= texto.count(",") else ","
    return [[c.strip() for c in fila] for fila in csv.reader(io.StringIO(texto), delimiter=delim)]


# ------------------------------------------------------------------ importación

def importar_duas_csv(db: Session, decl: Declaracion, contenido: bytes) -> tuple[int, int]:
    """Reemplaza DUAs y CDPs de la declaración con los del CSV (formato Falcon Contable).
    Devuelve (n_duas, n_cdps). Lanza ValueError con el N° de fila si algo no cuadra."""
    filas = _leer_csv(contenido)
    duas: list[Dua] = []
    cdps: list[Cdp] = []
    bloque = None
    for n, fila in enumerate(filas, start=1):
        if not any(fila):
            continue
        primera = fila[0].upper()
        if primera.startswith("DUAS"):
            bloque = "D_enc"
            continue
        if primera.startswith("CDP"):
            bloque = "E_enc"
            continue
        if bloque == "D_enc":
            bloque = "D"
            continue          # fila de encabezados
        if bloque == "E_enc":
            bloque = "E"
            continue
        try:
            if bloque == "D":
                if len(fila) < 6:
                    raise ValueError("Se esperaban 6 columnas en el bloque DUA")
                duas.append(Dua(
                    codigo_aduana=fila[0], anio_dua=fila[1], correlativo=fila[2],
                    fecha_embarque=_fecha(fila[3]), fecha_regularizacion=_fecha(fila[4]),
                    fob_usd=_dec(fila[5]),
                ))
            elif bloque == "E":
                if len(fila) < 10:
                    raise ValueError("Se esperaban 10 columnas en el bloque CDP")
                cdps.append(Cdp(
                    periodo_cdp=fila[0], tipo_comprobante=fila[1], serie=fila[2], numero=fila[3],
                    tipo_exportacion=fila[4], dua_key=fila[5], fob_facturado=_dec(fila[6]),
                    fob_propietario=_dec(fila[7]), pct_participacion=_dec(fila[8]) or Decimal("100"),
                    ruc_operador=fila[9],
                ))
            else:
                raise ValueError("Fila fuera de un bloque «DUAS» o «CDP»")
        except ValueError as e:
            raise ValueError(f"Fila {n}: {e}") from None
    if not duas and not cdps:
        raise ValueError("El archivo no tiene bloques DUAS/CDP con datos.")
    decl.duas.clear()
    decl.cdps.clear()
    db.flush()
    decl.duas.extend(duas)
    decl.cdps.extend(cdps)
    return len(duas), len(cdps)


def importar_hoja_csv(db: Session, decl: Declaracion, hoja: str, contenido: bytes) -> int:
    """Reemplaza las filas de una hoja tabular (primera fila = encabezados, se omite)."""
    filas = [f for f in _leer_csv(contenido) if any(f)]
    if len(filas) < 2:
        raise ValueError("El archivo debe tener encabezados y al menos una fila de datos.")
    if hoja == "TC":
        return registrar_tc_historial(db, _parsear_tc(filas[1:]))     # agrega/actualiza, no borra
    for f in [f for f in decl.filas if f.hoja == hoja]:
        decl.filas.remove(f)
    db.flush()
    for i, celdas in enumerate(filas[1:]):
        decl.filas.append(HojaFila(hoja=hoja, orden=i, celdas=celdas))
    return len(filas) - 1


# ------------------------------------------------------------------ grillas (pestañas)

def _txt_fecha(d) -> str:
    return _ddmmyyyy(d)


def _tc_a_filas(registros) -> list[list[str]]:
    return [[_ddmmyyyy(r.fecha), f"{r.compra:.3f}", f"{r.venta:.3f}"] for r in registros]


def _parsear_tc(filas: list[list[str]]) -> list[tuple]:
    """[fecha, compra, venta] en texto → [(date, Decimal, Decimal)]; ValueError con N° de fila."""
    salida = []
    for n, f in enumerate(filas, start=1):
        f = (list(f) + ["", "", ""])[:3]
        try:
            fecha = _fecha(f[0])
            if not fecha:
                raise ValueError("falta la fecha")
            compra, venta = _dec(f[1]), _dec(f[2])
            if compra <= 0 or venta <= 0:
                raise ValueError("el tipo de cambio debe ser mayor que cero")
        except ValueError as e:
            raise ValueError(f"Fila {n}: {e}") from None
        salida.append((fecha, compra, venta))
    return salida


def registrar_tc_historial(db: Session, registros: list[tuple]) -> int:
    """Agrega o actualiza fechas en el historial común de tipo de cambio."""
    for fecha, compra, venta in registros:
        fila = db.get(TipoCambioDia, fecha)
        if fila:
            fila.compra, fila.venta = compra, venta
        else:
            db.add(TipoCambioDia(fecha=fecha, compra=compra, venta=venta))
    db.flush()
    return len(registros)


def purgar_tc_futuros(db: Session) -> int:
    """Borra del historial los días posteriores a hoy: SUNAT aún no los publica, así que son datos
    de otro mes/año cargados por error. Devuelve cuántos borró."""
    n = db.query(TipoCambioDia).filter(TipoCambioDia.fecha > date.today()).delete(synchronize_session=False)
    db.flush()
    return n


def normalizar_ventas_sin_documento(db: Session) -> int:
    """Ventas con tipo de documento «-»: deja en blanco su N° de documento (regla de SUNAT)."""
    n = 0
    for f in db.query(HojaFila).filter(HojaFila.hoja == "VENTAS").all():
        c = list(f.celdas)
        if len(c) > 7 and str(c[6]).strip() == "-" and str(c[7]).strip():
            c[7] = ""
            f.celdas = c
            n += 1
    if n:
        db.flush()
    return n


def sembrar_historial(db: Session) -> None:
    """Al arrancar: copia al historial los TC que ya estaban en declaraciones (solo fechas faltantes)
    y quita los días futuros."""
    purgar_tc_futuros(db)
    normalizar_ventas_sin_documento(db)
    existentes = {r[0] for r in db.query(TipoCambioDia.fecha).all()}
    nuevos = []
    for f in db.query(HojaFila).filter(HojaFila.hoja == "TC").all():
        try:
            (fecha, c, v), = _parsear_tc([f.celdas])
        except ValueError:
            continue
        if fecha not in existentes and fecha <= date.today():
            existentes.add(fecha)
            nuevos.append((fecha, c, v))
    if nuevos:
        registrar_tc_historial(db, nuevos)
    db.commit()


def _tc_del_mes(db: Session, anio: int, mes: int) -> list[TipoCambioDia]:
    ini = date(anio, mes, 1)
    fin = date(anio + (mes == 12), mes % 12 + 1, 1)
    return (db.query(TipoCambioDia).filter(TipoCambioDia.fecha >= ini, TipoCambioDia.fecha < fin)
            .order_by(TipoCambioDia.fecha).all())


def meses_tc(db: Session) -> list[tuple[int, int, int]]:
    """[(año, mes, n° de días)] de los meses con tipo de cambio guardado, del más reciente al más antiguo."""
    cuenta: dict[tuple[int, int], int] = {}
    for (f,) in db.query(TipoCambioDia.fecha).all():
        cuenta[(f.year, f.month)] = cuenta.get((f.year, f.month), 0) + 1
    return [(a, m, n) for (a, m), n in sorted(cuenta.items(), reverse=True)]


def filas_bloque(decl: Declaracion, bloque: str, mes: tuple[int, int] | None = None) -> list[list[str]]:
    """Filas de un bloque como listas de texto, para mostrar/editar en la grilla.
    El tipo de cambio (TC) vive en el historial interno y se muestra por mes (por defecto el del período)."""
    if bloque == "TC":
        anio, m = mes or (decl.anio, decl.mes)
        return _tc_a_filas(_tc_del_mes(object_session(decl), anio, m))
    if bloque == "DUAS_D":
        return [[d.codigo_aduana, d.anio_dua, d.correlativo, _txt_fecha(d.fecha_embarque),
                 _txt_fecha(d.fecha_regularizacion), f"{d.fob_usd:.2f}"] for d in decl.duas]
    if bloque == "DUAS_E":
        return [[c.periodo_cdp, c.tipo_comprobante, c.serie, c.numero, c.tipo_exportacion, c.dua_key,
                 f"{c.fob_facturado:.2f}", f"{c.fob_propietario:.2f}", f"{c.pct_participacion:.2f}",
                 c.ruc_operador] for c in decl.cdps]
    return [f.celdas for f in decl.filas if f.hoja == bloque]


def guardar_grilla(db: Session, decl: Declaracion, bloque: str, filas: list[list[str]],
                   mes: tuple[int, int] | None = None) -> int:
    """Reemplaza el contenido de un bloque con las filas de la grilla (filas vacías se omiten).
    Lanza ValueError con el N° de fila si un dato no es válido."""
    ncols = len(COLUMNAS_TODAS[bloque])
    filas = [([str(c).strip() for c in f] + [""] * ncols)[:ncols] for f in filas if any(str(c).strip() for c in f)]
    if bloque == "VENTAS":
        for f in filas:
            if f[6] == "-":
                f[7] = ""                        # sin documento → N° de documento en blanco
    if bloque == "TC":
        registros = _parsear_tc(filas)
        anio, m = mes or (decl.anio, decl.mes)
        for r in _tc_del_mes(db, anio, m):          # lo que se quitó de la grilla se borra del mes
            db.delete(r)
        db.flush()
        registrar_tc_historial(db, registros)
        return len(registros)
    if bloque == "DUAS_D":
        nuevas = []
        for n, f in enumerate(filas, start=1):
            try:
                nuevas.append(Dua(codigo_aduana=f[0], anio_dua=f[1], correlativo=f[2],
                                  fecha_embarque=_fecha(f[3]), fecha_regularizacion=_fecha(f[4]),
                                  fob_usd=_dec(f[5])))
            except ValueError as e:
                raise ValueError(f"Fila {n}: {e}") from None
        decl.duas.clear()
        db.flush()
        decl.duas.extend(nuevas)
    elif bloque == "DUAS_E":
        nuevas = []
        for n, f in enumerate(filas, start=1):
            try:
                nuevas.append(Cdp(periodo_cdp=f[0], tipo_comprobante=f[1], serie=f[2], numero=f[3],
                                  tipo_exportacion=f[4], dua_key=f[5], fob_facturado=_dec(f[6]),
                                  fob_propietario=_dec(f[7]), pct_participacion=_dec(f[8]) or Decimal("100"),
                                  ruc_operador=f[9]))
            except ValueError as e:
                raise ValueError(f"Fila {n}: {e}") from None
        decl.cdps.clear()
        db.flush()
        decl.cdps.extend(nuevas)
    else:
        for f in [f for f in decl.filas if f.hoja == bloque]:
            decl.filas.remove(f)
        db.flush()
        for i, celdas in enumerate(filas):
            decl.filas.append(HojaFila(hoja=bloque, orden=i, celdas=celdas))
    return len(filas)


def _safe_fecha(txt):
    try:
        return _fecha(txt)
    except ValueError:
        return None


# ------------------------------------------------------------------ validación

def validar(decl: Declaracion) -> list[dict]:
    """Devuelve hallazgos [{nivel: 'error'|'aviso', texto}] antes de enviar el PDB."""
    h: list[dict] = []

    def err(t): h.append({"nivel": "error", "texto": t})
    def avi(t): h.append({"nivel": "aviso", "texto": t})

    if not decl.duas:
        err("No hay DUAs cargadas (bloque D).")
    if not decl.cdps:
        err("No hay facturas de exportación cargadas (bloque E).")

    claves = {d.clave: d for d in decl.duas}
    if len(claves) != len(decl.duas):
        err("Hay DUAs repetidas (misma aduana, año y correlativo).")

    fob_cdp: dict[str, Decimal] = {}
    vistos = set()
    for c in decl.cdps:
        id_cdp = f"{c.serie}-{c.numero}"
        if (c.tipo_comprobante, c.serie, c.numero) in vistos:
            err(f"Factura {id_cdp} duplicada.")
        vistos.add((c.tipo_comprobante, c.serie, c.numero))
        if c.dua_key not in claves:
            err(f"Factura {id_cdp}: la DUA «{c.dua_key}» no existe en el bloque D.")
        else:
            fob_cdp[c.dua_key] = fob_cdp.get(c.dua_key, Decimal("0")) + (c.fob_facturado or 0)
        if (c.fob_propietario or 0) > (c.fob_facturado or 0) + TOLERANCIA_FOB:
            err(f"Factura {id_cdp}: FOB propietario mayor que FOB facturado.")
        if len(c.periodo_cdp or "") != 6:
            avi(f"Factura {id_cdp}: período CDP «{c.periodo_cdp}» no tiene formato AAAAMM.")

    for d in decl.duas:
        if d.clave not in fob_cdp:
            err(f"DUA {d.clave} sin facturas en el bloque E.")
        elif abs(fob_cdp[d.clave] - (d.fob_usd or 0)) > TOLERANCIA_FOB:
            err(f"DUA {d.clave}: FOB {d.fob_usd} ≠ suma de facturas {fob_cdp[d.clave]}.")
        if not d.fecha_embarque:
            err(f"DUA {d.clave}: falta fecha de embarque.")
        if not d.fecha_regularizacion:
            avi(f"DUA {d.clave}: sin fecha de regularización.")
        elif d.fecha_embarque and d.fecha_regularizacion < d.fecha_embarque:
            err(f"DUA {d.clave}: regularización anterior al embarque.")

    ventas = {(f.celdas[3].strip().upper(), f.celdas[4].strip().lstrip("0")): f.celdas
              for f in decl.filas if f.hoja == "VENTAS" and len(f.celdas) > 4}
    en_dam = {(c.serie.strip().upper(), c.numero.strip().lstrip("0")) for c in decl.cdps}
    if ventas:
        for serie, num in sorted(en_dam - set(ventas)):
            avi(f"Factura {serie}-{num} de la DAM no figura en la hoja VENTAS.")
        for (serie, num), celdas in sorted(ventas.items()):
            if celdas[0] == "2" and (serie, num) not in en_dam and decl.cdps:
                avi(f"Exportación {serie}-{num} de VENTAS no figura en las DUAS (bloque E).")

    for f in decl.filas:
        if f.hoja == "COMPRAS" and len(f.celdas) > 23 and f.celdas[20].strip() == "1":
            if not f.celdas[21].strip() or not f.celdas[22].strip():
                avi(f"Compra {f.celdas[3]}-{f.celdas[4]} con detracción: falta el código de la tasa (A22) o la constancia (A23).")

    notas = {"COMPRAS": ("7", "8", "87", "88", "97", "98"), "VENTAS": ("7", "8")}
    for f in decl.filas:
        if f.hoja in notas and len(f.celdas) > 27 and f.celdas[1].strip().lstrip("0") in notas[f.hoja]:
            fref, fnota = _safe_fecha(f.celdas[27]), _safe_fecha(f.celdas[2])
            if fref and fnota and fref > fnota:
                avi(f"{f.celdas[3]}-{f.celdas[4]}: la fecha del documento de referencia es posterior a la de la nota.")
            if not all(x.strip() for x in f.celdas[24:28]):
                avi(f"{'Compra' if f.hoja == 'COMPRAS' else 'Venta'} {f.celdas[3]}-{f.celdas[4]} (nota): "
                    "complete el documento de referencia (tipo, serie, número y fecha).")

    hojas = {f.hoja for f in decl.filas}
    for hoja in ("VENTAS", "COMPRAS"):
        if hoja not in hojas:
            avi(f"La hoja {hoja} está vacía.")
    return h


# ------------------------------------------------------------------ exportación

def duas_a_csv(decl: Declaracion) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["DUAS — Bloque D (una fila por DUA)"])
    w.writerow(PDB_DUA_HEADERS)
    for d in decl.duas:
        w.writerow([d.codigo_aduana, d.anio_dua, d.correlativo, _ddmmyyyy(d.fecha_embarque),
                    _ddmmyyyy(d.fecha_regularizacion), f"{d.fob_usd:.2f}"])
    w.writerow([])
    w.writerow(["CDP — Bloque E (una fila por factura de exportación)"])
    w.writerow(PDB_CDP_HEADERS)
    for c in decl.cdps:
        w.writerow([c.periodo_cdp, c.tipo_comprobante, c.serie, c.numero, c.tipo_exportacion,
                    c.dua_key, f"{c.fob_facturado:.2f}", f"{c.fob_propietario:.2f}",
                    f"{c.pct_participacion:.0f}", c.ruc_operador])
    return buf.getvalue()


def hoja_a_csv(decl: Declaracion, hoja: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    for celdas in filas_bloque(decl, hoja):
        w.writerow(celdas)
    return buf.getvalue()


def obtener_ajuste(db: Session, clave: str, defecto: str = "") -> str:
    a = db.get(Ajuste, clave)
    return a.valor if a else defecto


def guardar_ajuste(db: Session, clave: str, valor: str) -> None:
    a = db.get(Ajuste, clave)
    if a:
        a.valor = valor
    else:
        db.add(Ajuste(clave=clave, valor=valor))
    db.flush()


def resumen_declaracion(decl: Declaracion) -> dict:
    """Cifras para la tarjeta del período en la pantalla de inicio."""
    cuenta = lambda h: sum(1 for f in decl.filas if f.hoja == h and any(f.celdas))  # noqa: E731
    hallazgos = validar(decl)
    return {
        "compras": cuenta("COMPRAS"), "ventas": cuenta("VENTAS"), "duas": len(decl.duas),
        "errores": sum(h["nivel"] == "error" for h in hallazgos),
        "avisos": sum(h["nivel"] == "aviso" for h in hallazgos),
    }
