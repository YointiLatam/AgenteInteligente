"""Lazy construction of the ``ConversationService`` from the existing pieces.

Environment needed (nothing is read or connected at import time):

- ``DATABASE_URL``: conversations repository and LangGraph checkpointer (PostgreSQL).
- ``GEMINI_API_KEY``: Gemini client used by the agent graph.
- Retrieval (RAG) resolves its own configuration lazily when the RAG branch runs.

Known gap: the agent graph expects ``app.agent.tools.ToolRegistry`` (sync handlers) while the
HTTP tool catalog lives in ``app.tools``; there is no adapter between them yet, so the service
is built without a tool registry.

Usage:
    with open_conversation_service() as service:      # plain code / background tasks
        service.process_user_turn(...)

    service: Annotated[ConversationService, Depends(get_conversation_service)]   # routes

Both paths can be replaced in tests with ``app.dependency_overrides``.
"""

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager

from fastapi import HTTPException, status

from app.agent.checkpointer import CheckpointerConfigurationError, postgres_checkpointer
from app.conversations.repository import (
    ConversationPersistenceError,
    postgres_conversation_repository,
)
from app.conversations.service import ConversationService
from app.integrations.gemini import GeminiClient, GeminiNotConfigured
from app.integrations.settings import get_integrations_settings
from app.knowledge.retrieval import configured_retriever

ServiceOpener = Callable[[], AbstractContextManager[ConversationService]]


class ServiceNotConfigured(RuntimeError):
    """A piece of the conversation service cannot be built with the current environment."""


@contextmanager
def open_conversation_service() -> Iterator[ConversationService]:
    """Open the DB connections and build a service; they are closed when the block ends."""
    try:
        with ExitStack() as stack:
            repository = stack.enter_context(postgres_conversation_repository())
            checkpointer = stack.enter_context(postgres_checkpointer())
            gemini = GeminiClient(get_integrations_settings())
            yield ConversationService(
                repository,
                checkpointer,
                retriever=configured_retriever,
                gemini_client=gemini,
            )
    except (
        ConversationPersistenceError,
        CheckpointerConfigurationError,
        GeminiNotConfigured,
    ) as error:
        raise ServiceNotConfigured(str(error)) from error


def get_conversation_service_opener() -> ServiceOpener:
    """Dependency returning the opener; override it in tests or in other composition roots."""
    return open_conversation_service


def get_conversation_service() -> Iterator[ConversationService]:
    """FastAPI dependency (per request). Maps a missing configuration to HTTP 503."""
    try:
        with open_conversation_service() as service:
            yield service
    except ServiceNotConfigured as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de conversación no configurado"
        ) from error
