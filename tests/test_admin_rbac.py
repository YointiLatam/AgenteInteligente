import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.admin.repository import AdminStoreError, get_admin_store_opener
from app.auth.security import create_access_token, hash_api_key
from app.auth.settings import get_auth_settings
from app.conversations.wiring import get_conversation_service_opener
from app.main import app

SECRET = "s" * 40


class FakeStore:
    """In-memory AdminStore; ``users`` maps external_id -> role and may change between calls."""

    def __init__(self, users=None, fail=False):
        self.users = users if users is not None else {}
        self.fail = fail
        self.calls = []
        self.threads = []
        self.messages = {}
        self.executions = []
        self.paused = []

    def _check(self):
        if self.fail:
            raise AdminStoreError("boom: postgres://user:secret@host/db")

    def get_user_by_external_id(self, external_id):
        self._check()
        role = self.users.get(external_id)
        if role is None:
            return None
        return {"id": uuid4(), "external_id": external_id, "email": None, "role": role}

    def list_users(self, limit, offset):
        self._check()
        self.calls.append(("list_users", limit, offset))
        rows = [
            {
                "id": uuid4(),
                "external_id": name,
                "email": None,
                "role": role,
                "created_at": None,
            }
            for name, role in self.users.items()
        ]
        return rows, len(rows)

    def list_paused_threads(self, limit, offset):
        self._check()
        self.calls.append(("list_paused_threads", limit, offset))
        return self.paused, len(self.paused)

    def list_threads(self, channel, user, limit, offset):
        self._check()
        self.calls.append(("list_threads", channel, user, limit, offset))
        rows = [
            t
            for t in self.threads
            if (channel is None or t["channel"] == channel)
            and (user is None or t["external_conversation_id"] == user)
        ]
        return rows[offset : offset + limit], len(rows)

    def get_thread(self, thread_id):
        self._check()
        return next((t for t in self.threads if t["id"] == thread_id), None)

    def list_messages(self, thread_id, limit, offset):
        self._check()
        self.calls.append(("list_messages", thread_id, limit, offset))
        rows = self.messages.get(thread_id, [])
        return rows[offset : offset + limit], len(rows)

    def list_tool_executions(self, status, tool, limit, offset):
        self._check()
        self.calls.append(("list_tool_executions", status, tool, limit, offset))
        rows = [
            e
            for e in self.executions
            if (status is None or e["status"] == status)
            and (tool is None or e["tool_name"] == tool)
        ]
        return rows[offset : offset + limit], len(rows)


def install_store(store):
    @contextmanager
    def opener():
        yield store

    app.dependency_overrides[get_admin_store_opener] = lambda: opener


def token_for(name):
    return {"Authorization": f"Bearer {create_access_token(name, get_auth_settings())}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("AUTH_API_KEYS", json.dumps({"demo": hash_api_key("k")}))
    get_auth_settings.cache_clear()
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_auth_settings.cache_clear()


ENDPOINTS = [
    ("GET", "/admin/me", {"operator", "admin"}),
    ("GET", "/admin/users", {"admin"}),
    ("GET", "/admin/approvals", {"operator", "admin"}),
    ("GET", "/admin/conversations", {"operator", "admin"}),
    ("GET", f"/admin/conversations/{uuid4()}/messages", {"operator", "admin"}),
    ("GET", "/admin/audit/tool-executions", {"admin"}),
]


@pytest.fixture(autouse=True)
def _no_real_service():
    @contextmanager
    def opener():
        yield type("S", (), {"read_pending_approval": lambda self, _id: None})()

    app.dependency_overrides[get_conversation_service_opener] = lambda: opener
    yield


@pytest.mark.parametrize("method,path,allowed", ENDPOINTS)
@pytest.mark.parametrize("role", ["admin", "operator", "end_user"])
def test_role_matrix(client, method, path, allowed, role):
    install_store(FakeStore({"demo": role}))
    response = client.request(method, path, headers=token_for("demo"))
    if role in allowed:
        assert response.status_code in (200, 404)  # 404: unknown thread in the messages route
    else:
        assert response.status_code == 403


@pytest.mark.parametrize("method,path,allowed", ENDPOINTS)
def test_unknown_user_is_403_and_missing_or_bad_token_is_401(client, method, path, allowed):
    install_store(FakeStore({}))
    assert client.request(method, path, headers=token_for("stranger")).status_code == 403
    assert client.request(method, path).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.request(method, path, headers=bad).status_code == 401
    # An API key alone is not enough: the panel requires the JWT.
    assert client.request(method, path, headers={"X-API-Key": "k"}).status_code == 401


def test_revoked_role_takes_effect_on_next_request(client):
    store = FakeStore({"demo": "admin"})
    install_store(store)
    headers = token_for("demo")  # the same token is reused
    assert client.get("/admin/audit/tool-executions", headers=headers).status_code == 200
    store.users["demo"] = "end_user"
    assert client.get("/admin/audit/tool-executions", headers=headers).status_code == 403
    store.users["demo"] = "operator"
    assert client.get("/admin/audit/tool-executions", headers=headers).status_code == 403
    assert client.get("/admin/conversations", headers=headers).status_code == 200
    del store.users["demo"]
    assert client.get("/admin/me", headers=headers).status_code == 403


def test_me_returns_the_database_identity(client):
    install_store(FakeStore({"demo": "operator"}))
    body = client.get("/admin/me", headers=token_for("demo")).json()
    assert set(body) == {"id", "external_id", "email", "role"}
    assert (body["external_id"], body["role"]) == ("demo", "operator")


def test_users_list_is_admin_only_and_paginated(client):
    install_store(FakeStore({"demo": "admin", "ana": "operator"}))
    body = client.get("/admin/users?limit=1", headers=token_for("demo")).json()
    assert body["total"] == 2 and body["limit"] == 1 and body["offset"] == 0
    assert {"id", "external_id", "email", "role", "created_at"} == set(body["items"][0])


def test_store_failure_is_503_without_internals(client):
    install_store(FakeStore({"demo": "admin"}, fail=True))
    response = client.get("/admin/me", headers=token_for("demo"))
    assert response.status_code == 503
    assert response.json() == {"detail": "Servicio de administración no disponible"}
    assert "secret" not in response.text and "postgres" not in response.text
