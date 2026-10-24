"""Guardado de fotos de productos y logo en el disco (volumen de Railway)."""
import io
import os
import uuid

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

from .db import DB_PATH

MEDIA_DIR = os.getenv("MEDIA_DIR", os.path.join(os.path.dirname(DB_PATH), "imagenes"))
os.makedirs(MEDIA_DIR, exist_ok=True)
MAX_BYTES = 12 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 60_000_000


def guardar_imagen(datos: bytes, max_lado: int, transparencia: bool = False) -> str:
    """Valida, endereza, reduce y guarda como WebP. Devuelve el nombre del archivo."""
    if not datos:
        raise HTTPException(400, "El archivo está vacío.")
    if len(datos) > MAX_BYTES:
        raise HTTPException(413, "La imagen pesa más de 12 MB. Use una más liviana.")
    try:
        img = Image.open(io.BytesIO(datos))
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, "No se reconoce el archivo como imagen. Use JPG, PNG o WebP.")
    if transparencia and img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
    else:
        img = img.convert("RGB")
    img.thumbnail((max_lado, max_lado), Image.LANCZOS)
    nombre = f"{uuid.uuid4().hex}.webp"
    img.save(os.path.join(MEDIA_DIR, nombre), "WEBP", quality=82, method=4)
    return nombre


def borrar_imagen(nombre: str | None):
    if not nombre:
        return
    ruta = os.path.join(MEDIA_DIR, os.path.basename(nombre))
    try:
        os.remove(ruta)
    except FileNotFoundError:
        pass


def abrir(nombre: str) -> Image.Image:
    return Image.open(os.path.join(MEDIA_DIR, os.path.basename(nombre)))


def guardar_pil(img: Image.Image, formato: str = "WEBP", **opciones) -> str:
    ext = {"WEBP": "webp", "JPEG": "jpg", "PNG": "png"}[formato]
    nombre = f"{uuid.uuid4().hex}.{ext}"
    img.save(os.path.join(MEDIA_DIR, nombre), formato, **opciones)
    return nombre
