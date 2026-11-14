import json
from contextlib import asynccontextmanager, contextmanager
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes.whatsapp import get_sender_factory
from app.conversations.repository import ConversationThread, StoredMessage
from app.conversations.service import (
    InputBlockedError,
    PendingApprovalResult,
    TurnResult,
)
from app.conversations.wiring import get_conversation_service_opener
from app.main import app
from app.whatsapp.handler import BLOCKED_NOTICE, PENDING_NOTICE, WhatsAppSender
from app.whatsapp.settings import get_whatsapp_webhook_settings
from app.whatsapp.signature import sign

SECRET = "app-secret"
VERIFY = "verify-me"
THREAD = ConversationThread(
    id=uuid4(),
    channel="whatsapp",
    external_conversation_id="5215512345678",
    langgraph_thread_id="t",
    status="active",
)


def _message(content="respuesta"):
    return StoredMessage(
        id=uuid4(),
        thread_id=THREAD.id,
        turn_id=uuid4(),
        role="agent",
        content=content,
        raw_payload={},
        idempotency_key="agent:x",
    )


class FakeService:
    """Mimics idempotency by request_id: the second call with the same id is a replay."""

    def __init__(self, mode="ok"):
        self.mode = mode
        self.requests = []
        self.seen = set()

    def process_user_turn(self, request):
        self.requests.append(request)
        replayed = request.request_id in self.seen
        self.seen.add(request.request_id)
        if self.mode == "blocked":
            raise InputBlockedError("bloqueado", "x", "v1")
        if self.mode == "boom":
            raise RuntimeError("db caída: secreto")
        if self.mode == "pending":
            return PendingApprovalResult(
                thread=THREAD, turn_id=uuid4(), tool_call={}, replayed=replayed
            )
        return TurnResult(thread=THREAD, message=_message(), replayed=replayed)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    get_whatsapp_webhook_settings.cache_clear()
    state = {"service": FakeService(), "sent": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["sent"].append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "out"}]})

    @contextmanager
    def opener():
        yield state["service"]

    @asynccontextmanager
    async def sender():
        async with httpx.AsyncClient(
            base_url="https://graph.test", transport=httpx.MockTransport(handler)
        ) as client:
            yield WhatsAppSender(client, "123")

    app.dependency_overrides[get_conversation_service_opener] = lambda: opener
    app.dependency_overrides[get_sender_factory] = lambda: sender
    yield state, TestClient(app)
    app.dependency_overrides.clear()
    get_whatsapp_webhook_settings.cache_clear()


def _payload(message_id="wamid.1", body="Hola"):
    message = {"id": message_id, "from": "5215512345678", "type": "text", "text": {"body": body}}
    return json.dumps({"entry": [{"changes": [{"value": {"messages": [message]}}]}]}).encode()


def _post(client, body, signature="auto"):
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        signature = sign(SECRET, body)
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/webhooks/whatsapp", content=body, headers=headers)


def test_challenge_handshake(env):
    _, client = env
    params = {"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "12345"}
    response = client.get("/webhooks/whatsapp", params=params)
    assert (response.status_code, response.text) == (200, "12345")


def test_challenge_rejects_bad_token_or_mode(env):
    _, client = env
    bad = {"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "1"}
    assert client.get("/webhooks/whatsapp", params=bad).status_code == 403
    bad = {"hub.mode": "other", "hub.verify_token": VERIFY, "hub.challenge": "1"}
    assert client.get("/webhooks/whatsapp", params=bad).status_code == 403


def test_valid_message_gets_one_reply(env):
    state, client = env
    assert _post(client, _payload()).status_code == 200
    [request] = state["service"].requests
    assert (request.channel, request.external_conversation_id) == ("whatsapp", "5215512345678")
    assert request.request_id == "wamid.1"
    [sent] = state["sent"]
    assert sent["to"] == "5215512345678" and sent["text"]["body"] == "respuesta"


@pytest.mark.parametrize("signature", [None, "sha256=deadbeef", "bad"])
def test_invalid_or_missing_signature_is_rejected(env, signature):
    state, client = env
    assert _post(client, _payload(), signature=signature).status_code == 401
    assert state["service"].requests == [] and state["sent"] == []


def test_duplicate_delivery_sends_single_reply(env):
    state, client = env
    _post(client, _payload())
    _post(client, _payload())
    assert len(state["service"].requests) == 2
    assert len(state["sent"]) == 1


def test_status_only_payload_is_harmless(env):
    state, client = env
    body = json.dumps({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}).encode()
    assert _post(client, body).status_code == 200
    assert state["service"].requests == []


def test_invalid_json_with_valid_signature_returns_200(env):
    _, client = env
    assert _post(client, b"not json").status_code == 200


def test_pending_approval_notice(env):
    state, client = env
    state["service"] = FakeService("pending")
    _post(client, _payload())
    assert state["sent"][0]["text"]["body"] == PENDING_NOTICE


def test_guard_blocked_input_gets_neutral_reply(env):
    state, client = env
    state["service"] = FakeService("blocked")
    _post(client, _payload())
    assert state["sent"][0]["text"]["body"] == BLOCKED_NOTICE


def test_handler_exception_is_swallowed(env):
    state, client = env
    state["service"] = FakeService("boom")
    assert _post(client, _payload()).status_code == 200
    assert state["sent"] == []


def test_503_when_secret_unset(env, monkeypatch):
    state, client = env
    monkeypatch.delenv("WHATSAPP_APP_SECRET")
    get_whatsapp_webhook_settings.cache_clear()
    body = _payload()
    assert _post(client, body, signature=sign(SECRET, body)).status_code == 503
    assert state["service"].requests == []


def test_503_on_handshake_when_verify_token_unset(env, monkeypatch):
    _, client = env
    monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN")
    get_whatsapp_webhook_settings.cache_clear()
    params = {"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"}
    assert client.get("/webhooks/whatsapp", params=params).status_code == 503
