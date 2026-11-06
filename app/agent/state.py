"""Tipos compartidos por los nodos del agente."""

from typing import Any, Literal, TypedDict


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


class AgentState(TypedDict):
    """Estado mínimo compartido por el flujo inicial del agente."""

    query: str
    retrieval_requested: bool
    route: Literal["rag", "reasoning"] | None
    documents: list[RetrievedDocument]
    messages: list[AgentMessage]
    conversation_summary: str
    summary_cursor: int
    summary_failure_cursor: int | None
    new_turn: bool
    answer: str | None
    error: str | None
