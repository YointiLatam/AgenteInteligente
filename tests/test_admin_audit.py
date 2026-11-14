import re
from uuid import uuid4

import pytest

from app.admin import repository
from app.admin.repository import PostgresAdminStore
from tests.test_admin_rbac import FakeStore, install_store, token_for
from tests.test_admin_rbac import client as _client_fixture

client = _client_fixture


def _execution(tool, status):
    return {
        "id": uuid4(),
        "thread_id": uuid4(),
        "turn_id": uuid4(),
        "call_id": "c",
        "tool_name": tool,
        "decision": "approved",
        "status": status,
        "approved_by": "ana",
        "created_at": None,
        "completed_at": None,
    }


def test_audit_filters_and_total(client):
    store = FakeStore({"root": "admin"})
    store.executions = [
        _execution("send_whatsapp", "completed"),
        _execution("send_whatsapp", "failed"),
        _execution("post_x", "completed"),
    ]
    install_store(store)
    headers = token_for("root")
    body = client.get("/admin/audit/tool-executions", headers=headers).json()
    assert body["total"] == 3 and body["limit"] == 50
    body = client.get(
        "/admin/audit/tool-executions?status=completed&tool=send_whatsapp", headers=headers
    )
    assert body.json()["total"] == 1
    assert store.calls[-1] == ("list_tool_executions", "completed", "send_whatsapp", 50, 0)
    item = body.json()["items"][0]
    assert "result" not in item
    assert set(item) == {
        "id",
        "thread_id",
        "turn_id",
        "call_id",
        "tool_name",
        "decision",
        "status",
        "approved_by",
        "created_at",
        "completed_at",
    }


@pytest.mark.parametrize(
    "query",
    ["status=pending", "status=COMPLETED", "tool=a;DROP", "tool=x'--", "tool=", "limit=500"],
)
def test_invalid_filters_are_422_and_never_reach_the_store(client, query):
    store = FakeStore({"root": "admin"})
    install_store(store)
    response = client.get(f"/admin/audit/tool-executions?{query}", headers=token_for("root"))
    assert response.status_code == 422
    assert store.calls == []


def test_audit_is_denied_to_operators(client):
    install_store(FakeStore({"ana": "operator"}))
    assert client.get("/admin/audit/tool-executions", headers=token_for("ana")).status_code == 403


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        return _Cursor([{"total": 0}])


def test_sql_is_parameterized_for_every_filtered_query():
    hostile = "x'; DROP TABLE users; --"
    connection = RecordingConnection()
    store = PostgresAdminStore(connection)
    store.list_tool_executions("completed", hostile, 10, 5)
    store.list_threads("whatsapp", hostile, 10, 5)
    store.get_user_by_external_id(hostile)
    assert len(connection.statements) == 5
    for sql, params in connection.statements:
        assert hostile not in sql and "'completed'" not in sql and "'whatsapp'" not in sql
        assert sql.count("%s") == len(params)
    audit_sql, audit_params = connection.statements[0]
    assert audit_params == ("completed", hostile, 10, 5)
    assert "ORDER BY created_at DESC" in audit_sql
    assert re.search(r"status = %s AND tool_name = %s", audit_sql)
    assert repository.ADMIN_SCHEMA in audit_sql
