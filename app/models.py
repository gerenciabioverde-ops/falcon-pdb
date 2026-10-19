"""Modelos de Falcon PDB.

Una `Declaracion` es el PDB Exportadores de un período (año/mes) de una empresa.
Cuelga de ella: DUAs (bloque D), CDPs (bloque E) y filas de las hojas
VENTAS / COMPRAS (guardadas tal cual, como lista de celdas JSON).
"""
from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

ESTADOS = {"PENDIENTE": "Pendiente", "LISTO": "Listo para enviar", "ENVIADO": "Enviado a SUNAT"}


class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)


class Empresa(Base):
    __tablename__ = "empresas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ruc: Mapped[str] = mapped_column(String(11), unique=True, nullable=False)
    razon_social: Mapped[str] = mapped_column(String(200), nullable=False)
    declaraciones: Mapped[list["Declaracion"]] = relationship(
        back_populates="empresa", cascade="all, delete-orphan")


class Declaracion(Base):
    __tablename__ = "declaraciones"
    __table_args__ = (UniqueConstraint("empresa_id", "anio", "mes", name="uq_decl_periodo"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    anio: Mapped[int] = mapped_column(Integer, nullable=False)
    mes: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[str] = mapped_column(String(12), default="PENDIENTE", nullable=False)
    fecha_envio: Mapped[date | None] = mapped_column(Date, nullable=True)
    nro_constancia: Mapped[str | None] = mapped_column(String(40), nullable=True)
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    empresa: Mapped[Empresa] = relationship(back_populates="declaraciones")
    duas: Mapped[list["Dua"]] = relationship(
        back_populates="declaracion", cascade="all, delete-orphan", order_by="Dua.id")
    cdps: Mapped[list["Cdp"]] = relationship(
        back_populates="declaracion", cascade="all, delete-orphan", order_by="Cdp.id")
    filas: Mapped[list["HojaFila"]] = relationship(
        back_populates="declaracion", cascade="all, delete-orphan", order_by="HojaFila.orden")

    @property
    def periodo(self) -> str:
        return f"{self.anio}{self.mes:02d}"


class Dua(Base):
    """Bloque D del PDB: una fila por DUA."""
    __tablename__ = "duas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaracion_id: Mapped[int] = mapped_column(ForeignKey("declaraciones.id", ondelete="CASCADE"), index=True)
    codigo_aduana: Mapped[str] = mapped_column(String(3), nullable=False)       # D1
    anio_dua: Mapped[str] = mapped_column(String(4), nullable=False)            # D2
    correlativo: Mapped[str] = mapped_column(String(10), nullable=False)        # D3
    fecha_embarque: Mapped[date | None] = mapped_column(Date, nullable=True)    # D4
    fecha_regularizacion: Mapped[date | None] = mapped_column(Date, nullable=True)  # D5
    fob_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)         # D6
    declaracion: Mapped[Declaracion] = relationship(back_populates="duas")

    @property
    def clave(self) -> str:
        return f"{self.codigo_aduana}{self.anio_dua}{self.correlativo}"


class Cdp(Base):
    """Bloque E del PDB: una fila por factura de exportación."""
    __tablename__ = "cdps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaracion_id: Mapped[int] = mapped_column(ForeignKey("declaraciones.id", ondelete="CASCADE"), index=True)
    periodo_cdp: Mapped[str] = mapped_column(String(6), default="")             # E1 YYYYMM
    tipo_comprobante: Mapped[str] = mapped_column(String(2), default="01")      # E2
    serie: Mapped[str] = mapped_column(String(10), default="")                  # E3
    numero: Mapped[str] = mapped_column(String(15), default="")                 # E4
    tipo_exportacion: Mapped[str] = mapped_column(String(2), default="1")       # E5
    dua_key: Mapped[str] = mapped_column(String(20), default="")                # DUA
    fob_facturado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)   # E7
    fob_propietario: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)  # E8
    pct_participacion: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=100)  # E9
    ruc_operador: Mapped[str] = mapped_column(String(11), default="")           # E10
    declaracion: Mapped[Declaracion] = relationship(back_populates="cdps")


class HojaFila(Base):
    """Fila de una hoja tabular (VENTAS, COMPRAS, COMPRAS_PAGO): lista de celdas como texto."""
    __tablename__ = "hoja_filas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    declaracion_id: Mapped[int] = mapped_column(ForeignKey("declaraciones.id", ondelete="CASCADE"), index=True)
    hoja: Mapped[str] = mapped_column(String(20), nullable=False)
    orden: Mapped[int] = mapped_column(Integer, default=0)
    celdas: Mapped[list] = mapped_column(JSON, default=list)
    declaracion: Mapped[Declaracion] = relationship(back_populates="filas")


class TipoCambioDia(Base):
    """Historial de tipo de cambio USD (hoja HISTORIAL_TC del libro), común a todas las empresas."""
    __tablename__ = "tipo_cambio_dia"
    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    compra: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)
    venta: Mapped[Decimal] = mapped_column(Numeric(8, 3), nullable=False)


class Ajuste(Base):
    """Ajustes sencillos de la aplicación (clave → valor)."""
    __tablename__ = "ajustes"
    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(String(200), default="")


class Detraccion(Base):
    """Constancia de depósito de detracción (HTML de SUNAT). Es de la empresa, no de un período:
    se enlaza a la compra por proveedor + comprobante, sea cual sea el período de la declaración."""
    __tablename__ = "detracciones"
    __table_args__ = (UniqueConstraint("empresa_id", "constancia", name="uq_detraccion_constancia"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    constancia: Mapped[str] = mapped_column(String(20), nullable=False)
    ruc_proveedor: Mapped[str] = mapped_column(String(11), default="")
    proveedor: Mapped[str] = mapped_column(String(200), default="")
    tipo_comprobante: Mapped[str] = mapped_column(String(2), default="")
    serie: Mapped[str] = mapped_column(String(10), default="")
    numero: Mapped[str] = mapped_column(String(20), default="")
    bien_servicio: Mapped[str] = mapped_column(String(3), default="")
    monto: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    fecha_pago: Mapped[date | None] = mapped_column(Date, nullable=True)
    periodo_tributario: Mapped[str] = mapped_column(String(6), default="")
    nro_operacion: Mapped[str] = mapped_column(String(20), default="")


class Form1662(Base):
    """Constancia del Formulario 1662 (pago de IGV por servicios de no domiciliados). De la empresa, no de un período."""
    __tablename__ = "formularios_1662"
    __table_args__ = (UniqueConstraint("empresa_id", "nro_orden", name="uq_f1662_orden"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id", ondelete="CASCADE"), index=True)
    nro_orden: Mapped[str] = mapped_column(String(20), nullable=False)
    periodo: Mapped[str] = mapped_column(String(6), default="")
    serie: Mapped[str] = mapped_column(String(20), default="")
    numero: Mapped[str] = mapped_column(String(30), default="")
    importe: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    fecha_pago: Mapped[date | None] = mapped_column(Date, nullable=True)
    banco: Mapped[str] = mapped_column(String(80), default="")
    nro_operacion: Mapped[str] = mapped_column(String(30), default="")
