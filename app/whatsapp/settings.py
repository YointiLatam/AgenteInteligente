from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppWebhookSettings(BaseSettings):
    """Webhook secrets. No defaults: if unset, the webhook refuses traffic."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None


@lru_cache
def get_whatsapp_webhook_settings() -> WhatsAppWebhookSettings:
    return WhatsAppWebhookSettings()
