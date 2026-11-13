import json

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.tools.audit import AuditEntry


def _jsonb(value: dict | None) -> Jsonb | None:
    # default=str evita que un datetime o Decimal en el resultado rompa la auditoría
    return None if value is None else Jsonb(value, dumps=lambda o: json.dumps(o, default=str))


class PgAuditSink:
    """Guarda cada ejecución en `tool_audit_log` (ver db/init/002_tool_audit_log.sql)."""

    _INSERT = """
        INSERT INTO tool_audit_log
            (thread_id, tool_name, input_parameters, output_result, status,
             approved_by, execution_time_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def record(self, entry: AuditEntry) -> None:
        params = (
            entry.thread_id,
            entry.tool_name,
            _jsonb(entry.input_parameters),
            _jsonb(entry.output_result),
            entry.status.value,
            entry.approved_by,
            entry.execution_time_ms,
        )
        try:
            with self._connection.cursor() as cursor:
                cursor.execute(self._INSERT, params)
            self._connection.commit()
        except Exception:
            self._connection.rollback()  # deja la conexión utilizable para la próxima auditoría
            raise
