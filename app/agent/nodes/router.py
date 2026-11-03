"""Clasificación de la ruta del flujo mediante Gemini."""

from typing import Literal

from app.agent.state import AgentState
from app.integrations.gemini import TextGenerator

Route = Literal["rag", "reasoning"]


def _fallback_route(state: AgentState) -> Route:
    return "rag" if state["retrieval_requested"] else "reasoning"


def _router_prompt(state: AgentState) -> str:
    return (
        "Clasifica la consulta para un asistente. Responde solo una palabra: 'rag' si "
        "requiere recuperar contexto documental, o 'reasoning' si puede responderse sin "
        "recuperación. La señal retrieval_requested es "
        f"{state['retrieval_requested']}. Consulta: {state['query']}"
    )


def router_node(
    state: AgentState, gemini_client: TextGenerator | None = None
) -> dict[str, Route | list]:
    """Clasifica entre RAG y razonamiento; degrada a la regla previa ante fallos."""
    fallback = _fallback_route(state)
    if gemini_client is None:
        return {"route": fallback}
    try:
        result = gemini_client.generate(_router_prompt(state))
        candidate = result.text.strip().lower()
        route: Route = candidate if candidate in {"rag", "reasoning"} else fallback
        raw_payload = result.raw_payload
    except Exception:
        route = fallback
        raw_payload = {"provider": "gemini", "error": "classification_unavailable"}
    messages = [
        *state.get("messages", []),
        {"role": "router", "content": route, "raw_payload": raw_payload},
    ]
    return {"route": route, "messages": messages}


def route_after_router(state: AgentState) -> Route:
    """Devuelve el siguiente nodo después de que Router actualiza el estado."""
    return state["route"] or "reasoning"
