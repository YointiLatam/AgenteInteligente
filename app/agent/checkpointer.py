"""Configuracion explicita del checkpointer PostgreSQL de LangGraph."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row

from app.knowledge.settings import KnowledgeSettings, get_knowledge_settings

CHECKPOINT_SCHEMA = "inmobiliaria_pruebas"


class CheckpointerConfigurationError(RuntimeError):
    """La configuracion no permite abrir el checkpointer PostgreSQL."""


def _database_url(settings: KnowledgeSettings) -> str:
    if settings.database_url is None:
        raise CheckpointerConfigurationError("Falta DATABASE_URL para los checkpoints")
    return settings.database_url.get_secret_value()


@contextmanager
def postgres_checkpointer(
    settings: KnowledgeSettings | None = None,
) -> Iterator[PostgresSaver]:
    """Abre un ``PostgresSaver`` limitado al esquema de conocimiento verificado."""
    configured = settings or get_knowledge_settings()
    try:
        connection = psycopg.connect(
            _database_url(configured),
            autocommit=True,
            row_factory=dict_row,
            options=f"-c search_path={CHECKPOINT_SCHEMA},public",
        )
    except psycopg.Error as error:
        raise CheckpointerConfigurationError("No fue posible conectar los checkpoints") from error

    try:
        yield PostgresSaver(
            connection,
            serde=JsonPlusSerializer(
                pickle_fallback=False,
                allowed_msgpack_modules=None,
            ),
        )
    finally:
        connection.close()


def setup_postgres_checkpointer(settings: KnowledgeSettings | None = None) -> None:
    """Crea o actualiza las tablas oficiales al solicitarse administrativamente."""
    with postgres_checkpointer(settings) as checkpointer:
        checkpointer.setup()
