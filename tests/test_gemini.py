from types import SimpleNamespace

import pytest
from google.genai import types

from app.agent.nodes.reasoning import reasoning_node
from app.agent.nodes.router import router_node
from app.agent.state import AgentState
from app.integrations.gemini import (
    GeminiClient,
    GeminiGenerationError,
    GeminiNotConfigured,
    GeminiResult,
)
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


class ProviderError(RuntimeError):
    def __init__(self, code: int) -> None:
        super().__init__(str(code))
        self.code = code


class Responses:
    def __init__(self, *responses: object) -> None:
        self._responses = iter(responses)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


def _response(
    text: str,
    *,
    finish_reason: str | None = None,
    model_version: str | None = None,
    prompt_block_reason: object | None = None,
) -> SimpleNamespace:
    choices = [
        SimpleNamespace(
            message=SimpleNamespace(content=text),
            finish_reason=finish_reason,
        )
    ]
    return SimpleNamespace(
        model=model_version,
        choices=choices,
        prompt_feedback=SimpleNamespace(block_reason=prompt_block_reason),
        usage=SimpleNamespace(
            prompt_tokens=4,
            completion_tokens=2,
            total_tokens=6,
        ),
    )


def _fallback_settings(**updates) -> IntegrationsSettings:
    return IntegrationsSettings(
        _env_file=None,
        **{
            "gemini_api_key": "secret",
            "gemini_fallback_enabled": True,
            **updates,
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


def test_cliente_convierte_respuesta_de_litellm_en_payload_seguro():
    response = _response("OK", model_version="gemini/actual")

    settings = IntegrationsSettings(_env_file=None, gemini_api_key="secret")

    result = GeminiClient(settings, completion_fn=lambda **_: response).generate("OK")

    assert result.text == "OK"
    assert result.raw_payload["provider"] == "gemini"
    assert result.raw_payload["input_tokens"] == 4
    assert "secret" not in repr(result.raw_payload)


def test_cliente_admite_respuesta_sin_metadatos_de_uso():
    response = SimpleNamespace(
        model="gemini/test",
        choices=[SimpleNamespace(message=SimpleNamespace(content="OK"), finish_reason=None)],
        usage=None,
    )

    settings = IntegrationsSettings(_env_file=None, gemini_api_key="secret")

    result = GeminiClient(settings, completion_fn=lambda **_: response).generate("OK")

    assert result.text == "OK"
    assert result.raw_payload["input_tokens"] is None
    assert result.raw_payload["output_tokens"] is None
    assert result.raw_payload["total_tokens"] is None


def test_fallback_conserva_flash_principal_cuando_responde(monkeypatch):
    responses = Responses(_response("Respuesta Flash."))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    result = GeminiClient(_fallback_settings()).generate_with_fallback(
        "Consulta compleja", fallback_eligible=True, estimated_input_tokens=10
    )

    assert [call["model"] for call in responses.calls] == ["gemini/gemini-3.5-flash"]
    assert result.raw_payload["used_model"] == "gemini/gemini-3.5-flash"
    assert result.raw_payload["fallback_used"] is False
    assert result.raw_payload["attempt_count"] == 1


def test_error_recuperable_de_flash_activa_un_fallback(monkeypatch):
    responses = Responses(ProviderError(503), _response("Respuesta 3.8."))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    result = GeminiClient(_fallback_settings()).generate_with_fallback(
        "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=10
    )

    assert [call["model"] for call in responses.calls] == [
        "gemini/gemini-3.5-flash",
        "gemini/gemini-3.8-flash",
    ]
    assert result.text == "Respuesta 3.8."
    assert result.raw_payload["used_model"] == "gemini/gemini-3.8-flash"
    assert result.raw_payload["fallback_reason"] == "primary_error_recoverable"
    assert result.raw_payload["attempt_count"] == 2
    assert result.raw_payload["total_tokens"] == 6
    assert responses.calls[1]["max_tokens"] == 2_048


def test_consulta_no_elegible_no_escala_aunque_flash_falle(monkeypatch):
    responses = Responses(ProviderError(503))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    with pytest.raises(GeminiGenerationError):
        GeminiClient(_fallback_settings()).generate_with_fallback(
            "Dato simple", fallback_eligible=False, estimated_input_tokens=10
        )

    assert len(responses.calls) == 1


def test_respuesta_vacia_activa_fallback_solo_si_es_elegible(monkeypatch):
    responses = Responses(_response(""), _response("Respuesta mejorada."))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    result = GeminiClient(_fallback_settings()).generate_with_fallback(
        "Compara alternativas", fallback_eligible=True, estimated_input_tokens=10
    )

    assert result.raw_payload["fallback_reason"] == "empty_response"
    assert result.raw_payload["attempts"][0]["outcome"] == "unusable"
    assert len(responses.calls) == 2


def test_bloqueo_de_seguridad_no_activa_fallback(monkeypatch):
    responses = Responses(_response("", finish_reason="SAFETY"))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    with pytest.raises(GeminiGenerationError) as raised:
        GeminiClient(_fallback_settings()).generate_with_fallback(
            "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=10
        )

    assert len(responses.calls) == 1
    assert raised.value.raw_payload["fallback_used"] is False
    assert raised.value.raw_payload["fallback_reason"] == "safety_blocked"
    assert raised.value.raw_payload["attempts"][0]["outcome"] == "blocked"


def test_bloqueo_del_prompt_sin_candidatos_no_activa_fallback(monkeypatch):
    responses = Responses(_response("", prompt_block_reason=types.BlockedReason.SAFETY))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    with pytest.raises(GeminiGenerationError) as raised:
        GeminiClient(_fallback_settings()).generate_with_fallback(
            "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=10
        )

    assert len(responses.calls) == 1
    assert raised.value.raw_payload["fallback_used"] is False
    assert raised.value.raw_payload["fallback_reason"] == "safety_blocked"
    assert raised.value.raw_payload["attempts"][0]["prompt_block_reason"] == "SAFETY"
    assert raised.value.raw_payload["attempts"][0]["outcome"] == "blocked"


def test_bloqueo_de_motivo_no_especificado_no_activa_fallback(monkeypatch):
    responses = Responses(
        _response("", prompt_block_reason=types.BlockedReason.BLOCKED_REASON_UNSPECIFIED)
    )
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    with pytest.raises(GeminiGenerationError) as raised:
        GeminiClient(_fallback_settings()).generate_with_fallback(
            "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=10
        )

    assert len(responses.calls) == 1
    assert raised.value.raw_payload["fallback_used"] is False
    assert raised.value.raw_payload["fallback_reason"] == "safety_blocked"
    assert (
        raised.value.raw_payload["attempts"][0]["prompt_block_reason"]
        == "BLOCKED_REASON_UNSPECIFIED"
    )


def test_respuesta_truncada_activa_fallback_cuando_el_motivo_es_confiable(monkeypatch):
    responses = Responses(
        _response("Respuesta incompleta", finish_reason="MAX_TOKENS"),
        _response("Respuesta completa."),
    )
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    result = GeminiClient(_fallback_settings()).generate_with_fallback(
        "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=10
    )

    assert len(responses.calls) == 2
    assert result.raw_payload["fallback_reason"] == "truncated_response"


@pytest.mark.parametrize("error_code", [400, 401])
def test_errores_no_recuperables_no_escalan(monkeypatch, error_code):
    responses = Responses(ProviderError(error_code))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    with pytest.raises(GeminiGenerationError) as raised:
        GeminiClient(_fallback_settings()).generate_with_fallback(
            "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=10
        )

    assert len(responses.calls) == 1
    assert raised.value.raw_payload["attempt_count"] == 1


def test_fallback_respeta_habilitacion_y_limite_estimado(monkeypatch):
    responses = Responses(ProviderError(503), ProviderError(503))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    with pytest.raises(GeminiGenerationError):
        GeminiClient(_fallback_settings(gemini_fallback_enabled=False)).generate_with_fallback(
            "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=10
        )
    with pytest.raises(GeminiGenerationError):
        GeminiClient(_fallback_settings()).generate_with_fallback(
            "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=8_001
        )

    assert len(responses.calls) == 2


def test_fallo_de_ambos_modelos_registra_dos_intentos(monkeypatch):
    responses = Responses(ProviderError(503), ProviderError(503))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    with pytest.raises(GeminiGenerationError) as raised:
        GeminiClient(_fallback_settings()).generate_with_fallback(
            "Analiza alternativas", fallback_eligible=True, estimated_input_tokens=10
        )

    assert len(responses.calls) == 2
    assert raised.value.raw_payload["attempt_count"] == 2
    assert raised.value.raw_payload["fallback_used"] is True


def test_turno_que_ya_uso_fallback_no_realiza_otro_intento(monkeypatch):
    responses = Responses(ProviderError(503))
    monkeypatch.setattr("app.integrations.gemini.completion", responses)

    with pytest.raises(GeminiGenerationError):
        GeminiClient(_fallback_settings()).generate_with_fallback(
            "Analiza alternativas",
            fallback_eligible=True,
            estimated_input_tokens=10,
            fallback_attempted=True,
        )

    assert len(responses.calls) == 1


@pytest.mark.parametrize(
    ("task", "setting", "credential", "expected_model"),
    [
        ("router", "llm_router_model", "openai_api_key", "openai/gpt-4.1-mini"),
        ("memory", "llm_memory_model", "anthropic_api_key", "anthropic/claude-haiku"),
    ],
)
def test_tarea_usa_el_proveedor_configurado_disponible(
    task, setting, credential, expected_model
):
    calls: list[dict] = []

    def complete(**kwargs):
        calls.append(kwargs)
        return _response("ok", model_version=expected_model)

    settings = IntegrationsSettings(
        _env_file=None,
        gemini_api_key="gemini-secret",
        **{setting: expected_model, credential: "provider-secret"},
    )
    GeminiClient(settings, completion_fn=complete).generate_for_task("consulta", task=task)

    assert calls[0]["model"] == expected_model
    assert calls[0]["api_key"] == "provider-secret"


def test_fallback_opcional_usa_otro_proveedor_y_no_repite_el_principal():
    responses = Responses(ProviderError(503), _response("alternativa", model_version="openai/gpt"))
    settings = _fallback_settings(llm_fallback_model="openai/gpt", openai_api_key="openai-secret")

    result = GeminiClient(settings, completion_fn=responses).generate_with_fallback(
        "consulta compleja", fallback_eligible=True, estimated_input_tokens=10
    )

    assert [call["model"] for call in responses.calls] == [
        "gemini/gemini-3.5-flash",
        "openai/gpt",
    ]
    assert result.raw_payload["provider"] == "openai"


def test_fallback_bloqueado_por_seguridad_no_se_acepta_como_exitoso():
    responses = Responses(
        ProviderError(503),
        _response("texto bloqueado", finish_reason="SAFETY", model_version="openai/gpt"),
    )
    settings = _fallback_settings(llm_fallback_model="openai/gpt", openai_api_key="secret")

    with pytest.raises(GeminiGenerationError) as raised:
        GeminiClient(settings, completion_fn=responses).generate_with_fallback(
            "consulta compleja", fallback_eligible=True, estimated_input_tokens=10
        )

    assert raised.value.raw_payload["attempt_count"] == 2
    assert raised.value.raw_payload["attempts"][-1]["outcome"] == "blocked"
    assert raised.value.raw_payload["fallback_reason"] == "safety_blocked"


def test_proveedor_usa_el_modelo_configurado_aunque_litellm_devuelva_nombre_sin_prefijo():
    responses = Responses(ProviderError(503), _response("alternativa", model_version="gpt-real"))
    settings = _fallback_settings(
        llm_fallback_model="openai/gpt-configurado", openai_api_key="secret"
    )

    result = GeminiClient(settings, completion_fn=responses).generate_with_fallback(
        "consulta compleja", fallback_eligible=True, estimated_input_tokens=10
    )

    assert result.raw_payload["provider"] == "openai"
    assert result.raw_payload["used_model"] == "gpt-real"
    assert result.raw_payload["requested_model"] == "gemini/gemini-3.5-flash"
    assert result.raw_payload["attempts"][-1]["requested_model"] == "openai/gpt-configurado"


def test_http_429_activa_un_unico_fallback_si_esta_disponible():
    responses = Responses(ProviderError(429), _response("alternativa"))

    result = GeminiClient(_fallback_settings(), completion_fn=responses).generate_with_fallback(
        "consulta compleja", fallback_eligible=True, estimated_input_tokens=10
    )

    assert result.raw_payload["attempt_count"] == 2
    assert result.raw_payload["fallback_reason"] == "primary_error_recoverable"


def test_http_429_sin_alternativa_no_reintenta():
    responses = Responses(ProviderError(429))

    with pytest.raises(GeminiGenerationError):
        GeminiClient(
            _fallback_settings(llm_fallback_model="openai/gpt-sin-credencial"),
            completion_fn=responses,
        ).generate_with_fallback(
            "consulta compleja", fallback_eligible=True, estimated_input_tokens=10
        )

    assert len(responses.calls) == 1


def test_fallback_igual_al_modelo_principal_no_repite_la_llamada():
    responses = Responses(ProviderError(503))
    settings = _fallback_settings(llm_fallback_model="gemini/gemini-3.5-flash")

    with pytest.raises(GeminiGenerationError) as raised:
        GeminiClient(settings, completion_fn=responses).generate_with_fallback(
            "consulta compleja", fallback_eligible=True, estimated_input_tokens=10
        )

    assert [call["model"] for call in responses.calls] == ["gemini/gemini-3.5-flash"]
    assert raised.value.raw_payload["attempt_count"] == 1
    assert raised.value.raw_payload["fallback_used"] is False
