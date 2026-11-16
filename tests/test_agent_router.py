from app.agent.nodes.router import route_after_router, router_node
from app.agent.state import AgentState


def _state(*, retrieval_requested: bool) -> AgentState:
    return {
        "query": "consulta",
        "retrieval_requested": retrieval_requested,
        "route": None,
        "documents": [],
        "messages": [],
        "answer": None,
        "error": None,
    }


def test_router_envia_a_rag_cuando_se_solicita_recuperacion():
    state = _state(retrieval_requested=True)

    update = router_node(state)

    assert update == {"route": "rag", "fallback_eligible": False}
    assert route_after_router({**state, **update}) == "rag"


def test_router_envia_directamente_a_reasoning_sin_recuperacion():
    state = _state(retrieval_requested=False)

    update = router_node(state)

    assert update == {"route": "reasoning", "fallback_eligible": False}
    assert route_after_router({**state, **update}) == "reasoning"


def test_router_no_atribuye_proveedor_cuando_la_clasificacion_falla():
    class FailingGenerator:
        def generate(self, _: str):
            raise RuntimeError("unavailable")

    update = router_node(_state(retrieval_requested=True), FailingGenerator())  # type: ignore[arg-type]

    assert update["route"] == "rag"
    assert update["messages"][-1]["raw_payload"] == {"error": "classification_unavailable"}


def test_router_marca_comparacion_razonada_como_elegible_para_fallback():
    state = {
        **_state(retrieval_requested=False),
        "query": "Compara las alternativas y justifica la decisión paso a paso.",
    }

    update = router_node(state)

    assert update["route"] == "reasoning"
    assert update["fallback_eligible"] is True


def test_router_marca_sintesis_de_fuentes_como_elegible_para_fallback():
    state = {
        **_state(retrieval_requested=True),
        "query": "Relaciona varias fuentes y sintetiza sus diferencias.",
    }

    update = router_node(state)

    assert update["route"] == "rag"
    assert update["fallback_eligible"] is True
