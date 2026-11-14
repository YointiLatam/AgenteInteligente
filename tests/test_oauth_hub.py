import asyncio

import httpx
import pytest

from app.integrations.clients import Provider, ProviderNotConfigured, build_client
from app.integrations.oauth import (
    InMemoryTokenStore,
    TokenHub,
    TokenSet,
    get_token_hub,
    set_token_hub,
)
from app.integrations.settings import IntegrationsSettings
from app.tools.base import ToolError

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_settings(**kwargs) -> IntegrationsSettings:
    return IntegrationsSettings(_env_file=None, **kwargs)


X_ENV = {
    "x_bearer_token": "x-old",
    "x_client_id": "cid",
    "x_client_secret": "csecret",
    "x_refresh_token": "r-old",
    "x_access_token_expires_at": 1030.0,
}


def x_transport(calls: list, *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "invalid_grant"})
        n = len(calls)
        return httpx.Response(
            200,
            json={"access_token": f"x-new{n}", "refresh_token": f"r-new{n}", "expires_in": 7200},
        )

    return httpx.MockTransport(handler)


async def test_refreshes_when_close_to_expiry():
    calls: list = []
    hub = TokenHub(make_settings(**X_ENV), clock=Clock(), transport=x_transport(calls))

    token = await hub.get_token(Provider.X)

    assert token == "x-new1"
    assert len(calls) == 1
    assert str(calls[0].url) == "https://api.x.com/2/oauth2/token"
    assert calls[0].headers["Authorization"].startswith("Basic ")
    assert b"grant_type=refresh_token" in calls[0].content
    assert b"refresh_token=r-old" in calls[0].content


async def test_no_refresh_when_token_is_valid():
    calls: list = []
    env = {**X_ENV, "x_access_token_expires_at": 5000.0}
    hub = TokenHub(make_settings(**env), clock=Clock(), transport=x_transport(calls))

    assert await hub.get_token(Provider.X) == "x-old"
    assert calls == []


async def test_single_refresh_under_concurrency():
    calls: list = []
    hub = TokenHub(make_settings(**X_ENV), clock=Clock(), transport=x_transport(calls))

    tokens = await asyncio.gather(*(hub.get_token(Provider.X) for _ in range(10)))

    assert set(tokens) == {"x-new1"}
    assert len(calls) == 1


async def test_rotated_x_refresh_token_is_stored_and_used_next_time():
    calls: list = []
    clock = Clock()
    store = InMemoryTokenStore()
    hub = TokenHub(make_settings(**X_ENV), store=store, clock=clock, transport=x_transport(calls))

    await hub.get_token(Provider.X)
    saved = await store.get(Provider.X)
    assert saved is not None and saved.refresh_token == "r-new1"

    clock.now += 7200  # expire the new token
    await hub.get_token(Provider.X)
    assert b"refresh_token=r-new1" in calls[1].content


async def test_x_without_access_token_refreshes_on_first_use():
    calls: list = []
    env = {
        k: v for k, v in X_ENV.items() if k not in ("x_bearer_token", "x_access_token_expires_at")
    }
    hub = TokenHub(make_settings(**env), clock=Clock(), transport=x_transport(calls))

    assert await hub.get_token(Provider.X) == "x-new1"


async def test_refresh_failure_does_not_leak_secrets(caplog):
    calls: list = []
    hub = TokenHub(make_settings(**X_ENV), clock=Clock(), transport=x_transport(calls, status=400))

    with pytest.raises(ToolError) as info:
        await hub.get_token(Provider.X)

    assert info.value.code == "upstream_error"
    text = f"{info.value.message} {caplog.text} {info.value!r}"
    for secret in ("csecret", "r-old", "x-old"):
        assert secret not in text


