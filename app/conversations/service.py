"""Servicio reutilizable que enlaza mensajes de negocio y LangGraph."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import UUID, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.graph import build_agent_graph, invoke_agent
from app.agent.nodes.retrieval import Retriever
from app.agent.state import AgentState
from app.conversations.repository import (
    Channel,
    ConversationRepository,
    ConversationThread,
    MessageRole,
    StoredMessage,
)
from app.integrations.gemini import TextGenerator


class TurnRecoveryRequiredError(RuntimeError):
    """Un turno existe, pero no hay una respuesta inequívoca para recuperarlo."""


class TurnIdempotencyConflictError(RuntimeError):
    """Un request_id existente no representa la misma solicitud de usuario."""


@dataclass(frozen=True)
class UserTurnRequest:
    channel: Channel
    external_conversation_id: str
    request_id: str
    content: str
    retrieval_requested: bool = False
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TurnResult:
    thread: ConversationThread
    message: StoredMessage
    replayed: bool


class ConversationService:
    """Persiste solo los mensajes de negocio, nunca el evento interno del router."""

    _local_locks: dict[str, Lock] = {}
    _local_locks_guard = Lock()

    def __init__(
        self,
        repository: ConversationRepository,
        checkpointer: BaseCheckpointSaver,
        *,
        retriever: Retriever | None = None,
        gemini_client: TextGenerator | None = None,
        recursion_limit: int = 4,
    ) -> None:
        self._repository = repository
        self._checkpointer = checkpointer
        self._retriever = retriever
        self._gemini_client = gemini_client
        self._recursion_limit = recursion_limit

    def process_user_turn(self, request: UserTurnRequest) -> TurnResult:
        thread = self._repository.get_or_create_thread(
            request.channel, request.external_conversation_id
        )
        with self._thread_lock(thread):
            turn_id = uuid5(thread.id, request.request_id)
            user_key = f"user:{request.request_id}"
            agent_key = f"agent:{request.request_id}"
            existing_user = self._repository.get_message(thread.id, user_key)
            if existing_user is not None:
                self._validate_existing_request(existing_user, request, turn_id)
                existing_agent = self._repository.get_message(thread.id, agent_key)
                if existing_agent is not None:
                    return TurnResult(thread=thread, message=existing_agent, replayed=True)
                return self._recover_completed_turn(thread, turn_id, agent_key)

            self._repository.store_message(
                thread_id=thread.id,
                turn_id=turn_id,
                role="user",
                content=request.content,
                raw_payload=self._user_payload(request),
                idempotency_key=user_key,
            )
            result = invoke_agent(
                self._state(request.content, request.retrieval_requested, str(turn_id)),
                retriever=self._retriever,
                gemini_client=self._gemini_client,
                checkpointer=self._checkpointer,
                thread_id=thread.langgraph_thread_id,
                recursion_limit=self._recursion_limit,
            )
            answer = self._assistant_for_turn(result, str(turn_id))
            stored = self._repository.store_message(
                thread_id=thread.id,
                turn_id=turn_id,
                role="agent",
                content=answer["content"],
                raw_payload=answer["raw_payload"],
                idempotency_key=agent_key,
            )
            return TurnResult(thread=thread, message=stored, replayed=False)

    def record_external_message(
        self,
        *,
        channel: Channel,
        external_conversation_id: str,
        request_id: str,
        role: MessageRole,
        content: str,
        raw_payload: dict[str, Any] | None = None,
    ) -> StoredMessage:
        if role not in {"system", "human_operator"}:
            raise ValueError("Este método solo registra mensajes system o human_operator")
        thread = self._repository.get_or_create_thread(channel, external_conversation_id)
        key = f"{role}:{request_id}"
        return self._repository.store_message(
            thread_id=thread.id,
            turn_id=uuid5(thread.id, key),
            role=role,
            content=content,
            raw_payload=raw_payload or {},
            idempotency_key=key,
        )

    def _recover_completed_turn(
        self, thread: ConversationThread, turn_id: UUID, agent_key: str
    ) -> TurnResult:
        graph = build_agent_graph(
            retriever=self._retriever,
            gemini_client=self._gemini_client,
            checkpointer=self._checkpointer,
        )
        snapshot = graph.get_state({"configurable": {"thread_id": thread.langgraph_thread_id}})
        values = snapshot.values
        if snapshot.next or values.get("completed_turn_id") != str(turn_id):
            raise TurnRecoveryRequiredError(
                "El turno existe sin respuesta persistida y no puede asociarse "
                "a un checkpoint finalizado"
            )
        answer = self._assistant_for_turn(values, str(turn_id))
        stored = self._repository.store_message(
            thread_id=thread.id,
            turn_id=turn_id,
            role="agent",
            content=answer["content"],
            raw_payload=answer["raw_payload"],
            idempotency_key=agent_key,
        )
        return TurnResult(thread=thread, message=stored, replayed=True)

    @staticmethod
    def _assistant_for_turn(state: dict[str, Any], turn_id: str) -> dict[str, Any]:
        for message in reversed(state.get("messages", [])):
            if message.get("role") == "assistant" and message.get("turn_id") == turn_id:
                return message
        raise TurnRecoveryRequiredError("No se encontró la respuesta del turno en el checkpoint")

    @staticmethod
    def _user_payload(request: UserTurnRequest) -> dict[str, Any]:
        return {
            **request.raw_payload,
            "_conversation": {"retrieval_requested": request.retrieval_requested},
        }

    @classmethod
    def _validate_existing_request(
        cls, message: StoredMessage, request: UserTurnRequest, turn_id: UUID
    ) -> None:
        if (
            message.turn_id != turn_id
            or message.role != "user"
            or message.content != request.content
            or message.raw_payload != cls._user_payload(request)
        ):
            raise TurnIdempotencyConflictError(
                "El request_id ya existe para una solicitud incompatible"
            )

    @contextmanager
    def _thread_lock(self, thread: ConversationThread) -> Iterator[None]:
        repository_lock = getattr(self._repository, "thread_lock", None)
        if repository_lock is not None:
            with repository_lock(thread.langgraph_thread_id):
                yield
            return
        with self._local_locks_guard:
            lock = self._local_locks.setdefault(thread.langgraph_thread_id, Lock())
        with lock:
            yield

    @staticmethod
    def _state(query: str, retrieval_requested: bool, turn_id: str) -> AgentState:
        return {
            "query": query,
            "retrieval_requested": retrieval_requested,
            "route": None,
            "documents": [],
            "messages": [],
            "conversation_summary": "",
            "summary_cursor": 0,
            "summary_failure_cursor": None,
            "new_turn": False,
            "turn_id": turn_id,
            "completed_turn_id": None,
            "answer": None,
            "error": None,
        }
