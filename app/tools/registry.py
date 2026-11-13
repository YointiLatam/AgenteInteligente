import importlib
import pkgutil

from app.integrations.settings import IntegrationsSettings
from app.tools import catalog
from app.tools.audit import AuditSink, ToolStatus
from app.tools.base import BaseTool, ToolError, ToolErrorInfo, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"La herramienta '{tool.name}' ya está registrada")
        tool.declaration()  # falla al registrar, no en mitad de una conversación
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def declarations(self) -> list[dict]:
        """Lista lista para `tools=[{"function_declarations": ...}]` de Gemini."""
        return [tool.declaration() for tool in self._tools.values()]

    async def call(self, name: str, args: dict, **options) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            error = ToolError("unknown_tool", f"No existe la herramienta '{name}'")
            info = ToolErrorInfo(code=error.code, message=error.message)
            return ToolResult(status=ToolStatus.FAILED, error=info)
        return await tool.run(args, **options)


def build_default_registry(
    audit: AuditSink | None = None, settings: IntegrationsSettings | None = None
) -> ToolRegistry:
    """Registra todas las herramientas de `app/tools/catalog` (cada módulo expone `TOOLS`)."""
    registry = ToolRegistry()
    for module in pkgutil.iter_modules(catalog.__path__):
        for tool_class in importlib.import_module(f"{catalog.__name__}.{module.name}").TOOLS:
            registry.register(tool_class(audit=audit, settings=settings))
    return registry
