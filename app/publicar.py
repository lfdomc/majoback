"""Publica el menú para clientes como archivos estáticos en Cloudflare R2.

Así el menú se ve aunque el backend esté dormido: el sitio lee menu.json y las
imágenes directo de R2, y el backend solo trabaja cuando alguien hace cambios.

Variables de entorno (si falta alguna, la publicación queda apagada):
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET, R2_PUBLIC_URL
"""
import json
import os
import threading
from datetime import datetime, timezone

from .imagenes import MEDIA_DIR

VARIABLES = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_URL")
TIPOS = {"webp": "image/webp", "png": "image/png", "jpg": "image/jpeg"}

estado = {"ultima": None, "error": None, "imagenes": 0}
_lock = threading.Lock()
_timer: threading.Timer | None = None
_subidas: set[str] | None = None  # imágenes que ya están en R2
_cliente = None


def _v(k: str) -> str:
    return (os.getenv(k) or "").strip()


def activo() -> bool:
    return all(_v(k) for k in VARIABLES)


def info() -> dict:
    return {"activo": activo(), "url": (_v("R2_PUBLIC_URL").rstrip("/") + "/menu.json") if activo() else None, **estado}


def _s3():
    global _cliente
    if _cliente is None:
        import boto3
        from botocore.config import Config
        _cliente = boto3.client(
            "s3", endpoint_url=_v("R2_ENDPOINT") or f"https://{_v('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
            aws_access_key_id=_v("R2_ACCESS_KEY_ID"), aws_secret_access_key=_v("R2_SECRET_ACCESS_KEY"),
            region_name="auto", config=Config(retries={"max_attempts": 3}, connect_timeout=10, read_timeout=30))
    return _cliente


def _ya_subidas(s3, bucket) -> set[str]:
    global _subidas
    if _subidas is None:
        _subidas = set()
        for pagina in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="media/"):
            _subidas.update(o["Key"][len("media/"):] for o in pagina.get("Contents", []))
    return _subidas


def publicar_ahora(armar_menu) -> dict:
    """Arma el menú, sube las imágenes nuevas y reemplaza menu.json. armar_menu() devuelve el dict del menú."""
    if not activo():
        return info()
    with _lock:
        try:
            s3, bucket = _s3(), _v("R2_BUCKET")
            base = _v("R2_PUBLIC_URL").rstrip("/")
            menu = armar_menu()
            subidas = _ya_subidas(s3, bucket)

            def a_r2(ruta):
                if not ruta or not ruta.startswith("/media/"):
                    return ruta
                nombre = ruta[len("/media/"):]
                if nombre not in subidas:
                    archivo = os.path.join(MEDIA_DIR, nombre)
                    if not os.path.exists(archivo):
                        return None
                    s3.upload_file(archivo, bucket, f"media/{nombre}", ExtraArgs={
                        "ContentType": TIPOS.get(nombre.rsplit(".", 1)[-1], "application/octet-stream"),
                        "CacheControl": "public, max-age=31536000, immutable"})
                    subidas.add(nombre)
                return f"{base}/media/{nombre}"

            menu["negocio"]["logo"] = a_r2(menu["negocio"].get("logo"))
            n = 0
            for c in menu["categorias"]:
                for p in c["productos"]:
                    p["foto"] = a_r2(p.get("foto"))
                    if p.get("foto_en"):
                        p["foto_en"] = a_r2(p["foto_en"])
                    n += 1 if p["foto"] else 0
            menu["publicado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            s3.put_object(Bucket=bucket, Key="menu.json", Body=json.dumps(menu, ensure_ascii=False).encode("utf-8"),
                          ContentType="application/json; charset=utf-8", CacheControl="public, max-age=30")
            estado.update(ultima=menu["publicado"], error=None, imagenes=n)
        except Exception as e:  # nunca debe tumbar el backend
            estado["error"] = f"{type(e).__name__}: {e}"[:300]
            print("No se pudo publicar el menú en R2:", estado["error"])
    return info()


def programar(armar_menu, retraso: float = 3.0):
    """Publica unos segundos después del último cambio (agrupa varios guardados seguidos)."""
    global _timer
    if not activo():
        return
    if _timer is not None:
        _timer.cancel()
    _timer = threading.Timer(retraso, publicar_ahora, args=(armar_menu,))
    _timer.daemon = True
    _timer.start()
