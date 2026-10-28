from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """Configuración de autenticación (variables `AUTH_*`)."""

    model_config = SettingsConfigDict(env_prefix="AUTH_", env_file=".env", extra="ignore")

    jwt_secret: SecretStr = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = Field(default=15, gt=0)
    # JSON {"cliente": "<sha256 hex de la API key>"}. Nunca se guarda la clave en claro.
    api_keys: dict[str, str] = Field(default_factory=dict)


@lru_cache
def get_auth_settings() -> AuthSettings:
    return AuthSettings()
