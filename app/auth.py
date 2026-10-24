import os
import secrets
import time
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .db import get_db

SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)
if not os.getenv("SECRET_KEY"):
    print("AVISO: SECRET_KEY no definido; las sesiones se cerrarán al reiniciar el servidor.")
ALGORITMO = "HS256"
HORAS_SESION = int(os.getenv("HORAS_SESION", "168"))

bearer = HTTPBearer(auto_error=False)


def hash_clave(clave: str) -> str:
    return bcrypt.hashpw(clave.encode()[:72], bcrypt.gensalt()).decode()


def verificar_clave(clave: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(clave.encode()[:72], h.encode())
    except ValueError:
        return False


def crear_token(u: models.Usuario) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=HORAS_SESION)
    return jwt.encode({"sub": str(u.id), "exp": exp}, SECRET_KEY, algorithm=ALGORITMO)


def usuario_actual(cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                   db: Session = Depends(get_db)) -> models.Usuario:
    no = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión vencida o inválida. Vuelva a ingresar.")
    if not cred:
        raise no
    try:
        data = jwt.decode(cred.credentials, SECRET_KEY, algorithms=[ALGORITMO])
    except jwt.PyJWTError:
        raise no
    u = db.get(models.Usuario, int(data["sub"]))
    if not u or not u.activo:
        raise no
    return u


def solo_admin(u: models.Usuario = Depends(usuario_actual)) -> models.Usuario:
    if not u.es_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Solo un administrador puede hacer esto.")
    return u


# Límite simple de intentos fallidos (por usuario+IP)
_fallos: dict[str, list[float]] = {}
VENTANA, MAX_FALLOS = 900, 8


def revisar_bloqueo(clave: str):
    ahora = time.time()
    lista = [t for t in _fallos.get(clave, []) if ahora - t < VENTANA]
    _fallos[clave] = lista
    if len(lista) >= MAX_FALLOS:
        raise HTTPException(429, "Demasiados intentos fallidos. Espere 15 minutos.")


def registrar_fallo(clave: str):
    _fallos.setdefault(clave, []).append(time.time())
