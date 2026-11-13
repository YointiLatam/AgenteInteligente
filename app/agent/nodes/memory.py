"""Memoria conversacional acotada y resumen acumulativo."""

from __future__ import annotations

from app.agent.state import AgentMessage, AgentState
from app.integrations.gemini import TextGenerator

MAX_RECENT_MESSAGES = 8
MEMORY_TOKEN_BUDGET = 3_000
SUMMARY_TOKEN_BUDGET = 600
RECENT_TOKEN_BUDGET = MEMORY_TOKEN_BUDGET - SUMMARY_TOKEN_BUDGET


def estimate_tokens(text: str) -> int:
    """Estimación conservadora, independiente del proveedor y sin dependencias nuevas."""
    return max(1, (len(text) + 2) // 3)


def _conversation_messages(messages: list[AgentMessage]) -> list[tuple[int, AgentMessage]]:
    return [
        (index, message)
        for index, message in enumerate(messages)
        if message["role"] in {"user", "assistant"}
    ]


def _current_query_index(state: AgentState) -> int | None:
    conversation = _conversation_messages(state.get("messages", []))
    if (
        conversation
        and conversation[-1][1]["role"] == "user"
        and conversation[-1][1]["content"] == state["query"]
    ):
        return conversation[-1][0]
    return None


def _truncate_to_tokens(text: str, limit: int) -> str:
    if estimate_tokens(text) <= limit:
        return text
    return text[: limit * 3].rstrip()


def _recent_window(
    messages: list[AgentMessage], *, exclude_indexes: set[int] | None = None
) -> list[tuple[int, AgentMessage]]:
    """Selecciona desde el final y recorta solo copias destinadas al prompt."""
    selected: list[tuple[int, AgentMessage]] = []
    used_tokens = 0
    excluded = exclude_indexes or set()
    for item in reversed(_conversation_messages(messages)):
        if item[0] in excluded:
            continue
        tokens = estimate_tokens(item[1]["content"])
        if len(selected) >= MAX_RECENT_MESSAGES:
            break
        remaining_tokens = RECENT_TOKEN_BUDGET - used_tokens
        if remaining_tokens <= 0:
            break
        if tokens > remaining_tokens:
            content = _truncate_to_tokens(item[1]["content"], remaining_tokens)
            message = {**item[1], "content": content}
            selected.append((item[0], message))
            break
        selected.append(item)
        used_tokens += tokens
    return list(reversed(selected))


def _format_messages(messages: list[tuple[int, AgentMessage]]) -> str:
    labels = {"user": "Usuario", "assistant": "Asistente"}
    return "\n".join(f"{labels[message['role']]}: {message['content']}" for _, message in messages)


def conversation_context(state: AgentState) -> str:
    """Resume y acota el historial; la consulta actual se agrega aparte al prompt."""
    current_index = _current_query_index(state)
    window = _recent_window(
        state.get("messages", []),
        exclude_indexes={current_index} if current_index is not None else None,
    )
    summary = _truncate_to_tokens(state.get("conversation_summary", ""), SUMMARY_TOKEN_BUDGET)
    parts: list[str] = []
    if summary:
        parts.append(f"Resumen de conversación previa:\n{summary}")
    if window:
        parts.append(f"Mensajes recientes:\n{_format_messages(window)}")
    return "\n\n".join(parts) or "(sin conversación previa)"


def _summary_prompt(previous_summary: str, messages: list[tuple[int, AgentMessage]]) -> str:
    return (
        "Resume la conversación para preservar hechos, preferencias, decisiones, restricciones "
        "y referencias necesarias para turnos posteriores. No inventes información. "
        f"Máximo aproximado: {SUMMARY_TOKEN_BUDGET} tokens.\n"
        f"Resumen anterior:\n{previous_summary or '(ninguno)'}\n\n"
        f"Mensajes nuevos a incorporar:\n{_format_messages(messages)}"
    )


def prepare_memory(
    state: AgentState, gemini_client: TextGenerator | None = None
) -> dict[str, object]:
    """Registra un nuevo turno y comprime solo el tramo antiguo no resumido."""
    messages = list(state.get("messages", []))
    if state.get("new_turn", False):
        message: AgentMessage = {"role": "user", "content": state["query"], "raw_payload": {}}
        if state.get("turn_id") is not None:
            message["turn_id"] = state["turn_id"]
        messages.append(message)

    summary = state.get("conversation_summary", "")
    cursor = state.get("summary_cursor", 0)
    failure_cursor = state.get("summary_failure_cursor")
    conversation = _conversation_messages(messages)
    current_index = _current_query_index({**state, "messages": messages})
    # La consulta actual se incorpora separadamente al prompt y nunca se resume en este turno.
    excluded_indexes = {current_index} if current_index is not None else set()
    recent = _recent_window(messages, exclude_indexes=excluded_indexes)
    recent_indexes = {index for index, _ in recent} | excluded_indexes
    candidates = [
        (index, message)
        for index, message in conversation
        if index >= cursor and index not in recent_indexes
    ]
    unsummarized_messages = [message for index, message in conversation if index >= cursor]
    total_tokens = estimate_tokens(summary) + sum(
        estimate_tokens(message["content"]) for message in unsummarized_messages
    )
    updates: dict[str, object] = {"messages": messages, "new_turn": False}

    # El cursor de fallo identifica el final del bloque; se reintenta solo con material nuevo.
    candidate_end = candidates[-1][0] + 1 if candidates else cursor
    if (
        total_tokens <= MEMORY_TOKEN_BUDGET
        or not candidates
        or gemini_client is None
        or failure_cursor is not None
        and candidate_end <= failure_cursor
    ):
        return updates
    try:
        result = gemini_client.generate(_summary_prompt(summary, candidates))
        compact_summary = _truncate_to_tokens(result.text.strip(), SUMMARY_TOKEN_BUDGET)
        if not compact_summary:
            raise ValueError("empty_summary")
    except Exception:
        updates["summary_failure_cursor"] = candidate_end
        return updates

    updates.update(
        {
            "conversation_summary": compact_summary,
            "summary_cursor": candidate_end,
            "summary_failure_cursor": None,
        }
    )
    return updates
