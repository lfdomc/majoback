from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Usuario(Base):
    __tablename__ = "usuarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(120), default="")
    clave_hash: Mapped[str] = mapped_column(String(200))
    es_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Proveedor(Base):
    __tablename__ = "proveedores"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), unique=True)
    contacto: Mapped[str] = mapped_column(String(200), default="")
    notas: Mapped[str] = mapped_column(Text, default="")


class Ingrediente(Base):
    __tablename__ = "ingredientes"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    categoria: Mapped[str] = mapped_column(String(80), default="General")
    unidad_base: Mapped[str] = mapped_column(String(10), default="g")  # g | ml | unidad
    peso_unidad_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    densidad_g_ml: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_costo: Mapped[str] = mapped_column(String(12), default="preferida")  # preferida|menor|promedio|mayor
    notas: Mapped[str] = mapped_column(Text, default="")
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    presentaciones: Mapped[list["Presentacion"]] = relationship(
        back_populates="ingrediente", cascade="all, delete-orphan", order_by="Presentacion.id")


class Presentacion(Base):
    """Cómo se compra un ingrediente: precio, contenido y merma."""
    __tablename__ = "presentaciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    ingrediente_id: Mapped[int] = mapped_column(ForeignKey("ingredientes.id", ondelete="CASCADE"))
    proveedor_id: Mapped[int | None] = mapped_column(ForeignKey("proveedores.id", ondelete="SET NULL"), nullable=True)
    descripcion: Mapped[str] = mapped_column(String(200), default="")
    precio: Mapped[float] = mapped_column(Float, default=0)
    cantidad: Mapped[float] = mapped_column(Float, default=1)
    unidad: Mapped[str] = mapped_column(String(10), default="g")
    merma_pct: Mapped[float] = mapped_column(Float, default=0)
    preferida: Mapped[bool] = mapped_column(Boolean, default=False)
    fecha: Mapped[date | None] = mapped_column(Date, nullable=True)
    ingrediente: Mapped[Ingrediente] = relationship(back_populates="presentaciones")
    proveedor: Mapped[Proveedor | None] = relationship()


class Receta(Base):
    __tablename__ = "recetas"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    tipo: Mapped[str] = mapped_column(String(12), default="producto")  # producto | subreceta
    categoria: Mapped[str] = mapped_column(String(80), default="General")
    rinde_cantidad: Mapped[float] = mapped_column(Float, default=1)
    rinde_unidad: Mapped[str] = mapped_column(String(10), default="porcion")
    merma_pct: Mapped[float] = mapped_column(Float, default=0)
    margen_objetivo_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    precio_venta: Mapped[float | None] = mapped_column(Float, nullable=True)
    precio_competencia: Mapped[float | None] = mapped_column(Float, nullable=True)
    notas: Mapped[str] = mapped_column(Text, default="")
    # Menú para clientes
    foto: Mapped[str | None] = mapped_column(String(120), nullable=True)
    descripcion_menu: Mapped[str] = mapped_column(Text, default="")
    nombre_en: Mapped[str] = mapped_column(String(160), default="")          # nombre en inglés (menú /en)
    descripcion_menu_en: Mapped[str] = mapped_column(Text, default="")
    en_menu: Mapped[bool] = mapped_column(Boolean, default=False)
    arte: Mapped[str | None] = mapped_column(String(120), nullable=True)      # imagen tipo anuncio generada
    arte_en: Mapped[str | None] = mapped_column(String(120), nullable=True)   # la misma imagen con el nombre en inglés
    recorte: Mapped[str | None] = mapped_column(String(120), nullable=True)   # foto sin fondo
    estilo_arte: Mapped[str] = mapped_column(String(12), default="auto")
    quitar_fondo: Mapped[bool] = mapped_column(Boolean, default=True)
    actualizado: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    items: Mapped[list["RecetaItem"]] = relationship(
        back_populates="receta", cascade="all, delete-orphan", order_by="RecetaItem.orden",
        foreign_keys="RecetaItem.receta_id")


class RecetaItem(Base):
    __tablename__ = "receta_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    receta_id: Mapped[int] = mapped_column(ForeignKey("recetas.id", ondelete="CASCADE"))
    orden: Mapped[int] = mapped_column(Integer, default=0)
    ingrediente_id: Mapped[int | None] = mapped_column(ForeignKey("ingredientes.id", ondelete="RESTRICT"), nullable=True)
    subreceta_id: Mapped[int | None] = mapped_column(ForeignKey("recetas.id", ondelete="RESTRICT"), nullable=True)
    cantidad: Mapped[float] = mapped_column(Float, default=0)
    unidad: Mapped[str] = mapped_column(String(10), default="g")
    merma_pct: Mapped[float] = mapped_column(Float, default=0)
    receta: Mapped[Receta] = relationship(back_populates="items", foreign_keys=[receta_id])


class Configuracion(Base):
    __tablename__ = "configuracion"
    clave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text)


CONFIG_DEFECTO = {
    "margen_objetivo_pct": "65",
    "iva_pct": "13",
    "aplicar_iva": "0",       # 1 = los precios de venta incluyen IVA
    "redondeo": "50",          # redondear precio sugerido hacia arriba a múltiplos de ₡50
    "nombre_negocio": "Mi negocio",
}
