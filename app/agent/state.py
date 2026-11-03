"""Tipos compartidos por los nodos del agente."""

from typing import Literal, TypedDict


class RetrievedDocument(TypedDict):
    """Fragmento recuperado que puede usarse como contexto."""

    id: str
    content: str
    score: float


class AgentState(TypedDict):
    """Estado mínimo compartido por el flujo inicial del agente."""

    query: str
    retrieval_requested: bool
    route: Literal["rag", "reasoning"] | None
    documents: list[RetrievedDocument]
    answer: str | None
    error: str | None
