import pytest
from langgraph.errors import GraphRecursionError

import app.agent.graph as graph_module
from app.agent.graph import invoke_agent
from app.agent.state import AgentState, RetrievedDocument
from app.integrations.gemini import GeminiResult
from app.integrations.settings import IntegrationsSettings


class FakeGemini:
    def __init__(self, *responses: str):
        self._responses = iter(responses)

    def generate(self, _: str) -> GeminiResult:
        return GeminiResult(
            text=next(self._responses),
            raw_payload={"provider": "gemini", "model": "gemini-3.5-flash"},
        )


def _state(*, retrieval_requested: bool) -> AgentState:
    return {
        "query": "¿Cómo funciona JWT?",
        "retrieval_requested": retrieval_requested,
        "route": None,
        "documents": [],
        "messages": [],
        "answer": None,
        "error": None,
    }


def test_grafo_completa_ruta_directa_con_limite_validado():
    result = invoke_agent(
        _state(retrieval_requested=False),
        gemini_client=FakeGemini("reasoning", "Respuesta Gemini."),
        recursion_limit=3,
    )

    assert result["route"] == "reasoning"
    assert result["documents"] == []
    assert result["answer"] == "Respuesta Gemini."


def test_grafo_completa_ruta_rag_con_limite_validado():
    document: RetrievedDocument = {
        "id": "auth-1",
        "content": "JWT firma tokens.",
        "score": 0.9,
    }

    result = invoke_agent(
        _state(retrieval_requested=True),
        retriever=lambda _: [document],
        gemini_client=FakeGemini("rag", "Respuesta con contexto."),
        recursion_limit=4,
    )

    assert result["route"] == "rag"
    assert result["documents"] == [document]
    assert result["answer"] == "Respuesta con contexto."


def test_grafo_termina_con_error_controlado_del_recuperador():
    def failing_retriever(_: str) -> list[RetrievedDocument]:
        raise RuntimeError("servicio no disponible")

    result = invoke_agent(
        _state(retrieval_requested=True),
        retriever=failing_retriever,
        gemini_client=FakeGemini("rag", "Respuesta sin recuperación."),
        recursion_limit=4,
    )

    assert result["error"] == "retrieval_unavailable"
    assert result["answer"] == "Respuesta sin recuperación."


def test_limite_insuficiente_interrumpe_el_recorrido_rag():
    with pytest.raises(GraphRecursionError):
        invoke_agent(
            _state(retrieval_requested=True),
            gemini_client=FakeGemini("rag", "No debe completarse."),
            recursion_limit=3,
        )


def test_grafo_degrada_sin_api_key(monkeypatch):
    monkeypatch.setattr(
        graph_module,
        "get_integrations_settings",
        lambda: IntegrationsSettings(_env_file=None),
    )

    result = graph_module.invoke_agent(_state(retrieval_requested=False), recursion_limit=3)

    assert result["route"] == "reasoning"
    assert result["answer"] == "Respuesta sin contexto recuperado para: ¿Cómo funciona JWT?"


def test_grafo_conserva_payloads_de_router_y_reasoning():
    class PayloadGemini:
        def __init__(self):
            self._results = iter(
                [
                    GeminiResult(
                        text="reasoning",
                        raw_payload={"provider": "gemini", "input_tokens": 3},
                    ),
                    GeminiResult(
                        text="Respuesta Gemini.",
                        raw_payload={"provider": "gemini", "input_tokens": 5},
                    ),
                ]
            )

        def generate(self, _: str) -> GeminiResult:
            return next(self._results)

    result = invoke_agent(
        _state(retrieval_requested=False),
        gemini_client=PayloadGemini(),
        recursion_limit=3,
    )

    assert result["messages"] == [
        {
            "role": "router",
            "content": "reasoning",
            "raw_payload": {"provider": "gemini", "input_tokens": 3},
        },
        {
            "role": "assistant",
            "content": "Respuesta Gemini.",
            "raw_payload": {"provider": "gemini", "input_tokens": 5},
        },
    ]
