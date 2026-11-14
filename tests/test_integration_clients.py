import httpx
import pytest
from pydantic import BaseModel

from app.integrations.clients import PROVIDER_TIMEOUTS, TIMEOUT, Provider, build_client
from app.integrations.settings import IntegrationsSettings
from app.tools.base import BaseTool


def _settings() -> IntegrationsSettings:
    return IntegrationsSettings(
        _env_file=None,
        meta_access_token="m",
        whatsapp_access_token="w",
        whatsapp_phone_number_id="1",
        x_bearer_token="x",
    )


def test_every_provider_has_explicit_timeouts():
    for provider in Provider:
        timeout = PROVIDER_TIMEOUTS[provider]
        assert None not in (timeout.connect, timeout.read, timeout.write, timeout.pool)


@pytest.mark.parametrize("provider", list(Provider))
def test_build_client_uses_the_provider_timeout(provider):
    client = build_client(provider, _settings())

    assert client.timeout == PROVIDER_TIMEOUTS[provider]


def test_explicit_timeout_overrides_the_provider_default():
    custom = httpx.Timeout(1.0)

    assert build_client(Provider.X, _settings(), custom).timeout == custom


def test_legacy_timeout_constant_is_still_importable():
    assert isinstance(TIMEOUT, httpx.Timeout)


def test_tool_timeout_override_reaches_the_client():
    class Slow(BaseTool):
        name, description = "slow", "slow"
        input_model = BaseModel
        provider = Provider.META
        timeout = httpx.Timeout(connect=1.0, read=60.0, write=1.0, pool=1.0)

        async def execute(self, args):
            return {}

    assert Slow(settings=_settings()).http_client().timeout == Slow.timeout


def test_build_client_hub_is_optional_and_static_path_unchanged():
    client = build_client(Provider.META, _settings())
    assert client.headers["Authorization"].startswith("Bearer ")
