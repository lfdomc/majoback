from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .units import UNIDADES

Unidad = str


def _unidad(v):
    if v not in UNIDADES:
        raise ValueError(f"Unidad no válida: {v}")
    return v


class Login(BaseModel):
    usuario: str
    clave: str


class CambioClave(BaseModel):
    clave_actual: str
    clave_nueva: str = Field(min_length=6)


class UsuarioIn(BaseModel):
    usuario: str = Field(min_length=3, max_length=60)
    nombre: str = ""
    clave: str | None = Field(default=None, min_length=6)
    es_admin: bool = False
    activo: bool = True


class ProveedorIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=120)
    contacto: str = ""
    notas: str = ""


class PresentacionIn(BaseModel):
    id: int | None = None
    proveedor_id: int | None = None
    descripcion: str = ""
    precio: float = Field(ge=0)
    cantidad: float = Field(gt=0)
    unidad: Unidad
    merma_pct: float = Field(default=0, ge=0, lt=100)
    preferida: bool = False
    fecha: date | None = None
    @field_validator("unidad")
    @classmethod
    def valida_unidad(cls, v):
        return _unidad(v)


class IngredienteIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=160)
    categoria: str = "General"
    unidad_base: Literal["g", "ml", "unidad"] = "g"
    peso_unidad_g: float | None = Field(default=None, gt=0)
    densidad_g_ml: float | None = Field(default=None, gt=0)
    base_costo: Literal["preferida", "menor", "promedio", "mayor"] = "preferida"
    notas: str = ""
    presentaciones: list[PresentacionIn] = []


class ItemIn(BaseModel):
    ingrediente_id: int | None = None
    subreceta_id: int | None = None
    cantidad: float = Field(ge=0)
    unidad: Unidad
    merma_pct: float = Field(default=0, ge=0, lt=100)
    @field_validator("unidad")
    @classmethod
    def valida_unidad(cls, v):
        return _unidad(v)


class RecetaIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=160)
    tipo: Literal["producto", "subreceta"] = "producto"
    categoria: str = "General"
    rinde_cantidad: float = Field(gt=0, default=1)
    rinde_unidad: Unidad = "porcion"
    merma_pct: float = Field(default=0, ge=0, lt=100)
    margen_objetivo_pct: float | None = Field(default=None, ge=0, lt=100)
    precio_venta: float | None = Field(default=None, ge=0)
    precio_competencia: float | None = Field(default=None, ge=0)
    ventas_mes: float | None = Field(default=None, ge=0, le=1_000_000)
    notas: str = ""
    descripcion_menu: str = Field(default="", max_length=600)
    nombre_en: str = Field(default="", max_length=160)
    descripcion_menu_en: str = Field(default="", max_length=600)
    en_menu: bool = False
    items: list[ItemIn] = []
    @field_validator("rinde_unidad")
    @classmethod
    def valida_rinde(cls, v):
        return _unidad(v)


class PreciosIn(BaseModel):
    precio_venta: float | None = Field(default=None, ge=0)
    precio_competencia: float | None = Field(default=None, ge=0)


class RecetaBorrador(RecetaIn):
    id: int | None = None
    nombre: str = "Borrador"


class ConfigIn(BaseModel):
    margen_objetivo_pct: float = Field(ge=0, lt=100)
    iva_pct: float = Field(ge=0, le=100)
    aplicar_iva: bool
    redondeo: float = Field(ge=0)
    nombre_negocio: str = ""


class NegocioIn(BaseModel):
    nombre: str = Field(default="", max_length=120)
    eslogan: str = Field(default="", max_length=160)
    eslogan_en: str = Field(default="", max_length=160)
    descripcion_en: str = Field(default="", max_length=800)
    horario_en: str = Field(default="", max_length=300)
    lugar: str = Field(default="", max_length=120)
    descripcion: str = Field(default="", max_length=800)
    telefono: str = Field(default="", max_length=40)
    whatsapp: str = Field(default="", max_length=40)
    sinpe: str = Field(default="", max_length=40)          # número para SINPE Móvil (se muestra al cobrar)
    sinpe_nombre: str = Field(default="", max_length=120)  # a nombre de quién está
    # Datos tributarios para el comprobante (no se publican en el menú)
    regimen: Literal["simplificado", "tradicional", "ninguno"] = "simplificado"
    contribuyente: str = Field(default="", max_length=160)       # nombre o razón social ante Hacienda
    cedula_tipo: Literal["fisica", "juridica", "dimex", "nite"] = "fisica"
    cedula: str = Field(default="", max_length=20)
    actividad: str = Field(default="", max_length=160)           # actividad económica (código y descripción)
    direccion_fiscal: str = Field(default="", max_length=300)
    leyenda_comprobante: str = Field(default="", max_length=400)  # vacío = la leyenda del régimen
    email: str = Field(default="", max_length=120)
    direccion: str = Field(default="", max_length=300)
    mapa: str = Field(default="", max_length=500)
    horario: str = Field(default="", max_length=300)
    instagram: str = Field(default="", max_length=200)
    facebook: str = Field(default="", max_length=200)
    tiktok: str = Field(default="", max_length=200)
    sitio_web: str = Field(default="", max_length=200)
    color: str = Field(default="#D94C72", pattern=r"^#[0-9A-Fa-f]{6}$")
    mostrar_precios: bool = True
    distintivo: Literal["logo", "gorro", "ninguno"] = "logo"  # qué se muestra como logo


class PedidoItem(BaseModel):
    receta_id: int
    cantidad: float = Field(gt=0, le=1_000_000)


class ComprasIn(BaseModel):
    items: list[PedidoItem] = Field(default_factory=list, max_length=200)
