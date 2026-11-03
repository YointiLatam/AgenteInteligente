import pytest
from langgraph.errors import GraphRecursionError

from app.agent.graph import invoke_agent
from app.agent.state import AgentState, RetrievedDocument


def _state(*, retrieval_requested: bool) -> AgentState:
    return {
        "query": "¿Cómo funciona JWT?",
        "retrieval_requested": retrieval_requested,
        "route": None,
        "documents": [],
        "answer": None,
        "error": None,
    }


def test_grafo_completa_ruta_directa_con_limite_validado():
    result = invoke_agent(_state(retrieval_requested=False), recursion_limit=3)

    assert result["route"] == "reasoning"
    assert result["documents"] == []
    assert result["answer"] == "Respuesta sin contexto recuperado para: ¿Cómo funciona JWT?"


def test_grafo_completa_ruta_rag_con_limite_validado():
    document: RetrievedDocument = {
        "id": "auth-1",
        "content": "JWT firma tokens.",
        "score": 0.9,
    }

    result = invoke_agent(
        _state(retrieval_requested=True),
        retriever=lambda _: [document],
        recursion_limit=4,
    )

    assert result["route"] == "rag"
    assert result["documents"] == [document]
    assert result["answer"] == "Respuesta basada en contexto: JWT firma tokens."


def test_grafo_termina_con_error_controlado_del_recuperador():
    def failing_retriever(_: str) -> list[RetrievedDocument]:
        raise RuntimeError("servicio no disponible")

    result = invoke_agent(
        _state(retrieval_requested=True),
        retriever=failing_retriever,
        recursion_limit=4,
    )

    assert result["error"] == "retrieval_unavailable"
    assert result["answer"] == "No fue posible recuperar contexto para: ¿Cómo funciona JWT?"


def test_limite_insuficiente_interrumpe_el_recorrido_rag():
    with pytest.raises(GraphRecursionError):
        invoke_agent(_state(retrieval_requested=True), recursion_limit=3)
