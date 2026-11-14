from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class IntegrationsSettings(BaseSettings):
    """Credenciales de APIs externas. Todo secreto es `SecretStr`: no se imprime en logs."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Mientras sea True, las herramientas deben usar apps de desarrollo y números de prueba.
    sandbox: bool = Field(default=True, validation_alias="INTEGRATIONS_SANDBOX")

    meta_graph_version: str = "v21.0"
    meta_access_token: SecretStr | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    x_bearer_token: SecretStr | None = None
    # OAuth2 "Tool Hub" (all optional; the hub is opt-in).
    x_client_id: str | None = None
    x_client_secret: SecretStr | None = None
    x_refresh_token: SecretStr | None = None
    x_access_token_expires_at: float | None = None  # epoch seconds
    x_user_id: str | None = None
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    # Fernet key that encrypts the OAuth tokens kept in PostgreSQL (never stored in the database).
    token_encryption_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    gemini_primary_model: str = "gemini-3.5-flash"
    gemini_fallback_model: str = "gemini-3.8-flash"
    gemini_fallback_enabled: bool = False
    gemini_fallback_max_attempts: int = Field(default=1, ge=0, le=1)
    gemini_fallback_input_token_limit: int = Field(default=8_000, ge=1)
    gemini_fallback_max_output_tokens: int = Field(default=2_048, ge=1)


@lru_cache
def get_integrations_settings() -> IntegrationsSettings:
    return IntegrationsSettings()
