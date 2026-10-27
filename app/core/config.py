from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración base de la aplicación, leída desde variables de entorno."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Agente Inteligente"
    environment: str = "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
