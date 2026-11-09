"""Clasificación de la ruta del flujo mediante Gemini."""

import re
from typing import Literal

from app.agent.nodes.memory import conversation_context
from app.agent.state import AgentState
from app.integrations.gemini import TextGenerator

Route = Literal["rag", "reasoning"]

_COMPLEXITY_TERMS = (
    "analiza",
    "compar",
    "evalúa",
    "evalua",
    "ventaja",
    "desventaja",
    "alternativa",
    "trade-off",
    "tradeoff",
    "justifica",
    "paso a paso",
)
_MULTI_SOURCE_TERMS = ("relaciona", "contrasta", "sintetiza", "varias fuentes")


def _fallback_route(state: AgentState) -> Route:
    return "rag" if state["retrieval_requested"] else "reasoning"


def _router_prompt(state: AgentState) -> str:
    return (
        "Clasifica la consulta para un asistente. Responde solo una palabra: 'rag' si "
        "requiere recuperar contexto documental, o 'reasoning' si puede responderse sin "
        "recuperación. La señal retrieval_requested es "
        f"{state['retrieval_requested']}.\n"
        f"Memoria conversacional:\n{conversation_context(state)}\n"
        f"Consulta actual: {state['query']}"
    )


def _fallback_eligible(state: AgentState) -> bool:
    """Marca señales explícitas de razonamiento complejo sin otra llamada al modelo."""
    query = state["query"].lower()
    multiple_subtasks = len(re.findall(r"(?:^|[\n;])\s*(?:\d+[.)-]|[-*])", query)) >= 2
    multiple_questions = query.count("?") >= 2
    complex_request = any(term in query for term in _COMPLEXITY_TERMS)
    multiple_sources = state["retrieval_requested"] and any(
        term in query for term in _MULTI_SOURCE_TERMS
    )
    return multiple_subtasks or multiple_questions or complex_request or multiple_sources


def router_node(
    state: AgentState, gemini_client: TextGenerator | None = None
) -> dict[str, Route | bool | list]:
    """Clasifica entre RAG y razonamiento; degrada a la regla previa ante fallos."""
    fallback = _fallback_route(state)
    fallback_eligible = _fallback_eligible(state)
    if gemini_client is None:
        return {"route": fallback, "fallback_eligible": fallback_eligible}
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
    return {
        "route": route,
        "fallback_eligible": fallback_eligible,
        "messages": messages,
    }


def route_after_router(state: AgentState) -> Route:
    """Devuelve el siguiente nodo después de que Router actualiza el estado."""
    return state["route"] or "reasoning"