async def test_network_failure_does_not_leak_secrets():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed " + str(request.url))

    hub = TokenHub(make_settings(**X_ENV), clock=Clock(), transport=httpx.MockTransport(boom))
    with pytest.raises(ToolError) as info:
        await hub.get_token(Provider.X)
    assert info.value.code == "upstream_error"
    assert "r-old" not in info.value.message


async def test_meta_exchange_when_app_credentials_are_configured():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "meta-long", "expires_in": 5_000_000})

    settings = make_settings(
        meta_access_token="meta-short", meta_app_id="app", meta_app_secret="sec"
    )
    hub = TokenHub(settings, clock=Clock(), transport=httpx.MockTransport(handler))

    assert await hub.get_token(Provider.META, force=True) == "meta-long"
    assert seen[0].url.path.endswith("/oauth/access_token")
    assert seen[0].url.params["grant_type"] == "fb_exchange_token"
    assert seen[0].url.params["fb_exchange_token"] == "meta-short"
    # Valid now: no second exchange.
    assert await hub.get_token(Provider.META) == "meta-long"
    assert len(seen) == 1


async def test_static_token_fallback_without_app_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no network expected")

    settings = make_settings(
        meta_access_token="meta-static", whatsapp_access_token="wa", whatsapp_phone_number_id="1"
    )
    hub = TokenHub(settings, transport=httpx.MockTransport(handler))

    assert await hub.get_token(Provider.META, force=True) == "meta-static"
    assert await hub.get_token(Provider.WHATSAPP) == "wa"


async def test_not_configured_provider():
    hub = TokenHub(make_settings())
    with pytest.raises(ToolError) as info:
        await hub.get_token(Provider.X)
    assert info.value.code == "not_configured"


async def test_hub_auth_retries_once_after_401_with_fresh_token():
    refresh_calls: list = []
    hub = TokenHub(
        make_settings(**{**X_ENV, "x_access_token_expires_at": 9999.0}),
        clock=Clock(),
        transport=x_transport(refresh_calls),
    )
    api_calls: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        api_calls.append(request.headers["Authorization"])
        ok = request.headers["Authorization"] == "Bearer x-new1"
        return httpx.Response(200 if ok else 401, json={})

    client = build_client(Provider.X, make_settings(**X_ENV), hub=hub)
    client._transport = httpx.MockTransport(api)  # noqa: SLF001 - swap network for the fake
    async with client:
        response = await client.get("/users/1/tweets")

    assert response.status_code == 200
    assert api_calls == ["Bearer x-old", "Bearer x-new1"]
    assert len(refresh_calls) == 1


async def test_hub_auth_does_not_loop_on_persistent_401():
    refresh_calls: list = []
    hub = TokenHub(
        make_settings(**{**X_ENV, "x_access_token_expires_at": 9999.0}),
        clock=Clock(),
        transport=x_transport(refresh_calls),
    )
    api_calls: list = []

    def api(request: httpx.Request) -> httpx.Response:
        api_calls.append(request)
        return httpx.Response(401, json={})

    client = build_client(Provider.X, make_settings(**X_ENV), hub=hub)
    client._transport = httpx.MockTransport(api)  # noqa: SLF001
    async with client:
        response = await client.get("/x")

    assert response.status_code == 401
    assert len(api_calls) == 2 and len(refresh_calls) == 1


def test_build_client_with_unconfigured_hub_raises():
    hub = TokenHub(make_settings())
    with pytest.raises(ProviderNotConfigured):
        build_client(Provider.X, make_settings(), hub=hub)


def test_build_client_without_hub_keeps_static_header():
    client = build_client(Provider.X, make_settings(x_bearer_token="tok"))
    assert client.headers["Authorization"] == "Bearer tok"


def test_token_hub_singleton_is_overridable():
    custom = TokenHub(make_settings())
    set_token_hub(custom)
    try:
        assert get_token_hub() is custom
    finally:
        set_token_hub(None)


def test_token_set_repr_hides_secrets():
    assert "secret" not in repr(TokenSet("secret-a", "secret-r", 1.0))
