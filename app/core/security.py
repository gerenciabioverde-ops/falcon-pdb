"""Hash de contraseñas (scrypt) y JWT en cookie HTTP-only `pdb_token`."""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings


def hash_password(password: str) -> str:
    sal = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=sal, n=2**14, r=8, p=1)
    return sal.hex() + "$" + h.hex()


def verify_password(password: str, almacenado: str) -> bool:
    try:
        sal_hex, h_hex = almacenado.split("$")
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(sal_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(h.hex(), h_hex)
    except (ValueError, TypeError):
        return False


def crear_token(usuario_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.token_horas)
    return jwt.encode({"sub": str(usuario_id), "exp": exp}, settings.jwt_secret_key, algorithm="HS256")


def leer_token(token: str) -> int | None:
    try:
        return int(jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
