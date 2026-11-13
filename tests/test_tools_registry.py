import pytest
from pydantic import BaseModel

from app.tools.audit import InMemoryAuditSink, ToolStatus
from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry, build_default_registry

pytestmark = pytest.mark.anyio


class PingInput(BaseModel):
    pass


class PingTool(BaseTool):
    name = "ping"
    description = "Responde pong"
    input_model = PingInput

    async def execute(self, args):
        return {"reply": "pong"}


def test_registra_y_expone_declaraciones_para_gemini():
    registry = ToolRegistry()
    registry.register(PingTool())

    assert [d["name"] for d in registry.declarations()] == ["ping"]


def test_no_permite_nombres_duplicados():
    registry = ToolRegistry()
    registry.register(PingTool())

    with pytest.raises(ValueError, match="ya está registrada"):
        registry.register(PingTool())


async def test_invoca_por_nombre_y_audita():
    sink = InMemoryAuditSink()
    registry = ToolRegistry()
    registry.register(PingTool(audit=sink))

    result = await registry.call("ping", {})

    assert (result.status, result.data) == (ToolStatus.EXECUTED, {"reply": "pong"})
    assert len(sink.entries) == 1


async def test_herramienta_desconocida_falla_con_error_estandar():
    result = await ToolRegistry().call("no_existe", {})

    assert (result.status, result.error.code) == (ToolStatus.FAILED, "unknown_tool")


def test_el_registro_por_defecto_arranca_con_el_catalogo():
    registry = build_default_registry()

    assert all(d["name"] and d["parameters"] for d in registry.declarations())
