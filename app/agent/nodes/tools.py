"""Enrutamiento y ejecución de herramientas con frontera HITL explícita."""

from __future__ import annotations

from typing import Literal

from app.agent.state import AgentState, PendingToolCall, ToolCall
from app.agent.tools import ToolEffect, ToolRegistry
from app.observability.telemetry import get_telemetry

ToolRoute = Literal["end", "safe_tools", "mark_pending_approval"]


def _tool_call(state: AgentState) -> ToolCall:
    call = state.get("tool_call")
    if call is None:
        raise ValueError("El estado no contiene una llamada de herramienta")
    return call


def route_after_reasoning(state: AgentState, *, registry: ToolRegistry) -> ToolRoute:
    call = state.get("tool_call")
    if call is None:
        return "end"
    if registry.requires_approval(call["tool_name"]):
        return "mark_pending_approval"
    return "safe_tools"


def mark_pending_approval(state: AgentState, *, registry: ToolRegistry) -> dict[str, object]:
    call = _tool_call(state)
    tool = registry.get(call["tool_name"])
    effect: Literal["external_write", "database_write", "unknown"]
    if tool is None:
        effect = "unknown"
    elif tool.effect == ToolEffect.EXTERNAL_WRITE:
        effect = "external_write"
    elif tool.effect == ToolEffect.DATABASE_WRITE:
        effect = "database_write"
    else:  # La ruta segura no debe alcanzar este nodo.
        raise ValueError("Una herramienta de lectura no requiere aprobación")
    pending: PendingToolCall = {**call, "effect": effect}
    result = {
        "execution_status": "pending_approval",
        "pending_tool_call": pending,
    }
    _tool_event(call["tool_name"], effect, "pending_approval")
    return result


def safe_tools(state: AgentState, *, registry: ToolRegistry) -> dict[str, object]:
    call = _tool_call(state)
    tool = registry.get(call["tool_name"])
    if tool is None or tool.effect != ToolEffect.READ:
        raise ValueError("Solo las herramientas de lectura pueden usar la ruta segura")
    telemetry = get_telemetry()
    with telemetry.observation(
        "tool.agent", as_type="tool", metadata={"tool_name": call["tool_name"], "effect": "read"}
    ) as span:
        result = registry.execute(call["tool_name"], call["arguments"])
        telemetry.update(span, {"outcome": "success", "tool_status": "executed"})
    return {
        "tool_call": None,
        "tool_result": result,
        "execution_status": "completed",
        "completed_turn_id": state.get("turn_id"),
    }


def critical_tools(state: AgentState, *, registry: ToolRegistry) -> dict[str, object]:
    call = _tool_call(state)
    tool = registry.get(call["tool_name"])
    if tool is None or tool.effect == ToolEffect.READ:
        raise ValueError("La herramienta crítica no está disponible para ejecución")
    telemetry = get_telemetry()
    with telemetry.observation(
        "tool.agent",
        as_type="tool",
        metadata={"tool_name": call["tool_name"], "effect": tool.effect.value},
    ) as span:
        result = registry.execute(call["tool_name"], call["arguments"])
        telemetry.update(span, {"outcome": "success", "tool_status": "executed"})
    return {
        "tool_call": None,
        "pending_tool_call": None,
        "tool_result": result,
        "execution_status": "completed",
        "completed_turn_id": state.get("turn_id"),
    }


def _tool_event(name: str, effect: str, status: str) -> None:
    telemetry = get_telemetry()
    with telemetry.observation(
        "tool.agent", as_type="tool", metadata={"tool_name": name, "effect": effect}
    ) as span:
        telemetry.update(span, {"outcome": status, "tool_status": status})
