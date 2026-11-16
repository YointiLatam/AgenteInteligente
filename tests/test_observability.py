from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_agent_graph, invoke_agent
from app.agent.state import AgentState
from app.agent.tools import AgentTool, ToolEffect, ToolRegistry
from app.integrations.gemini import GeminiClient
from app.integrations.settings import IntegrationsSettings
from app.observability.telemetry import GeminiCost, Telemetry, set_telemetry
from app.tools.audit import ToolStatus
from app.tools.base import BaseTool


class RecordedSpan:
    def __init__(self, data: dict) -> None:
        self.data = data

    def update(self, *, metadata=None, **kwargs) -> None:
        self.data.setdefault("updates", []).append({"metadata": metadata or {}, **kwargs})


class RecordingLangfuse:
    def __init__(self) -> None:
        self.observations: list[dict] = []

    def create_trace_id(self, *, seed: str | None = None) -> str:
        return f"trace-{seed}" if seed else "trace"

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        data = {"kwargs": kwargs}
        self.observations.append(data)
        yield RecordedSpan(data)


class BrokenLangfuse:
    def create_trace_id(self, *, seed: str | None = None) -> str:
        raise RuntimeError("telemetry offline")


class FakeGemini:
    def generate(self, _: str):
        from app.integrations.gemini import GeminiResult

        return GeminiResult(text="respuesta", raw_payload={"provider": "fake"})


@pytest.fixture(autouse=True)
def reset_telemetry():
    set_telemetry(None)
    yield
    set_telemetry(None)


def _state() -> AgentState:
    return {
        "query": "consulta privada que no debe salir",
        "retrieval_requested": False,
        "route": None,
        "documents": [],
        "messages": [],
        "conversation_summary": "",
        "summary_cursor": 0,
        "summary_failure_cursor": None,
        "new_turn": False,
        "turn_id": "turn-1",
        "completed_turn_id": None,
        "answer": None,
        "error": None,
        "execution_status": "running",
        "tool_call": None,
        "pending_tool_call": None,
        "tool_result": None,
    }


def test_disabled_telemetry_is_noop():
    telemetry = Telemetry()
    with telemetry.observation("private", metadata={"query": "secreto"}) as span:
        telemetry.update(span, {"answer": "secreto"})


def test_metadata_uses_an_explicit_technical_allow_list():
    client = RecordingLangfuse()
    telemetry = Telemetry(client, enabled=True)

    with telemetry.observation(
        "safe",
        metadata={
            "thread_id": "thread-1",
            "tool_status": "executed",
            "prompt": "contenido privado",
            "message": "contenido privado",
            "api_key": "secret",
            "password": "secret",
        },
    ) as span:
        telemetry.update(span, {"route": "reasoning", "output": "contenido privado"})

    [observation] = client.observations
    assert observation["kwargs"]["metadata"] == {
        "thread_id": "thread-1",
        "tool_status": "executed",
    }
    assert observation["updates"][-1]["metadata"] == {"route": "reasoning"}


def test_update_rejects_input_output_and_arbitrary_kwargs():
    telemetry = Telemetry()
    with telemetry.observation("safe") as span:
        with pytest.raises(TypeError):
            telemetry.update(span, input="contenido privado")
        with pytest.raises(TypeError):
            telemetry.update(span, custom="contenido privado")


def test_failing_telemetry_does_not_interrupt_graph():
    set_telemetry(Telemetry(BrokenLangfuse(), enabled=True))

    result = invoke_agent(_state(), gemini_client=FakeGemini(), recursion_limit=3)

    assert result["answer"] == "respuesta"


def test_missing_pricing_does_not_invent_cost():
    costs, metadata = Telemetry().gemini_cost("unknown", 10, 5)

    assert costs is None
    assert metadata == {"cost_available": False}


def test_graph_records_nodes_without_state_contents():
    client = RecordingLangfuse()
    set_telemetry(Telemetry(client, enabled=True))

    invoke_agent(
        _state(),
        gemini_client=FakeGemini(),
        checkpointer=InMemorySaver(),
        thread_id="thread-1",
        recursion_limit=3,
    )

    names = [item["kwargs"]["name"] for item in client.observations]
    assert "agent.graph" in names
    assert {"agent.node.prepare_memory", "agent.node.router", "agent.node.reasoning"} <= set(names)
    serialized = repr(client.observations)
    assert "consulta privada que no debe salir" not in serialized
    assert "documents" not in serialized


