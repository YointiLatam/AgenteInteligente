"""Servicio reutilizable que enlaza mensajes de negocio y LangGraph."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.graph import build_agent_graph, invoke_agent
from app.agent.nodes.retrieval import Retriever
from app.agent.state import AgentState, PendingToolCall
from app.agent.tools import ToolEffect, ToolRegistry
from app.conversations.repository import (
    Channel,
    ConversationRepository,
    ConversationThread,
    MessageRole,
    StoredMessage,
    ToolExecution,
)
from app.integrations.gemini import TextGenerator
from app.security.content_guard import (
    ContentGuard,
    DeterministicContentGuard,
    GuardDecision,
)


class TurnRecoveryRequiredError(RuntimeError):
    """Un turno existe, pero no hay una respuesta inequívoca para recuperarlo."""


class TurnIdempotencyConflictError(RuntimeError):
    """Un request_id existente no representa la misma solicitud de usuario."""


class InputBlockedError(RuntimeError):
    """La entrada fue rechazada antes de producir efectos de negocio."""

    def __init__(self, public_message: str, category: str, policy_version: str) -> None:
        super().__init__(public_message)
        self.category = category
        self.policy_version = policy_version


class ThreadPausedError(RuntimeError):
    """El hilo conserva una ejecución que requiere aprobación humana."""


class ToolDecisionConflictError(RuntimeError):
    """La decisión no corresponde a la llamada pendiente o contradice otra decisión."""


class ToolDecisionOutOfOrderError(RuntimeError):
    """La conversación no conserva la llamada pendiente indicada."""


class ToolExecutionUncertainError(RuntimeError):
    """Una ejecución iniciada no puede repetirse con seguridad."""


class ToolExecutionFailedError(RuntimeError):
    """La ejecución crítica terminó con un fallo controlado."""


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


@dataclass(frozen=True)
class PendingApprovalResult:
    thread: ConversationThread
    turn_id: UUID
    tool_call: PendingToolCall
    replayed: bool


@dataclass(frozen=True)
class ToolDecisionRequest:
    decision_id: str
    thread_id: UUID
    turn_id: UUID
    call_id: str
    tool_name: str
    decision: Literal["approved", "rejected"]
    approved_by: str
    reason: str | None = None


@dataclass(frozen=True)
class ToolDecisionResult:
    thread: ConversationThread
    execution: ToolExecution
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
        tool_registry: ToolRegistry | None = None,
        content_guard: ContentGuard | None = None,
        recursion_limit: int = 4,
    ) -> None:
        self._repository = repository
        self._checkpointer = checkpointer
        self._retriever = retriever
        self._gemini_client = gemini_client
        self._tool_registry = tool_registry
        self._content_guard = content_guard or DeterministicContentGuard()
        self._recursion_limit = recursion_limit

    def process_user_turn(self, request: UserTurnRequest) -> TurnResult | PendingApprovalResult:
        input_result = self._content_guard.validate_input(request.content)
        if input_result.decision == GuardDecision.BLOCKED_INPUT:
            raise InputBlockedError(
                input_result.public_message or "Solicitud bloqueada",
                input_result.category or "unknown",
                input_result.policy_version,
            )
        thread = self._repository.get_or_create_thread(
            request.channel, request.external_conversation_id
        )
        with self._thread_lock(thread):
            thread = self._repository.get_thread(thread.id)
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

            if thread.status == "paused_hil":
                raise ThreadPausedError("La conversación está pausada por aprobación humana")
            self._guard_no_pending_checkpoint(thread)

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
                tool_registry=self._tool_registry,
                thread_id=thread.langgraph_thread_id,
                recursion_limit=self._recursion_limit,
            )
            pending = self._pending_approval_result(thread, turn_id, replayed=False)
            if pending is not None:
                return pending
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

    def decide_tool_call(self, request: ToolDecisionRequest) -> ToolDecisionResult:
        self._validate_tool_decision_request(request)
        thread = self._repository.get_thread(request.thread_id)
        with self._thread_lock(thread):
            thread = self._repository.get_thread(request.thread_id)
            existing = self._repository.get_tool_execution(thread.id, request.call_id)
            if existing is not None:
                return self._replay_tool_decision(thread, existing, request)
            reused = self._repository.get_tool_execution_by_decision(thread.id, request.decision_id)
            if reused is not None:
                raise ToolDecisionConflictError(
                    "decision_id ya pertenece a otra llamada de herramienta"
                )

            graph = self._build_graph()
            config = self._graph_config(thread)
            snapshot = graph.get_state(config)
            pending = self._validate_pending_tool(snapshot, request)
            if thread.status == "closed":
                raise ToolDecisionConflictError("La conversación está cerrada")
            if request.decision == "approved":
                self._validate_approved_tool(pending)

            initial_result = (
                {"decision": "rejected", "reason": request.reason}
                if request.decision == "rejected"
                else {}
            )
            execution, created = self._repository.create_tool_execution(
                thread_id=thread.id,
                turn_id=request.turn_id,
                call_id=request.call_id,
                tool_name=request.tool_name,
                decision_id=request.decision_id,
                decision=request.decision,
                approved_by=request.approved_by,
                result=initial_result,
            )
            if not created:
                return self._replay_tool_decision(thread, execution, request)
            if request.decision == "rejected":
                return self._reject_tool_call(
                    graph, config, thread, execution, request, initial_result
                )
            return self._approve_tool_call(graph, config, thread, execution, request)

    def _approve_tool_call(
        self,
        graph: Any,
        config: dict[str, Any],
        thread: ConversationThread,
        execution: ToolExecution,
        request: ToolDecisionRequest,
    ) -> ToolDecisionResult:
        try:
            graph.invoke(None, config)
        except Exception:
            return self._recover_resolved_tool_execution(
                graph, config, thread, execution, request.turn_id
            )
        return self._recover_resolved_tool_execution(
            graph, config, thread, execution, request.turn_id
        )

    def _reject_tool_call(
        self,
        graph: Any,
        config: dict[str, Any],
        thread: ConversationThread,
        execution: ToolExecution,
        request: ToolDecisionRequest,
        result: dict[str, Any],
    ) -> ToolDecisionResult:
        try:
            updated_config = graph.update_state(
                config,
                {
                    "tool_call": None,
                    "pending_tool_call": None,
                    "tool_result": result,
                    "execution_status": "completed",
                    "completed_turn_id": str(request.turn_id),
                },
                as_node="critical_tools",
            )
            graph.invoke(None, updated_config)
        except Exception:
            return self._recover_resolved_tool_execution(
                graph, config, thread, execution, request.turn_id, result
            )
        return self._recover_resolved_tool_execution(
            graph, config, thread, execution, request.turn_id, result
        )

    def _recover_resolved_tool_execution(
        self,
        graph: Any,
        config: dict[str, Any],
        thread: ConversationThread,
        execution: ToolExecution,
        turn_id: UUID,
        expected_result: dict[str, Any] | None = None,
    ) -> ToolDecisionResult:
        """Finaliza la auditoría solo cuando el checkpoint confirma su resultado."""
        try:
            result = self._completed_tool_result(graph, config, turn_id)
            if expected_result is not None and result != expected_result:
                raise ToolExecutionFailedError(
                    "El checkpoint no conserva el resultado de rechazo esperado"
                )
            completed = self._repository.complete_tool_execution(execution.id, result)
            active_thread = self._repository.mark_thread_active(thread.id)
        except Exception as error:
            raise ToolExecutionUncertainError(
                "La resolución no pudo confirmarse; la ejecución permanece processing"
            ) from error
        return ToolDecisionResult(thread=active_thread, execution=completed, replayed=False)

    def _replay_tool_decision(
        self,
        thread: ConversationThread,
        execution: ToolExecution,
        request: ToolDecisionRequest,
    ) -> ToolDecisionResult:
        self._validate_existing_tool_execution(execution, request)
        if execution.status == "failed":
            raise ToolExecutionFailedError("La ejecución crítica ya terminó con un fallo")
        if execution.status == "completed":
            active_thread = self._repository.mark_thread_active(thread.id)
            return ToolDecisionResult(thread=active_thread, execution=execution, replayed=True)

        graph = self._build_graph()
        config = self._graph_config(thread)
        snapshot = graph.get_state(config)
        if not snapshot.next and snapshot.values.get("completed_turn_id") == str(request.turn_id):
            result = self._completed_tool_result(graph, config, request.turn_id)
            completed = self._repository.complete_tool_execution(execution.id, result)
            active_thread = self._repository.mark_thread_active(thread.id)
            return ToolDecisionResult(thread=active_thread, execution=completed, replayed=True)
        raise ToolExecutionUncertainError(
            "La ejecución permanece processing y no se repetirá automáticamente"
        )

    def _build_graph(self) -> Any:
        return build_agent_graph(
            retriever=self._retriever,
            gemini_client=self._gemini_client,
            checkpointer=self._checkpointer,
            tool_registry=self._tool_registry,
        )

    def _graph_config(self, thread: ConversationThread) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread.langgraph_thread_id},
            "recursion_limit": self._recursion_limit + 1,
        }

    @staticmethod
    def _validate_tool_decision_request(request: ToolDecisionRequest) -> None:
        if not all(
            value.strip()
            for value in (
                request.decision_id,
                request.call_id,
                request.tool_name,
                request.approved_by,
            )
        ):
            raise ValueError("Los identificadores de la decisión no pueden estar vacíos")

    @staticmethod
    def _validate_pending_tool(snapshot: Any, request: ToolDecisionRequest) -> PendingToolCall:
        pending = snapshot.values.get("pending_tool_call")
        if (
            snapshot.next != ("critical_tools",)
            or snapshot.values.get("execution_status") != "pending_approval"
            or snapshot.values.get("turn_id") != str(request.turn_id)
            or not isinstance(pending, dict)
            or pending.get("call_id") != request.call_id
            or pending.get("tool_name") != request.tool_name
            or not isinstance(pending.get("arguments"), dict)
        ):
            raise ToolDecisionOutOfOrderError("La decisión no corresponde al checkpoint pendiente")
        return cast(PendingToolCall, pending)

    def _validate_approved_tool(self, pending: PendingToolCall) -> None:
        registry = self._tool_registry
        tool = registry.get(pending["tool_name"]) if registry is not None else None
        if tool is None or tool.effect == ToolEffect.READ or tool.effect.value != pending["effect"]:
            raise ToolDecisionConflictError(
                "La herramienta pendiente ya no conserva su clasificación crítica"
            )

    @staticmethod
    def _validate_existing_tool_execution(
        execution: ToolExecution, request: ToolDecisionRequest
    ) -> None:
        expected_reason = execution.result.get("reason")
        if (
            execution.thread_id != request.thread_id
            or execution.turn_id != request.turn_id
            or execution.call_id != request.call_id
            or execution.tool_name != request.tool_name
            or execution.decision_id != request.decision_id
            or execution.decision != request.decision
            or execution.approved_by != request.approved_by
            or request.decision == "rejected"
            and expected_reason != request.reason
        ):
            raise ToolDecisionConflictError("La llamada ya tiene una decisión incompatible")

    @staticmethod
    def _completed_tool_result(graph: Any, config: dict[str, Any], turn_id: UUID) -> dict[str, Any]:
        snapshot = graph.get_state(config)
        result = snapshot.values.get("tool_result")
        if (
            snapshot.next
            or snapshot.values.get("execution_status") != "completed"
            or snapshot.values.get("completed_turn_id") != str(turn_id)
            or not isinstance(result, dict)
        ):
            raise ToolExecutionFailedError("El checkpoint no finalizó la ejecución esperada")
        return result

    def _recover_completed_turn(
        self, thread: ConversationThread, turn_id: UUID, agent_key: str
    ) -> TurnResult | PendingApprovalResult:
        pending = self._pending_approval_result(thread, turn_id, replayed=True)
        if pending is not None:
            return pending
        graph = build_agent_graph(
            retriever=self._retriever,
            gemini_client=self._gemini_client,
            checkpointer=self._checkpointer,
            tool_registry=self._tool_registry,
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

    def _pending_approval_result(
        self, thread: ConversationThread, turn_id: UUID, *, replayed: bool
    ) -> PendingApprovalResult | None:
        graph = build_agent_graph(
            retriever=self._retriever,
            gemini_client=self._gemini_client,
            checkpointer=self._checkpointer,
            tool_registry=self._tool_registry,
        )
        snapshot = graph.get_state({"configurable": {"thread_id": thread.langgraph_thread_id}})
        if snapshot.next != ("critical_tools",):
            return None
        values = snapshot.values
        if (
            values.get("execution_status") != "pending_approval"
            or values.get("turn_id") != str(turn_id)
            or not isinstance(values.get("pending_tool_call"), dict)
        ):
            raise TurnRecoveryRequiredError(
                "El checkpoint pendiente no describe una aprobación recuperable"
            )
        paused_thread = self._repository.mark_thread_paused_hil(thread.id)
        return PendingApprovalResult(
            thread=paused_thread,
            turn_id=turn_id,
            tool_call=cast(PendingToolCall, values["pending_tool_call"]),
            replayed=replayed,
        )

    def _guard_no_pending_checkpoint(self, thread: ConversationThread) -> None:
        graph = build_agent_graph(
            retriever=self._retriever,
            gemini_client=self._gemini_client,
            checkpointer=self._checkpointer,
            tool_registry=self._tool_registry,
        )
        snapshot = graph.get_state({"configurable": {"thread_id": thread.langgraph_thread_id}})
        if not snapshot.next:
            return
        if (
            snapshot.next == ("critical_tools",)
            and snapshot.values.get("execution_status") == "pending_approval"
        ):
            self._repository.mark_thread_paused_hil(thread.id)
            raise ThreadPausedError("La conversación está pausada por aprobación humana")
        raise TurnRecoveryRequiredError("La conversación conserva un checkpoint pendiente")

    def _assistant_for_turn(self, state: dict[str, Any], turn_id: str) -> dict[str, Any]:
        for message in reversed(state.get("messages", [])):
            if message.get("role") == "assistant" and message.get("turn_id") == turn_id:
                output_result = self._content_guard.validate_output(message["content"])
                if output_result.decision == GuardDecision.ALLOWED:
                    return message
                raw_payload = {
                    "_content_guard": {
                        "decision": output_result.decision.value,
                        "category": output_result.category,
                        "policy_version": output_result.policy_version,
                    },
                }
                return {
                    **message,
                    "content": output_result.public_message,
                    "raw_payload": raw_payload,
                }
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
            "execution_status": "running",
            "tool_call": None,
            "pending_tool_call": None,
            "tool_result": None,
        }
