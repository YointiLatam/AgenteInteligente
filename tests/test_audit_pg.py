from datetime import datetime
from uuid import uuid4

import pytest

from app.tools.audit import AuditEntry, ToolStatus
from app.tools.audit_pg import PgAuditSink


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        if self.connection.fail:
            raise RuntimeError("db caída")
        self.connection.executed.append((sql, params))


class FakeConnection:
    def __init__(self, fail=False):
        self.fail, self.executed, self.commits, self.rollbacks = fail, [], 0, 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_inserta_todos_los_campos_y_confirma():
    connection, thread, approver = FakeConnection(), uuid4(), uuid4()
    entry = AuditEntry(
        "get_order", ToolStatus.EXECUTED, {"id": "1"}, {"ok": True}, 12, thread, approver
    )

    PgAuditSink(connection).record(entry)

    [(sql, params)] = connection.executed
    assert "INSERT INTO tool_audit_log" in sql
    assert params[0] == thread
    assert params[1] == "get_order"
    assert params[2].obj == {"id": "1"}
    assert params[3].obj == {"ok": True}
    assert params[4:] == ("executed", approver, 12)
    assert connection.commits == 1


def test_resultado_ausente_se_guarda_como_null():
    connection = FakeConnection()

    PgAuditSink(connection).record(AuditEntry("t", ToolStatus.PENDING_APPROVAL, {}))

    assert connection.executed[0][1][3] is None


def test_resultados_no_serializables_no_rompen_la_auditoria():
    connection = FakeConnection()
    entry = AuditEntry("t", ToolStatus.EXECUTED, {}, {"cuando": datetime(2026, 1, 1)})

    PgAuditSink(connection).record(entry)

    jsonb = connection.executed[0][1][3]
    assert "2026-01-01" in jsonb.dumps(jsonb.obj)


def test_si_falla_el_insert_hace_rollback_y_propaga():
    connection = FakeConnection(fail=True)

    with pytest.raises(RuntimeError):
        PgAuditSink(connection).record(AuditEntry("t", ToolStatus.FAILED, {}))

    assert (connection.rollbacks, connection.commits) == (1, 0)