def test_gemini_records_usage_cost_and_no_prompt(monkeypatch):
    client = RecordingLangfuse()
    set_telemetry(Telemetry(client, enabled=True, costs={"actual-model": GeminiCost(2.0, 4.0)}))
    settings = IntegrationsSettings(gemini_api_key="test-key")
    gemini = GeminiClient(settings)
    response = SimpleNamespace(
        text="respuesta privada",
        model_version="actual-model",
        candidates=[],
        prompt_feedback=SimpleNamespace(block_reason=None),
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            total_token_count=15,
            thoughts_token_count=None,
            cached_content_token_count=None,
        ),
    )
    gemini._completion = lambda **_: response

    gemini.generate("prompt privado")

    [generation] = [
        item for item in client.observations if item["kwargs"]["name"] == "llm.generate"
    ]
    update = generation["updates"][-1]
    assert update["usage_details"] == {"input": 10, "output": 5, "total": 15}
    assert update["cost_details"]["total"] == pytest.approx(0.00004)
    assert "prompt privado" not in repr(generation)
    assert "respuesta privada" not in repr(generation)


def test_openai_sin_tarifa_verificada_no_hereda_costo_de_gemini():
    client = RecordingLangfuse()
    set_telemetry(
        Telemetry(
            client,
            enabled=True,
            costs={"gemini/gemini-3.5-flash": GeminiCost(2.0, 4.0)},
        )
    )
    response = SimpleNamespace(
        model="gpt-reportado-sin-prefijo",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="respuesta privada"), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    generator = GeminiClient(
        IntegrationsSettings(openai_api_key="openai-secret"),
        model="openai/gpt-configurado",
        completion_fn=lambda **_: response,
    )

    generator.generate("prompt privado")

    [generation] = [
        item for item in client.observations if item["kwargs"]["name"] == "llm.generate"
    ]
    update = generation["updates"][-1]
    assert update["metadata"]["provider"] == "openai"
    assert update["metadata"]["cost_available"] is False
    assert "cost_details" not in update
    assert update["usage_details"] == {"input": 10, "output": 5, "total": 15}
    assert "prompt privado" not in repr(generation)
    assert "respuesta privada" not in repr(generation)
    assert "openai-secret" not in repr(generation)


def test_litellm_normalizado_registra_proveedor_modelo_tokens_y_resultado_sin_contenido():
    client = RecordingLangfuse()
    set_telemetry(Telemetry(client, enabled=True))
    response = SimpleNamespace(
        model="anthropic/claude-haiku",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="respuesta privada"), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
    )
    generator = GeminiClient(
        IntegrationsSettings(anthropic_api_key="anthropic-secret"),
        model="anthropic/claude-haiku",
        completion_fn=lambda **_: response,
    )

    generator.generate("prompt privado")

    [generation] = [
        item for item in client.observations if item["kwargs"]["name"] == "llm.generate"
    ]
    update = generation["updates"][-1]
    assert generation["kwargs"]["metadata"]["provider"] == "anthropic"
    assert generation["kwargs"]["model"] == "anthropic/claude-haiku"
    assert update["metadata"] == {
        "outcome": "success",
        "provider": "anthropic",
        "fallback_used": False,
        "used_model": "anthropic/claude-haiku",
        "input_tokens": 7,
        "output_tokens": 3,
        "total_tokens": 10,
        "latency_ms": update["metadata"]["latency_ms"],
        "finish_reason": "STOP",
        "prompt_block_reason": None,
        "cost_available": False,
    }
    assert update["usage_details"] == {"input": 7, "output": 3, "total": 10}
    assert "prompt privado" not in repr(generation)
    assert "respuesta privada" not in repr(generation)
    assert "anthropic-secret" not in repr(generation)


