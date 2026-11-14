"""Read-only PostgreSQL access for the admin panel (users, threads, messages, audit).

Every query is parameterized: user-controlled values only travel as bound parameters. The only
text interpolated into SQL are module constants (schema name and fixed WHERE fragments).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.knowledge.settings import KnowledgeSettings, get_knowledge_settings

ADMIN_SCHEMA = "inmobiliaria_pruebas"


class AdminStoreError(RuntimeError):
    """The admin data could not be read (database missing or failing)."""


class AdminStore(Protocol):
    def get_user_by_external_id(self, external_id: str) -> dict[str, Any] | None: ...

    def list_users(self, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]: ...

    def list_paused_threads(self, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]: ...

    def list_threads(
        self, channel: str | None, user: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get_thread(self, thread_id: UUID) -> dict[str, Any] | None: ...

    def list_messages(
        self, thread_id: UUID, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]: ...

    def list_tool_executions(
        self, status: str | None, tool: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]: ...


AdminStoreOpener = Callable[[], AbstractContextManager[AdminStore]]


def _where(clauses: list[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


class PostgresAdminStore:
    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    def _fetch(self, sql: str, params: tuple[object, ...]) -> list[dict[str, Any]]:
        try:
            return list(self._connection.execute(sql, params).fetchall())
        except psycopg.Error as error:
            raise AdminStoreError("No fue posible consultar los datos del panel") from error

    def _page(
        self, select: str, count: str, params: tuple[object, ...], limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._fetch(select, (*params, limit, offset))
        total_rows = self._fetch(count, params)
        return rows, int(total_rows[0]["total"]) if total_rows else 0

    def get_user_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        rows = self._fetch(
            f"SELECT id, external_id, email, role FROM {ADMIN_SCHEMA}.users WHERE external_id = %s",
            (external_id,),
        )
        return rows[0] if rows else None

    def list_users(self, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        return self._page(
            f"SELECT id, external_id, email, role, created_at FROM {ADMIN_SCHEMA}.users "
            "ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
            f"SELECT count(*) AS total FROM {ADMIN_SCHEMA}.users",
            (),
            limit,
            offset,
        )

    def list_paused_threads(self, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        return self._page(
            f"SELECT id, channel, external_conversation_id, updated_at "
            f"FROM {ADMIN_SCHEMA}.conversation_threads WHERE status = 'paused_hil' "
            "ORDER BY updated_at, id LIMIT %s OFFSET %s",
            f"SELECT count(*) AS total FROM {ADMIN_SCHEMA}.conversation_threads "
            "WHERE status = 'paused_hil'",
            (),
            limit,
            offset,
        )

    def list_threads(
        self, channel: str | None, user: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[object] = []
        if channel is not None:
            clauses.append("channel = %s")
            params.append(channel)
        if user is not None:
            clauses.append("external_conversation_id = %s")
            params.append(user)
        where = _where(clauses)
        return self._page(
            f"SELECT id, channel, external_conversation_id, status, created_at, updated_at "
            f"FROM {ADMIN_SCHEMA}.conversation_threads {where} "
            "ORDER BY updated_at DESC, id LIMIT %s OFFSET %s",
            f"SELECT count(*) AS total FROM {ADMIN_SCHEMA}.conversation_threads {where}",
            tuple(params),
            limit,
            offset,
        )

    def get_thread(self, thread_id: UUID) -> dict[str, Any] | None:
        rows = self._fetch(
            f"SELECT id, channel, external_conversation_id, status "
            f"FROM {ADMIN_SCHEMA}.conversation_threads WHERE id = %s",
            (thread_id,),
        )
        return rows[0] if rows else None

    def list_messages(
        self, thread_id: UUID, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        return self._page(
            f"SELECT id, turn_id, role, content, created_at FROM {ADMIN_SCHEMA}.messages "
            "WHERE thread_id = %s ORDER BY created_at, id LIMIT %s OFFSET %s",
            f"SELECT count(*) AS total FROM {ADMIN_SCHEMA}.messages WHERE thread_id = %s",
            (thread_id,),
            limit,
            offset,
        )

    def list_tool_executions(
        self, status: str | None, tool: str | None, limit: int, offset: int
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = %s")
            params.append(status)
        if tool is not None:
            clauses.append("tool_name = %s")
            params.append(tool)
        where = _where(clauses)
        return self._page(
            "SELECT id, thread_id, turn_id, call_id, tool_name, decision, status, approved_by, "
            f"created_at, completed_at FROM {ADMIN_SCHEMA}.tool_executions {where} "
            "ORDER BY created_at DESC, id LIMIT %s OFFSET %s",
            f"SELECT count(*) AS total FROM {ADMIN_SCHEMA}.tool_executions {where}",
            tuple(params),
            limit,
            offset,
        )


@contextmanager
def postgres_admin_store(settings: KnowledgeSettings | None = None) -> Iterator[AdminStore]:
    configured = settings or get_knowledge_settings()
    if configured.database_url is None:
        raise AdminStoreError("Falta DATABASE_URL para el panel")
    if configured.knowledge_schema != ADMIN_SCHEMA:
        raise AdminStoreError("El panel está limitado al esquema verificado")
    try:
        connection = psycopg.connect(
            configured.database_url.get_secret_value(),
            autocommit=True,
            row_factory=dict_row,
            options=f"-c search_path={ADMIN_SCHEMA},public",
        )
    except psycopg.Error as error:
        raise AdminStoreError("No fue posible conectar con la base del panel") from error
    try:
        yield PostgresAdminStore(connection)
    finally:
        connection.close()


def get_admin_store_opener() -> AdminStoreOpener:
    """Dependency returning the opener; override it in tests with a fake store."""
    return postgres_admin_store
