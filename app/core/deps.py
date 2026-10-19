from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.orm import Session

from app.core.security import leer_token
from app.database import get_db
from app.models import Usuario


class NoAutenticado(Exception):
    pass


def get_current_user(db: Annotated[Session, Depends(get_db)],
                     pdb_token: Annotated[str | None, Cookie()] = None) -> Usuario:
    uid = leer_token(pdb_token) if pdb_token else None
    usuario = db.get(Usuario, uid) if uid else None
    if not usuario:
        raise NoAutenticado()
    return usuario