@pytest.mark.parametrize(
    ("text", "finish_reason", "prompt_block_reason", "expected"),
    [
        ("respuesta", None, None, "success"),
        ("", "SAFETY", None, "blocked"),
        ("", None, "SAFETY", "blocked"),
        ("", None, None, "unusable"),
        ("respuesta parcial", "MAX_TOKENS", None, "unusable"),
    ],
)
def test_gemini_observability_classifies_provider_response(
    monkeypatch, text, finish_reason, prompt_block_reason, expected
):
    client = RecordingLangfuse()
    set_telemetry(Telemetry(client, enabled=True))
    gemini = GeminiClient(IntegrationsSettings(gemini_api_key="test-key"))
    candidates = [] if finish_reason is None else [SimpleNamespace(finish_reason=finish_reason)]
    response = SimpleNamespace(
        text=text,
        model_version="actual-model",
        candidates=candidates,
        prompt_feedback=SimpleNamespace(block_reason=prompt_block_reason),
        usage_metadata=SimpleNamespace(
            prompt_token_count=1,
            candidates_token_count=1,
            total_token_count=2,
            thoughts_token_count=None,
            cached_content_token_count=None,
        ),
    )
    gemini._completion = lambda **_: response

    gemini.generate("prompt privado")

    [generation] = [
        item for item in client.observations if item["kwargs"]["name"] == "llm.generate"
    ]
    assert generation["updates"][-1]["metadata"]["outcome"] == expected


def test_gemini_observability_marks_provider_error(monkeypatch):
    client = RecordingLangfuse()
    set_telemetry(Telemetry(client, enabled=True))
    gemini = GeminiClient(IntegrationsSettings(gemini_api_key="test-key"))
    gemini._completion = lambda **_: (_ for _ in ()).throw(TimeoutError("provider error"))

    with pytest.raises(TimeoutError):
        gemini.generate("prompt privado")

    [generation] = [
        item for item in client.observations if item["kwargs"]["name"] == "llm.generate"
    ]
    assert any(update["metadata"].get("outcome") == "error" for update in generation["updates"])


def test_fallback_generation_is_labeled_without_prompt(monkeypatch):
    client = RecordingLangfuse()
    set_telemetry(Telemetry(client, enabled=True))
    gemini = GeminiClient(IntegrationsSettings(gemini_api_key="test-key"))
    response = SimpleNamespace(
        text="respuesta",
        model_version="fallback-model",
        candidates=[],
        prompt_feedback=SimpleNamespace(block_reason=None),
        usage_metadata=SimpleNamespace(
            prompt_token_count=1,
            candidates_token_count=1,
            total_token_count=2,
            thoughts_token_count=None,
            cached_content_token_count=None,
        ),
    )
    gemini._completion = lambda **_: response

    gemini._generate_model("prompt privado", "gemini/fallback-model", fallback_used=True)

    [generation] = [
        item for item in client.observations if item["kwargs"]["name"] == "llm.generate"
    ]
    assert generation["kwargs"]["metadata"]["fallback_used"] is True
    assert "prompt privado" not in repr(generation)


def test_agent_tool_records_pending_and_success():
    client = RecordingLangfuse()
    set_telemetry(Telemetry(client, enabled=True))
    registry = ToolRegistry(
        {
            "read": AgentTool("read", ToolEffect.READ, lambda _: {"ok": True}),
            "write": AgentTool("write", ToolEffect.EXTERNAL_WRITE, lambda _: {"ok": True}),
        }
    )
    safe = _state()
    safe["tool_call"] = {"call_id": "safe", "tool_name": "read", "arguments": {}}
    graph = build_agent_graph(gemini_client=FakeGemini(), tool_registry=registry)
    graph.invoke(safe, {"recursion_limit": 8})
    pending = _state()
    pending["tool_call"] = {"call_id": "pending", "tool_name": "write", "arguments": {}}
    result = graph.invoke(pending, {"recursion_limit": 8})

    statuses = [
        update["metadata"].get("tool_status")
        for item in client.observations
        if item["kwargs"]["name"] == "tool.agent"
        for update in item.get("updates", [])
    ]
    assert "executed" in statuses
    assert "pending_approval" in statuses
    assert result["execution_status"] == "pending_approval"


@pytest.mark.anyio
async def test_http_tool_failure_is_not_success():
    from pydantic import BaseModel

    class Input(BaseModel):
        value: str

    class FailingTool(BaseTool):
        name = "failing"
        description = "falla"
        input_model = Input

        async def execute(self, args):
            raise RuntimeError("secret failure")

    client = RecordingLangfuse()
    set_telemetry(Telemetry(client, enabled=True))
    result = await FailingTool().run({"value": "sensitive"})

    assert result.status == ToolStatus.FAILED
    [tool] = [item for item in client.observations if item["kwargs"]["name"] == "tool.http"]
    assert tool["updates"][-1]["metadata"]["outcome"] == "failed"
    assert "sensitive" not in repr(tool)
