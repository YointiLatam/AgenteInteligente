"""Generación de respuestas mediante Gemini."""

from app.agent.nodes.memory import conversation_context, estimate_tokens
from app.agent.state import AgentState
from app.integrations.gemini import GeminiGenerationError, GeminiResult, TextGenerator


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


def _generate_reasoning(
    gemini_client: TextGenerator, prompt: str, state: AgentState
) -> GeminiResult:
    generate_with_fallback = getattr(gemini_client, "generate_with_fallback", None)
    if generate_with_fallback is None:
        return gemini_client.generate(prompt)
    return generate_with_fallback(
        prompt,
        fallback_eligible=state.get("fallback_eligible", False),
        fallback_attempted=state.get("fallback_attempted", False),
        estimated_input_tokens=estimate_tokens(prompt),
    )


def reasoning_node(
    state: AgentState, gemini_client: TextGenerator | None = None
) -> dict[str, str | bool | list | None]:
    """Genera respuesta con Gemini; conserva una degradación determinista ante fallos."""
    if gemini_client is None:
        answer = _fallback_answer(state)
        if state.get("turn_id") is None:
            return {"answer": answer}
        raw_payload = {"provider": "gemini", "error": "generation_unavailable"}
    else:
        try:
            result = _generate_reasoning(gemini_client, _reasoning_prompt(state), state)
            answer = result.text.strip() or _fallback_answer(state)
            raw_payload = result.raw_payload
        except GeminiGenerationError as error:
            answer = _fallback_answer(state)
            raw_payload = error.raw_payload
        except Exception:
            answer = _fallback_answer(state)
            raw_payload = {"provider": "gemini", "error": "generation_unavailable"}
    message = {"role": "assistant", "content": answer, "raw_payload": raw_payload}
    if state.get("turn_id") is not None:
        message["turn_id"] = state["turn_id"]
    messages = [*state.get("messages", []), message]
    has_tool_call = state.get("tool_call") is not None
    return {
        "answer": answer,
        "messages": messages,
        "completed_turn_id": None if has_tool_call else state.get("turn_id"),
        "execution_status": "running" if has_tool_call else "completed",
        "fallback_attempted": raw_payload.get("fallback_used", False),
    }
