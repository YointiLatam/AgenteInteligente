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

    assert update == {"route": "rag"}
    assert route_after_router({**state, **update}) == "rag"


def test_router_envia_directamente_a_reasoning_sin_recuperacion():
    state = _state(retrieval_requested=False)

    update = router_node(state)

    assert update == {"route": "reasoning"}
    assert route_after_router({**state, **update}) == "reasoning"
