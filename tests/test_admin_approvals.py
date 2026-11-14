from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from app.conversations.service import ToolDecisionOutOfOrderError
from app.conversations.wiring import ServiceNotConfigured, get_conversation_service_opener
from app.main import app
from tests.test_admin_rbac import FakeStore, install_store, token_for
from tests.test_admin_rbac import client as _client_fixture

client = _client_fixture

THREAD = uuid4()
TURN = uuid4()


class FakeService:
    def __init__(self, pending=None, error=None):
        self.pending = pending or {}
        self.error = error
        self.requests = []
        self.decided = {}

    def read_pending_approval(self, thread_id):
        value = self.pending.get(thread_id)
        if isinstance(value, Exception):
            raise value
        return value

    def decide_tool_call(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        stored = self.decided.setdefault(request.call_id, request.decision)
        return SimpleNamespace(
            execution=SimpleNamespace(call_id=request.call_id, decision=stored), replayed=False
        )


def install_service(service=None, not_configured=False):
    @contextmanager
    def opener():
        if not_configured:
            raise ServiceNotConfigured("falta DATABASE_URL postgres://x:y@h/d")
        yield service

    app.dependency_overrides[get_conversation_service_opener] = lambda: opener


def _decision(**extra):
    return {
        "thread_id": str(THREAD),
        "turn_id": str(TURN),
        "tool_name": "send_whatsapp",
        "decision": "approved",
        **extra,
    }


def test_inbox_lists_pending_calls_and_skips_threads_without_one(client):
    other, broken = uuid4(), uuid4()
    store = FakeStore({"ana": "operator"})
    store.paused = [
        {
            "id": THREAD,
            "channel": "whatsapp",
            "external_conversation_id": "5215",
            "updated_at": None,
        },
        {
            "id": other,
            "channel": "web_widget",
            "external_conversation_id": "w1",
            "updated_at": None,
        },
        {"id": broken, "channel": "meta", "external_conversation_id": "m1", "updated_at": None},
    ]
    install_store(store)
    install_service(
        FakeService(
            {
                THREAD: {
                    "turn_id": str(TURN),
                    "call_id": "call-1",
                    "tool_name": "send_whatsapp",
                    "parameters": {"to": "5215", "text": "hola"},
                },
                other: None,
                broken: RuntimeError("checkpoint corrupto"),
            }
        )
    )
    body = client.get("/admin/approvals", headers=token_for("ana")).json()
    assert body["total"] == 3 and body["limit"] == 50
    assert body["items"] == [
        {
            "thread_id": str(THREAD),
            "turn_id": str(TURN),
            "call_id": "call-1",
            "tool_name": "send_whatsapp",
            "parameters": {"to": "5215", "text": "hola"},
            "channel": "whatsapp",
            "external_conversation_id": "5215",
            "created_at": None,
        }
    ]


def test_inbox_unconfigured_service_is_503_without_internals(client):
    install_store(FakeStore({"ana": "operator"}))
    install_service(not_configured=True)
    response = client.get("/admin/approvals", headers=token_for("ana"))
    assert response.status_code == 503
    assert "postgres" not in response.text and "DATABASE_URL" not in response.text


def test_decision_uses_the_database_user_as_approver_not_the_payload(client):
    install_store(FakeStore({"ana": "operator"}))
    service = FakeService()
    install_service(service)
    response = client.post(
        "/admin/approvals/call-1/decision",
        json=_decision(approved_by="intruso", call_id="otro"),
        headers=token_for("ana"),
    )
    assert response.status_code == 200
    assert response.json() == {"call_id": "call-1", "status": "executed"}
    request = service.requests[0]
    assert request.approved_by == "ana"
    assert request.call_id == "call-1"
    assert (request.thread_id, request.turn_id) == (THREAD, TURN)


def test_rejection_carries_the_reason(client):
    install_store(FakeStore({"ana": "admin"}))
    service = FakeService()
    install_service(service)
    response = client.post(
        "/admin/approvals/call-1/decision",
        json=_decision(decision="rejected", reason="no corresponde"),
        headers=token_for("ana"),
    )
    assert response.json() == {"call_id": "call-1", "status": "rejected"}
    assert service.requests[0].reason == "no corresponde"


def test_repeated_decision_is_idempotent(client):
    install_store(FakeStore({"ana": "operator"}))
    service = FakeService()
    install_service(service)
    first = client.post(
        "/admin/approvals/call-1/decision", json=_decision(), headers=token_for("ana")
    )
    second = client.post(
        "/admin/approvals/call-1/decision", json=_decision(), headers=token_for("ana")
    )
    assert first.json() == second.json()
    assert service.requests[0].decision_id == service.requests[1].decision_id


def test_decision_errors_map_to_public_messages(client):
    install_store(FakeStore({"ana": "operator"}))
    install_service(FakeService(error=ToolDecisionOutOfOrderError("checkpoint x secreto")))
    response = client.post(
        "/admin/approvals/call-1/decision", json=_decision(), headers=token_for("ana")
    )
    assert response.status_code == 409
    assert "secreto" not in response.text
    install_service(not_configured=True)
    response = client.post(
        "/admin/approvals/call-1/decision", json=_decision(), headers=token_for("ana")
    )
    assert response.status_code == 503 and "postgres" not in response.text


def test_decision_requires_role_and_valid_body(client):
    install_store(FakeStore({"u": "end_user", "ana": "operator"}))
    service = FakeService()
    install_service(service)
    url = "/admin/approvals/call-1/decision"
    assert client.post(url, json=_decision(), headers=token_for("u")).status_code == 403
    assert client.post(url, json=_decision()).status_code == 401
    bad = _decision(decision="maybe")
    assert client.post(url, json=bad, headers=token_for("ana")).status_code == 422
    assert service.requests == []
