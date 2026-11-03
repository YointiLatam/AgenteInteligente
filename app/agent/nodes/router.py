"""Enrutamiento determinista del flujo del agente."""

from typing import Literal

from app.agent.state import AgentState

Route = Literal["rag", "reasoning"]


def router_node(state: AgentState) -> dict[str, Route]:
    """Selecciona RAG solo cuando la invocación solicita recuperación."""
    route: Route = "rag" if state["retrieval_requested"] else "reasoning"
    return {"route": route}


def route_after_router(state: AgentState) -> Route:
    """Devuelve el siguiente nodo después de que Router actualiza el estado."""
    return state["route"] or "reasoning"
