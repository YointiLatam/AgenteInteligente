from types import SimpleNamespace

import pytest

from app.agent.nodes.reasoning import reasoning_node
from app.agent.nodes.router import router_node
from app.agent.state import AgentState
from app.integrations.gemini import GeminiClient, GeminiNotConfigured, GeminiResult
from app.integrations.settings import IntegrationsSettings


class FakeGemini:
    def __init__(self, result: GeminiResult | Exception):
        self.result = result
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> GeminiResult:
        self.prompts.append(prompt)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _state(**updates) -> AgentState:
    state: AgentState = {
        "query": "¿Cómo funciona JWT?",
        "retrieval_requested": False,
        "route": None,
        "documents": [],
        "messages": [],
        "answer": None,
        "error": None,
    }
    return {**state, **updates}


def _result(text: str) -> GeminiResult:
    return GeminiResult(
        text=text,
        raw_payload={
            "provider": "gemini",
            "model": "gemini-3.5-flash",
            "input_tokens": 10,
            "output_tokens": 3,
            "total_tokens": 13,
            "latency_ms": 20,
        },
    )


def test_router_usa_clasificacion_y_registra_consumo():
    client = FakeGemini(_result("rag"))

    update = router_node(_state(), client)

    assert update["route"] == "rag"
    assert update["messages"][-1] == {
        "role": "router",
        "content": "rag",
        "raw_payload": _result("rag").raw_payload,
    }
    assert "¿Cómo funciona JWT?" in client.prompts[0]


def test_router_degrada_a_regla_previa_si_gemini_falla():
    update = router_node(_state(retrieval_requested=True), FakeGemini(RuntimeError()))

    assert update["route"] == "rag"
    assert update["messages"][-1]["raw_payload"]["error"] == "classification_unavailable"


def test_reasoning_usa_respuesta_y_registra_consumo():
    update = reasoning_node(_state(), FakeGemini(_result("Respuesta Gemini.")))

    assert update["answer"] == "Respuesta Gemini."
    assert update["messages"][-1]["role"] == "assistant"
    assert update["messages"][-1]["raw_payload"]["total_tokens"] == 13


def test_reasoning_degrada_a_respuesta_determinista_si_gemini_falla():
    update = reasoning_node(_state(), FakeGemini(RuntimeError()))

    assert update["answer"] == "Respuesta sin contexto recuperado para: ¿Cómo funciona JWT?"
    assert update["messages"][-1]["raw_payload"]["error"] == "generation_unavailable"


def test_cliente_rechaza_credencial_ausente(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(GeminiNotConfigured):
        GeminiClient(IntegrationsSettings(_env_file=None))


def test_cliente_convierte_respuesta_del_sdk_en_payload_seguro(monkeypatch):
    response = SimpleNamespace(
        text="OK",
        usage_metadata=SimpleNamespace(
            prompt_token_count=4,
            candidates_token_count=1,
            total_token_count=5,
        ),
    )
    sdk = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_: response))
    monkeypatch.setattr("app.integrations.gemini.genai.Client", lambda **_: sdk)

    settings = IntegrationsSettings(_env_file=None, gemini_api_key="secret")

    result = GeminiClient(settings).generate("OK")

    assert result.text == "OK"
    assert result.raw_payload["provider"] == "gemini"
    assert result.raw_payload["input_tokens"] == 4
    assert "secret" not in repr(result.raw_payload)


def test_cliente_admite_respuesta_sin_metadatos_de_uso(monkeypatch):
    response = SimpleNamespace(text="OK", usage_metadata=None)
    sdk = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_: response))
    monkeypatch.setattr("app.integrations.gemini.genai.Client", lambda **_: sdk)

    settings = IntegrationsSettings(_env_file=None, gemini_api_key="secret")

    result = GeminiClient(settings).generate("OK")

    assert result.text == "OK"
    assert result.raw_payload["input_tokens"] is None
    assert result.raw_payload["output_tokens"] is None
    assert result.raw_payload["total_tokens"] is None
