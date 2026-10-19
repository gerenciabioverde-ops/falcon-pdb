"""Configuración central. Importar siempre `settings`; no leer variables de entorno directamente."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./falcon_pdb.db"
    jwt_secret_key: str = "dev-secret-cambiar"
    token_horas: int = 12


settings = Settings()
