import json
from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración base de la aplicación, leída desde variables de entorno."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Agente Inteligente"
    environment: str = "local"
    # Origins allowed to call the API from a browser (widget embedders). Empty = no CORS headers.
    # Accepts a JSON list or a comma-separated string in CORS_ALLOWED_ORIGINS.
    cors_allowed_origins: Annotated[list[str], NoDecode] = []
    cors_max_age: int = 600

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                value = json.loads(text)
            else:
                value = [part for part in text.split(",")]
        if isinstance(value, list):
            origins = [str(item).strip().rstrip("/") for item in value if str(item).strip()]
            if "*" in origins:
                raise ValueError("CORS_ALLOWED_ORIGINS no admite '*'; lista los dominios")
            return origins
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
