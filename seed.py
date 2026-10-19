"""Inicializa la BD y crea el usuario admin/admin123 (idempotente). Cambiar la clave en producción."""
from app import models  # noqa: F401
from app.core.security import hash_password
from app.database import Base, SessionLocal, engine
from app.models import Usuario

Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    if not db.query(Usuario).filter_by(username="admin").first():
        db.add(Usuario(username="admin", password_hash=hash_password("admin123")))
        db.commit()
        print("Usuario admin creado (clave: admin123).")
