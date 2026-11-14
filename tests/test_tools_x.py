import httpx
import pytest

import app.tools.base as base_module
from app.integrations.settings import IntegrationsSettings
from app.tools.audit import InMemoryAuditSink, ToolStatus
from app.tools.catalog.x import XPublishPost, XReadRecentPosts
from app.tools.registry import build_default_registry

pytestmark = pytest.mark.anyio

SETTINGS = IntegrationsSettings(_env_file=None, x_bearer_token="x-token", x_user_id="42")


@pytest.fixture
def x_api(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"id": "999", "text": "hola"}})
        return httpx.Response(
            200,
            json={"data": [{"id": "1", "text": "a", "created_at": "t", "edit_history": [1]}]},
        )

    def fake_build_client(provider, settings):
        return httpx.AsyncClient(
            base_url="https://x.test/2", transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(base_module, "build_client", fake_build_client)
    return seen


async def test_reads_recent_posts_with_trimmed_fields(x_api):
    result = await XReadRecentPosts(settings=SETTINGS).run({"max_results": 7})

    assert result.data == {"posts": [{"id": "1", "text": "a", "created_at": "t"}]}
    assert x_api[0].url.path.endswith("/users/42/tweets")
    assert x_api[0].url.params["max_results"] == "7"


async def test_read_uses_safe_default(x_api):
    await XReadRecentPosts(settings=SETTINGS).run({})
    assert x_api[0].url.params["max_results"] == "10"


@pytest.mark.parametrize("value", [4, 101])
async def test_read_rejects_out_of_range(x_api, value):
    result = await XReadRecentPosts(settings=SETTINGS).run({"max_results": value})
    assert result.error.code == "invalid_arguments"
    assert x_api == []


async def test_read_without_user_id_is_not_configured(x_api, monkeypatch):
    monkeypatch.delenv("X_USER_ID", raising=False)
    settings = IntegrationsSettings(_env_file=None, x_bearer_token="t")
    result = await XReadRecentPosts(settings=settings).run({})
    assert result.error.code == "not_configured"


@pytest.mark.parametrize("text", ["", "x" * 281])
async def test_publish_validates_text_length(x_api, text):
    result = await XPublishPost(settings=SETTINGS).run({"text": text}, approved=True)
    assert result.error.code == "invalid_arguments"
    assert x_api == []


async def test_publish_without_approval_makes_no_http_call(x_api):
    sink = InMemoryAuditSink()
    result = await XPublishPost(settings=SETTINGS, audit=sink).run({"text": "hola"})

    assert result.status == ToolStatus.PENDING_APPROVAL
    assert x_api == []
    assert sink.entries[0].status == ToolStatus.PENDING_APPROVAL


async def test_publish_with_approval_posts_to_x(x_api):
    result = await XPublishPost(settings=SETTINGS).run({"text": "hola"}, approved=True)

    assert result.status == ToolStatus.EXECUTED
    assert result.data == {"post_id": "999"}
    assert x_api[0].method == "POST" and x_api[0].url.path.endswith("/tweets")
    assert x_api[0].read() == b'{"text":"hola"}'


async def test_publish_is_not_retried_after_a_possible_send(monkeypatch):
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503, json={})

    monkeypatch.setattr(
        base_module,
        "build_client",
        lambda provider, settings: httpx.AsyncClient(
            base_url="https://x.test/2", transport=httpx.MockTransport(handler)
        ),
    )
    result = await XPublishPost(settings=SETTINGS, sleep=_no_sleep).run(
        {"text": "hola"}, approved=True
    )
    assert result.status == ToolStatus.FAILED
    assert len(calls) == 1


async def _no_sleep(_: float) -> None:
    return None


def test_catalog_auto_discovery_includes_x_tools():
    registry = build_default_registry(settings=SETTINGS)
    assert registry.get("x_read_recent_posts") is not None
    assert registry.get("x_publish_post").critical is True


async def test_tool_uses_hub_when_oauth_credentials_are_configured():
    from app.integrations.oauth import TokenHub

    def token_endpoint(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": "fresh", "refresh_token": "r2", "expires_in": 7200}
        )

    settings = IntegrationsSettings(
        _env_file=None,
        x_client_id="cid",
        x_client_secret="sec",
        x_refresh_token="r1",
        x_user_id="42",
    )
    tool = XReadRecentPosts(settings=settings)
    tool._hub = TokenHub(settings, transport=httpx.MockTransport(token_endpoint))

    client = tool.http_client()
    client._transport = httpx.MockTransport(  # noqa: SLF001
        lambda r: httpx.Response(200, json={"auth": r.headers["Authorization"]})
    )
    async with client:
        response = await client.get("/users/42/tweets")

    assert response.json() == {"auth": "Bearer fresh"}
