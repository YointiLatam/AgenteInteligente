"""Tipos compartidos por los nodos del agente."""

from typing import Any, Literal, NotRequired, TypedDict


class RetrievedDocument(TypedDict):
    """Fragmento recuperado que puede usarse como contexto."""

    id: str
    content: str
    score: float


class AgentMessage(TypedDict):
    """Mensaje generado por el flujo y sus metadatos de proveedor seguros."""

    role: Literal["user", "router", "assistant"]
    content: str
    raw_payload: dict[str, Any]
    turn_id: NotRequired[str]


class ToolCall(TypedDict):
    """Solicitud estructurada de una herramienta, todavía no ejecutada."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]


class PendingToolCall(ToolCall):
    """Solicitud crítica clasificada y persistida antes de la interrupción."""

    effect: Literal["external_write", "database_write", "unknown"]


class AgentState(TypedDict):
    """Estado mínimo compartido por el flujo inicial del agente."""

    query: str
    retrieval_requested: bool
    route: Literal["rag", "reasoning"] | None
    fallback_eligible: NotRequired[bool]
    fallback_attempted: NotRequired[bool]
    documents: list[RetrievedDocument]
    messages: list[AgentMessage]
    conversation_summary: str
    summary_cursor: int
    summary_failure_cursor: int | None
    new_turn: bool
    turn_id: str | None
    completed_turn_id: str | None
    answer: str | None
    error: str | None
    execution_status: NotRequired[Literal["running", "pending_approval", "completed"]]
    tool_call: NotRequired[ToolCall | None]
    pending_tool_call: NotRequired[PendingToolCall | None]
    tool_result: NotRequired[dict[str, Any] | None]
