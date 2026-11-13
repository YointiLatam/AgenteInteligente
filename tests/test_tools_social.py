import httpx
import pytest

import app.tools.base as base_module
from app.integrations.settings import IntegrationsSettings
from app.tools.audit import InMemoryAuditSink, ToolStatus
from app.tools.catalog.meta import MetaPublishPagePost, MetaReadPagePosts
from app.tools.catalog.whatsapp import WhatsAppSendMessage
from app.tools.registry import build_default_registry

pytestmark = pytest.mark.anyio

SETTINGS = IntegrationsSettings(
    _env_file=None,
    meta_access_token="meta-token",
    whatsapp_access_token="wa-token",
    whatsapp_phone_number_id="555",
)


@pytest.fixture
def graph(monkeypatch):
    """Sustituye la API de Meta por un servidor simulado y registra las peticiones."""
    monkeypatch.setenv("META_PAGE_ID", "page1")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        if request.url.path.endswith("/feed"):
            return httpx.Response(200, json={"id": "page1_99"})
        return httpx.Response(200, json={"data": [{"id": "1", "message": "hola"}]})

    def fake_build_client(provider, settings):
        return httpx.AsyncClient(
            base_url="https://graph.test/v1/", transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(base_module, "build_client", fake_build_client)
    return seen


async def test_lee_publicaciones_de_la_pagina(graph):
    tool = MetaReadPagePosts(settings=SETTINGS)

    result = await tool.run({"limit": 3})

    assert result.data == {"posts": [{"id": "1", "message": "hola"}]}
    assert graph[0].url.path.endswith("/page1/posts")
    assert graph[0].url.params["limit"] == "3"


async def test_publicar_exige_aprobacion_y_no_llama_a_meta(graph):
    sink = InMemoryAuditSink()
    tool = MetaPublishPagePost(settings=SETTINGS, audit=sink)

    result = await tool.run({"message": "Oferta"})

    assert result.status == ToolStatus.PENDING_APPROVAL
    assert graph == []
    assert sink.entries[0].status == ToolStatus.PENDING_APPROVAL


async def test_publicar_aprobado_llama_a_feed(graph):
    result = await MetaPublishPagePost(settings=SETTINGS).run({"message": "Oferta"}, approved=True)

    assert result.data == {"post_id": "page1_99"}
    assert graph[0].method == "POST"
    assert b"message=Oferta" in graph[0].content


async def test_whatsapp_envia_el_mensaje_solo_con_aprobacion(graph):
    tool = WhatsAppSendMessage(settings=SETTINGS)
    args = {"to": "593999111222", "body": "Hola"}

    assert (await tool.run(args)).status == ToolStatus.PENDING_APPROVAL
    assert graph == []

    result = await tool.run(args, approved=True)

    assert result.data == {"message_id": "wamid.1"}
    assert graph[0].url.path.endswith("/555/messages")
    assert b'"to":"593999111222"' in graph[0].content.replace(b" ", b"")


@pytest.mark.parametrize("to", ["+593999111222", "abc", "123"])
async def test_whatsapp_rechaza_telefonos_invalidos(graph, to):
    result = await WhatsAppSendMessage(settings=SETTINGS).run({"to": to, "body": "x"})

    assert result.error.code == "invalid_arguments"


async def test_sin_pagina_configurada_se_informa_sin_llamar_a_meta(graph, monkeypatch):
    monkeypatch.delenv("META_PAGE_ID")

    result = await MetaReadPagePosts(settings=SETTINGS).run({})

    assert result.error.code == "not_configured"
    assert graph == []


async def test_error_de_la_api_se_audita_como_fallido(monkeypatch):
    monkeypatch.setenv("META_PAGE_ID", "page1")
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    monkeypatch.setattr(
        base_module,
        "build_client",
        lambda p, s: httpx.AsyncClient(base_url="https://g.test/", transport=transport),
    )
    sink = InMemoryAuditSink()

    result = await MetaReadPagePosts(settings=SETTINGS, audit=sink).run({})

    assert (result.error.code, result.error.retryable) == ("upstream_error", True)
    assert sink.entries[0].status == ToolStatus.FAILED


def test_quedan_registradas_y_marcadas_como_criticas():
    registry = build_default_registry()

    assert {"meta_read_page_posts", "meta_publish_page_post", "whatsapp_send_message"} <= {
        d["name"] for d in registry.declarations()
    }
    assert registry.get("whatsapp_send_message").critical
    assert registry.get("meta_publish_page_post").critical
    assert not registry.get("meta_read_page_posts").critical
