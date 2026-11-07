"""Registro explícito y política fail-closed para herramientas del agente."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ToolEffect(StrEnum):
    READ = "read"
    EXTERNAL_WRITE = "external_write"
    DATABASE_WRITE = "database_write"


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class AgentTool:
    name: str
    effect: ToolEffect
    handler: ToolHandler


class UnknownToolError(RuntimeError):
    """La llamada no corresponde a una herramienta registrada."""


class ToolRegistry:
    """Registro inmutable; una herramienta desconocida siempre requiere aprobación."""

    def __init__(self, tools: Mapping[str, AgentTool] | None = None) -> None:
        self._tools = dict(tools or {})
        if any(name != tool.name for name, tool in self._tools.items()):
            raise ValueError("La clave del registro debe coincidir con el nombre de la herramienta")

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def requires_approval(self, name: str) -> bool:
        tool = self.get(name)
        return tool is None or tool.effect != ToolEffect.READ

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.get(name)
        if tool is None:
            raise UnknownToolError(f"Herramienta no registrada: {name}")
        return tool.handler(arguments)


EMPTY_TOOL_REGISTRY = ToolRegistry()
