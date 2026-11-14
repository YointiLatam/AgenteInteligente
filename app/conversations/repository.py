"""Repositorio PostgreSQL idempotente para conversaciones y mensajes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.knowledge.settings import KnowledgeSettings, get_knowledge_settings

CONVERSATION_SCHEMA = "inmobiliaria_pruebas"
Channel = Literal["web_widget", "whatsapp", "meta"]
MessageRole = Literal["user", "agent", "system", "human_operator"]
ThreadStatus = Literal["active", "paused_hil", "closed"]
ToolDecision = Literal["approved", "rejected"]
ToolExecutionStatus = Literal["processing", "completed", "failed"]


class ConversationPersistenceError(RuntimeError):
    """No fue posible leer o escribir la transcripción de negocio."""


class IdempotencyConflictError(ConversationPersistenceError):
    """Una clave reutilizada describe un mensaje distinto."""


@dataclass(frozen=True)
class ConversationThread:
    id: UUID
    channel: Channel
    external_conversation_id: str
    langgraph_thread_id: str
    status: ThreadStatus


@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    thread_id: UUID
    turn_id: UUID
    role: MessageRole
    content: str
    raw_payload: dict[str, Any]
    idempotency_key: str


@dataclass(frozen=True)
class ToolExecution:
    id: UUID
    thread_id: UUID
    turn_id: UUID
    call_id: str
    tool_name: str
    decision_id: str
    decision: ToolDecision
    status: ToolExecutionStatus
    approved_by: str
    result: dict[str, Any]


class ConversationRepository:
    """Acceso a conversaciones, mensajes y auditoría de herramientas."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection
        self._thread_locks: dict[str, Lock] = {}
        self._thread_locks_guard = Lock()

    def get_or_create_thread(
        self, channel: Channel, external_conversation_id: str
    ) -> ConversationThread:
        identifier = uuid4()
        try:
            row = self._connection.execute(
                f"""
                INSERT INTO {CONVERSATION_SCHEMA}.conversation_threads
                    (id, channel, external_conversation_id, langgraph_thread_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (channel, external_conversation_id) DO NOTHING
                RETURNING id, channel, external_conversation_id, langgraph_thread_id, status
                """,
                (identifier, channel, external_conversation_id, str(identifier)),
            ).fetchone()
            if row is None:
                row = self._connection.execute(
                    f"""
                    SELECT id, channel, external_conversation_id, langgraph_thread_id, status
                    FROM {CONVERSATION_SCHEMA}.conversation_threads
                    WHERE channel = %s AND external_conversation_id = %s
                    """,
                    (channel, external_conversation_id),
                ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError("No fue posible resolver la conversación") from error
        if row is None:
            raise ConversationPersistenceError("No fue posible recuperar la conversación existente")
        return ConversationThread(**row)

    def get_thread(self, thread_id: UUID) -> ConversationThread:
        try:
            row = self._connection.execute(
                f"""
                SELECT id, channel, external_conversation_id, langgraph_thread_id, status
                FROM {CONVERSATION_SCHEMA}.conversation_threads
                WHERE id = %s
                """,
                (thread_id,),
            ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError(
                "No fue posible consultar la conversación"
            ) from error
        if row is None:
            raise ConversationPersistenceError("No se encontró la conversación")
        return ConversationThread(**row)

    def mark_thread_paused_hil(self, thread_id: UUID) -> ConversationThread:
        """Proyecta idempotentemente una interrupción ya persistida por LangGraph."""
        try:
            row = self._connection.execute(
                f"""
                UPDATE {CONVERSATION_SCHEMA}.conversation_threads
                SET status = 'paused_hil',
                    updated_at = CASE
                        WHEN status = 'paused_hil' THEN updated_at
                        ELSE CURRENT_TIMESTAMP
                    END
                WHERE id = %s AND status IN ('active', 'paused_hil')
                RETURNING id, channel, external_conversation_id, langgraph_thread_id, status
                """,
                (thread_id,),
            ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError("No fue posible pausar la conversación") from error
        if row is None:
            raise ConversationPersistenceError(
                "La conversación no admite la transición a paused_hil"
            )
        return ConversationThread(**row)

    def mark_thread_active(self, thread_id: UUID) -> ConversationThread:
        """Reactiva idempotentemente un hilo cuyo checkpoint ya fue resuelto."""
        try:
            row = self._connection.execute(
                f"""
                UPDATE {CONVERSATION_SCHEMA}.conversation_threads
                SET status = 'active',
                    updated_at = CASE
                        WHEN status = 'active' THEN updated_at
                        ELSE CURRENT_TIMESTAMP
                    END
                WHERE id = %s AND status IN ('active', 'paused_hil')
                RETURNING id, channel, external_conversation_id, langgraph_thread_id, status
                """,
                (thread_id,),
            ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError(
                "No fue posible reactivar la conversación"
            ) from error
        if row is None:
            raise ConversationPersistenceError("La conversación no admite la transición a active")
        return ConversationThread(**row)

    def get_tool_execution(self, thread_id: UUID, call_id: str) -> ToolExecution | None:
        return self._find_tool_execution("thread_id = %s AND call_id = %s", (thread_id, call_id))

    def get_tool_execution_by_decision(
        self, thread_id: UUID, decision_id: str
    ) -> ToolExecution | None:
        return self._find_tool_execution(
            "thread_id = %s AND decision_id = %s", (thread_id, decision_id)
        )

    def create_tool_execution(
        self,
        *,
        thread_id: UUID,
        turn_id: UUID,
        call_id: str,
        tool_name: str,
        decision_id: str,
        decision: ToolDecision,
        approved_by: str,
        result: dict[str, Any],
    ) -> tuple[ToolExecution, bool]:
        identifier = uuid4()
        try:
            row = self._connection.execute(
                f"""
                INSERT INTO {CONVERSATION_SCHEMA}.tool_executions
                    (id, thread_id, turn_id, call_id, tool_name, decision_id,
                     decision, status, approved_by, result)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing', %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING id, thread_id, turn_id, call_id, tool_name, decision_id,
                          decision, status, approved_by, result
                """,
                (
                    identifier,
                    thread_id,
                    turn_id,
                    call_id,
                    tool_name,
                    decision_id,
                    decision,
                    approved_by,
                    Jsonb(result),
                ),
            ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError(
                "No fue posible registrar la ejecución de herramienta"
            ) from error
        if row:
            return ToolExecution(**row), True
        existing = self.get_tool_execution(thread_id, call_id)
        if existing is not None:
            return existing, False
        existing = self.get_tool_execution_by_decision(thread_id, decision_id)
        if existing is not None:
            return existing, False
        raise ConversationPersistenceError("No fue posible confirmar la ejecución registrada")

    def complete_tool_execution(self, execution_id: UUID, result: dict[str, Any]) -> ToolExecution:
        return self._finish_tool_execution(execution_id, "completed", result)

    def fail_tool_execution(self, execution_id: UUID, result: dict[str, Any]) -> ToolExecution:
        return self._finish_tool_execution(execution_id, "failed", result)

    def _find_tool_execution(
        self, predicate: str, params: tuple[object, ...]
    ) -> ToolExecution | None:
        try:
            row = self._connection.execute(
                f"""
                SELECT id, thread_id, turn_id, call_id, tool_name, decision_id,
                       decision, status, approved_by, result
                FROM {CONVERSATION_SCHEMA}.tool_executions
                WHERE {predicate}
                """,
                params,
            ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError(
                "No fue posible consultar la ejecución de herramienta"
            ) from error
        return ToolExecution(**row) if row else None

    def _finish_tool_execution(
        self,
        execution_id: UUID,
        status: Literal["completed", "failed"],
        result: dict[str, Any],
    ) -> ToolExecution:
        try:
            row = self._connection.execute(
                f"""
                UPDATE {CONVERSATION_SCHEMA}.tool_executions
                SET status = %s, result = %s, updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'processing'
                RETURNING id, thread_id, turn_id, call_id, tool_name, decision_id,
                          decision, status, approved_by, result
                """,
                (status, Jsonb(result), execution_id),
            ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError(
                "No fue posible finalizar la ejecución de herramienta"
            ) from error
        if row is None:
            raise ConversationPersistenceError(
                "La ejecución de herramienta no admite la transición solicitada"
            )
        return ToolExecution(**row)

    @contextmanager
    def thread_lock(self, langgraph_thread_id: str) -> Iterator[None]:
        """Serializa turnos de un hilo sin añadir estado ni tablas auxiliares."""
        with self._thread_locks_guard:
            local_lock = self._thread_locks.setdefault(langgraph_thread_id, Lock())
        with local_lock:
            try:
                self._connection.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))", (langgraph_thread_id,)
                )
            except psycopg.Error as error:
                raise ConversationPersistenceError(
                    "No fue posible bloquear la conversación"
                ) from error
            try:
                yield
            finally:
                try:
                    self._connection.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (langgraph_thread_id,),
                    )
                except psycopg.Error as error:
                    raise ConversationPersistenceError(
                        "No fue posible liberar la conversación"
                    ) from error

    def get_message(self, thread_id: UUID, idempotency_key: str) -> StoredMessage | None:
        try:
            row = self._connection.execute(
                f"""
                SELECT id, thread_id, turn_id, role, content, raw_payload, idempotency_key
                FROM {CONVERSATION_SCHEMA}.messages
                WHERE thread_id = %s AND idempotency_key = %s
                """,
                (thread_id, idempotency_key),
            ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError("No fue posible consultar el mensaje") from error
        return StoredMessage(**row) if row else None

    def store_message(
        self,
        *,
        thread_id: UUID,
        turn_id: UUID,
        role: MessageRole,
        content: str,
        raw_payload: dict[str, Any],
        idempotency_key: str,
    ) -> StoredMessage:
        identifier = uuid4()
        try:
            row = self._connection.execute(
                f"""
                INSERT INTO {CONVERSATION_SCHEMA}.messages
                    (id, thread_id, turn_id, role, content, raw_payload, idempotency_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (thread_id, idempotency_key) DO NOTHING
                RETURNING id, thread_id, turn_id, role, content, raw_payload, idempotency_key
                """,
                (
                    identifier,
                    thread_id,
                    turn_id,
                    role,
                    content,
                    Jsonb(raw_payload),
                    idempotency_key,
                ),
            ).fetchone()
        except psycopg.Error as error:
            raise ConversationPersistenceError("No fue posible guardar el mensaje") from error
        if row:
            return StoredMessage(**row)
        existing = self.get_message(thread_id, idempotency_key)
        if existing is None:
            raise ConversationPersistenceError("No fue posible confirmar el mensaje guardado")
        if (
            existing.turn_id != turn_id
            or existing.role != role
            or existing.content != content
            or existing.raw_payload != raw_payload
        ):
            raise IdempotencyConflictError("La clave de idempotencia ya pertenece a otro mensaje")
        return existing

    def list_messages(self, thread_id: UUID) -> list[StoredMessage]:
        try:
            rows = self._connection.execute(
                f"""
                SELECT id, thread_id, turn_id, role, content, raw_payload, idempotency_key
                FROM {CONVERSATION_SCHEMA}.messages
                WHERE thread_id = %s
                ORDER BY created_at, id
                """,
                (thread_id,),
            ).fetchall()
        except psycopg.Error as error:
            raise ConversationPersistenceError("No fue posible recuperar los mensajes") from error
        return [StoredMessage(**row) for row in rows]


@contextmanager
def postgres_conversation_repository(
    settings: KnowledgeSettings | None = None,
) -> Iterator[ConversationRepository]:
    configured = settings or get_knowledge_settings()
    if configured.database_url is None:
        raise ConversationPersistenceError("Falta DATABASE_URL para las conversaciones")
    if configured.knowledge_schema != CONVERSATION_SCHEMA:
        raise ConversationPersistenceError(
            "Las conversaciones están limitadas al esquema verificado"
        )
    try:
        connection = psycopg.connect(
            configured.database_url.get_secret_value(),
            autocommit=True,
            row_factory=dict_row,
            options=f"-c search_path={CONVERSATION_SCHEMA},public",
        )
    except psycopg.Error as error:
        raise ConversationPersistenceError("No fue posible conectar las conversaciones") from error
    try:
        yield ConversationRepository(connection)
    finally:
        connection.close()
