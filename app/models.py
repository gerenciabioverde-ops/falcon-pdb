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
