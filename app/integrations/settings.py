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


@lru_cache
def get_integrations_settings() -> IntegrationsSettings:
    return IntegrationsSettings()
