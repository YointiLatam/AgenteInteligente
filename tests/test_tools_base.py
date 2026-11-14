from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel, Field

from app.tools.audit import InMemoryAuditSink, ToolStatus
from app.tools.base import BaseTool, ToolError

pytestmark = pytest.mark.anyio


class EchoInput(BaseModel):
    text: str = Field(description="Texto a repetir")
    times: int | None = Field(default=None, description="Repeticiones")


class EchoTool(BaseTool):
    name = "echo"
    description = "Repite un texto"
    input_model = EchoInput

    def __init__(self, behavior=None, **kwargs):
        super().__init__(**kwargs)
        self.behavior = behavior

    async def execute(self, args):
        if self.behavior:
            raise self.behavior
        return {"echo": args.text}


class CriticalTool(EchoTool):
    name = "critical_echo"
    critical = True


async def _no_sleep(seconds: float) -> None:
    return None


def _tool(cls=EchoTool, **kwargs):
    sink = InMemoryAuditSink()
    kwargs.setdefault("sleep", _no_sleep)
    return cls(audit=sink, **kwargs), sink


async def test_ejecucion_exitosa_se_audita_con_latencia():
    tool, sink = _tool()

    result = await tool.run({"text": "hola"})

    assert (result.status, result.data) == (ToolStatus.EXECUTED, {"echo": "hola"})
    [entry] = sink.entries
    assert (entry.tool_name, entry.status) == ("echo", ToolStatus.EXECUTED)
    assert entry.input_parameters == {"text": "hola"}
    assert entry.execution_time_ms >= 0


async def test_argumentos_invalidos_fallan_y_quedan_auditados():
    tool, sink = _tool()

    result = await tool.run({"times": 2})

    assert result.status == ToolStatus.FAILED
    assert result.error.code == "invalid_arguments"
    assert "text" in result.error.message
    assert sink.entries[0].status == ToolStatus.FAILED


async def test_error_esperado_conserva_codigo_y_se_audita():
    tool, sink = _tool(behavior=ToolError("not_found", "Pedido inexistente"))

    result = await tool.run({"text": "x"})

    assert (result.error.code, result.error.message) == ("not_found", "Pedido inexistente")
    assert sink.entries[0].status == ToolStatus.FAILED


async def test_error_inesperado_no_filtra_detalles():
    tool, sink = _tool(behavior=RuntimeError("password=abc123"))

    result = await tool.run({"text": "x"})

    assert result.error.code == "internal_error"
    assert "abc123" not in result.model_dump_json()
    assert "abc123" not in str(sink.entries[0].output_result)


@pytest.mark.parametrize(("status", "retryable"), [(503, True), (429, True), (400, False)])
async def test_errores_http_indican_si_se_puede_reintentar(status, retryable):
    request = httpx.Request("GET", "https://api.test")
    error = httpx.HTTPStatusError("x", request=request, response=httpx.Response(status))
    tool, _ = _tool(behavior=error)

    result = await tool.run({"text": "x"})

    assert (result.error.code, result.error.retryable) == ("upstream_error", retryable)


async def test_herramienta_critica_queda_pendiente_sin_ejecutarse():
    tool, sink = _tool(CriticalTool)

    result = await tool.run({"text": "publicar"})

    assert result.status == ToolStatus.PENDING_APPROVAL
    assert [e.status for e in sink.entries] == [ToolStatus.PENDING_APPROVAL]


async def test_herramienta_critica_aprobada_se_ejecuta_y_registra_quien_aprobo():
    tool, sink = _tool(CriticalTool)
    approver = uuid4()

    result = await tool.run({"text": "publicar"}, approved=True, approved_by=approver)

    assert result.status == ToolStatus.EXECUTED
    assert sink.entries[0].approved_by == approver


async def test_si_falla_la_auditoria_el_resultado_se_entrega():
    class BrokenSink:
        def record(self, entry):
            raise OSError("db caída")

    result = await EchoTool(audit=BrokenSink()).run({"text": "hola"})

    assert result.status == ToolStatus.EXECUTED


async def test_sin_reintentos_el_resultado_y_la_auditoria_no_cambian():
    ok_tool, ok_sink = _tool()
    bad_tool, bad_sink = _tool(behavior=ToolError("not_found", "Pedido inexistente"))

    ok = await ok_tool.run({"text": "hola"})
    bad = await bad_tool.run({"text": "x"})

    assert ok.model_dump() == {"status": "executed", "data": {"echo": "hola"}, "error": None}
    assert bad.error.model_dump() == {
        "code": "not_found",
        "message": "Pedido inexistente",
        "retryable": False,
    }
    assert ok_sink.entries[0].output_result == {"echo": "hola"}
    assert bad_sink.entries[0].output_result == {"error": bad.error.model_dump()}


async def test_la_espera_entre_reintentos_usa_el_sleep_inyectado_y_se_audita():
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    request = httpx.Request("GET", "https://api.test")
    error = httpx.HTTPStatusError("x", request=request, response=httpx.Response(503))
    tool, sink = _tool(behavior=error, sleep=fake_sleep)

    result = await tool.run({"text": "x"})

    assert result.status == ToolStatus.FAILED
    assert len(waits) == 2
    assert [r["wait_seconds"] for r in sink.entries[0].output_result["retries"]] == [
        round(w, 3) for w in waits
    ]
