"""Recuperación de contexto intercambiable para el agente."""

from collections.abc import Callable

from app.agent.state import AgentState, RetrievedDocument

Retriever = Callable[[str], list[RetrievedDocument]]


def empty_retriever(_: str) -> list[RetrievedDocument]:
    """Recuperador inicial sin infraestructura externa."""
    return []


def rag_retrieval_node(
    state: AgentState, retriever: Retriever = empty_retriever
) -> dict[str, list[RetrievedDocument] | str | None]:
    """Recupera contexto y degrada de forma controlada ante errores."""
    try:
        return {"documents": retriever(state["query"]), "error": None}
    except Exception:
        return {"documents": [], "error": "retrieval_unavailable"}
