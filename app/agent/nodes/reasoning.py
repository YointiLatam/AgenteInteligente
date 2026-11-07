"""Generación de respuestas mediante Gemini."""

from app.agent.nodes.memory import conversation_context
from app.agent.state import AgentState
from app.integrations.gemini import TextGenerator


def _fallback_answer(state: AgentState) -> str:
    if state["documents"]:
        context = " ".join(document["content"] for document in state["documents"])
        return f"Respuesta basada en contexto: {context}"
    if state["error"]:
        return f"No fue posible recuperar contexto para: {state['query']}"
    return f"Respuesta sin contexto recuperado para: {state['query']}"


def _reasoning_prompt(state: AgentState) -> str:
    context = "\n".join(document["content"] for document in state["documents"])
    return (
        "Responde en español de forma breve y útil. Usa el contexto solo si está disponible.\n"
        f"Memoria conversacional:\n{conversation_context(state)}\n"
        f"Consulta actual: {state['query']}\n"
        f"Contexto documental: {context or '(sin contexto recuperado)'}"
    )


def reasoning_node(
    state: AgentState, gemini_client: TextGenerator | None = None
) -> dict[str, str | list]:
    """Genera respuesta con Gemini; conserva una degradación determinista ante fallos."""
    if gemini_client is None:
        answer = _fallback_answer(state)
        if state.get("turn_id") is None:
            return {"answer": answer}
        raw_payload = {"provider": "gemini", "error": "generation_unavailable"}
    else:
        try:
            result = gemini_client.generate(_reasoning_prompt(state))
            answer = result.text.strip() or _fallback_answer(state)
            raw_payload = result.raw_payload
        except Exception:
            answer = _fallback_answer(state)
            raw_payload = {"provider": "gemini", "error": "generation_unavailable"}
    message = {"role": "assistant", "content": answer, "raw_payload": raw_payload}
    if state.get("turn_id") is not None:
        message["turn_id"] = state["turn_id"]
    messages = [*state.get("messages", []), message]
    return {
        "answer": answer,
        "messages": messages,
        "completed_turn_id": state.get("turn_id"),
    }
