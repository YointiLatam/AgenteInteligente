from enum import StrEnum

import httpx

from app.integrations.settings import IntegrationsSettings

TIMEOUT = httpx.Timeout(10.0, connect=5.0)  # generic default, kept for compatibility


class Provider(StrEnum):
    META = "meta"
    WHATSAPP = "whatsapp"
    X = "x"


# Explicit per-provider timeouts (seconds). Sends are given more room to write/read.
PROVIDER_TIMEOUTS: dict[Provider, httpx.Timeout] = {
    Provider.META: httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
    Provider.WHATSAPP: httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
    Provider.X: httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
}


class ProviderNotConfigured(RuntimeError):
    pass


def _credentials(provider: Provider, settings: IntegrationsSettings) -> tuple[str, str] | None:
    """Devuelve (base_url, token) o None si faltan credenciales."""
    graph = f"https://graph.facebook.com/{settings.meta_graph_version}"
    match provider:
        case Provider.META:
            secret, base_url = settings.meta_access_token, graph
        case Provider.WHATSAPP:
            # Sin número de teléfono de prueba no hay sandbox utilizable.
            secret = settings.whatsapp_access_token if settings.whatsapp_phone_number_id else None
            base_url = graph
        case Provider.X:
            secret, base_url = settings.x_bearer_token, "https://api.x.com/2"
    return (base_url, secret.get_secret_value()) if secret else None


def is_configured(provider: Provider, settings: IntegrationsSettings) -> bool:
    return _credentials(provider, settings) is not None


def build_client(
    provider: Provider,
    settings: IntegrationsSettings,
    timeout: httpx.Timeout | None = None,
) -> httpx.AsyncClient:
    """Build the provider client with per-provider timeouts (or an explicit override)."""
    credentials = _credentials(provider, settings)
    if credentials is None:
        raise ProviderNotConfigured(f"Faltan credenciales para '{provider.value}'")
    base_url, token = credentials
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=timeout or PROVIDER_TIMEOUTS.get(provider, TIMEOUT),
        headers={"Authorization": f"Bearer {token}"},
    )
