import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token, hash_api_key
from app.auth.settings import get_auth_settings
from app.chat.streaming import get_streamer
from app.conversations.service import (
    ToolDecisionConflictError,
    ToolDecisionOutOfOrderError,
    ToolExecutionFailedError,
)
from app.conversations.wiring import ServiceNotConfigured, get_conversation_service_opener
from app.main import app

API_KEY = "clave-de-prueba"
THREAD_ID = str(uuid4())
TURN_ID = str(uuid4())


class FakeService:
    def __init__(self, error=None):
        self.error = error
        self.requests = []
        self.decided = {}

    def decide_tool_call(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        # Same call_id again returns the stored decision (idempotent replay).
        stored = self.decided.setdefault(request.call_id, request.decision)
        return SimpleNamespace(
            execution=SimpleNamespace(call_id=request.call_id, decision=stored),
            replayed=stored is not request.decision,
        )


class FakeStreamer:
    async def stream(self, prompt):
        yield prompt


def _install(service=None, not_configured=False):
    @contextmanager
    def opener():
        if not_configured:
            raise ServiceNotConfigured("falta DATABASE_URL")
        yield service

    app.dependency_overrides[get_conversation_service_opener] = lambda: opener


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("AUTH_API_KEYS", json.dumps({"demo": hash_api_key(API_KEY)}))
    get_auth_settings.cache_clear()
    app.dependency_overrides[get_streamer] = FakeStreamer
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_auth_settings.cache_clear()


def _body(decision="approved", **extra):
    return {
        "type": "approval",
        "thread_id": THREAD_ID,
        "turn_id": TURN_ID,
        "call_id": "call-1",
        "tool_name": "send_whatsapp",
        "decision": decision,
        **extra,
    }


HEADERS = {"X-API-Key": API_KEY}


def test_approved_returns_executed(client):
    service = FakeService()
    _install(service)
    response = client.post("/chat/approval", json=_body(), headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"call_id": "call-1", "status": "executed"}
    request = service.requests[0]
    assert (str(request.thread_id), str(request.turn_id)) == (THREAD_ID, TURN_ID)
    assert (request.call_id, request.tool_name, request.decision) == (
        "call-1",
        "send_whatsapp",
        "approved",
    )
    assert request.decision_id


def test_rejected_returns_rejected_with_reason(client):
    service = FakeService()
    _install(service)
    response = client.post(
        "/chat/approval", json=_body("rejected", reason="no es lo que pedí"), headers=HEADERS
    )
    assert response.json() == {"call_id": "call-1", "status": "rejected"}
    assert service.requests[0].reason == "no es lo que pedí"


def test_repeat_is_idempotent_and_reuses_the_decision_id(client):
    service = FakeService()
    _install(service)
    first = client.post("/chat/approval", json=_body(), headers=HEADERS)
    second = client.post("/chat/approval", json=_body(), headers=HEADERS)
    assert first.json() == second.json()
    assert service.requests[0].decision_id == service.requests[1].decision_id


def test_client_decision_id_is_respected(client):
    service = FakeService()
    _install(service)
    client.post("/chat/approval", json=_body(decision_id="d-1"), headers=HEADERS)
    assert service.requests[0].decision_id == "d-1"


@pytest.mark.parametrize(
    "error", [ToolDecisionOutOfOrderError("x"), ToolDecisionConflictError("x")]
)
def test_wrong_state_is_409(client, error):
    _install(FakeService(error=error))
    response = client.post("/chat/approval", json=_body(), headers=HEADERS)
    assert response.status_code == 409


def test_failed_execution_is_502_without_leaking_details(client):
    _install(FakeService(error=ToolExecutionFailedError("secreto-interno")))
    response = client.post("/chat/approval", json=_body(), headers=HEADERS)
    assert response.status_code == 502
    assert "secreto-interno" not in response.text


def test_unexpected_error_is_500_without_leaking_details(client):
    _install(FakeService(error=RuntimeError("secreto-interno")))
    response = client.post("/chat/approval", json=_body(), headers=HEADERS)
    assert response.status_code == 500
    assert "secreto-interno" not in response.text


@pytest.mark.parametrize(
    "mutation",
    [
        {"decision": "maybe"},
        {"thread_id": "no-es-uuid"},
        {"call_id": ""},
        {"turn_id": None},
    ],
)
def test_invalid_payload_is_422(client, mutation):
    service = FakeService()
    _install(service)
    response = client.post("/chat/approval", json={**_body(), **mutation}, headers=HEADERS)
    assert response.status_code == 422
    assert service.requests == []


def test_service_not_configured_is_503(client):
    _install(not_configured=True)
    response = client.post("/chat/approval", json=_body(), headers=HEADERS)
    assert response.status_code == 503


def test_auth_is_required_and_service_is_not_opened(client):
    service = FakeService()
    _install(service)
    assert client.post("/chat/approval", json=_body()).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.post("/chat/approval", json=_body(), headers=bad).status_code == 401
    assert service.requests == []


def test_approved_by_comes_from_auth_not_payload(client):
    service = FakeService()
    _install(service)
    client.post("/chat/approval", json=_body(approved_by="intruso"), headers=HEADERS)
    assert service.requests[0].approved_by == "demo"
    token = create_access_token("demo", get_auth_settings())
    client.post(
        "/chat/approval",
        json=_body(approved_by="otro"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert service.requests[1].approved_by == "demo"


def _ws_url():
    return "/chat/ws?token=" + create_access_token("demo", get_auth_settings())


def test_ws_approval_frame_returns_approval_result(client):
    service = FakeService()
    _install(service)
    with client.websocket_connect(_ws_url()) as ws:
        ws.send_json(_body("rejected"))
        assert ws.receive_json() == {
            "type": "approval_result",
            "call_id": "call-1",
            "status": "rejected",
        }
    assert service.requests[0].approved_by == "demo"


def test_ws_bad_frames_keep_the_socket_open(client):
    _install(FakeService(error=ToolDecisionOutOfOrderError("x")))
    with client.websocket_connect(_ws_url()) as ws:
        ws.send_json({"type": "approval", "decision": "approved"})
        assert ws.receive_json()["type"] == "error"
        ws.send_json(_body())
        error = ws.receive_json()
        assert (error["type"], error["status"]) == ("error", 409)
        ws.send_text("no es json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"prompt": "hola"})  # the chat still works afterwards
        assert ws.receive_json() == {"type": "token", "text": "hola"}
        assert ws.receive_json() == {"type": "done"}
