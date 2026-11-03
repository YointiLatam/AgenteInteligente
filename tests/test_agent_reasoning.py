from app.agent.nodes.reasoning import reasoning_node
from app.agent.state import AgentState


def _state(**updates) -> AgentState:
    state: AgentState = {
        "query": "¿Cómo funciona JWT?",
        "retrieval_requested": False,
        "route": "reasoning",
        "documents": [],
        "answer": None,
        "error": None,
    }
    return {**state, **updates}


def test_reasoning_responde_con_contexto_recuperado():
    update = reasoning_node(
        _state(documents=[{"id": "auth-1", "content": "JWT firma tokens.", "score": 0.9}])
    )

    assert update == {"answer": "Respuesta basada en contexto: JWT firma tokens."}


def test_reasoning_responde_sin_contexto():
    update = reasoning_node(_state())

    assert update == {"answer": "Respuesta sin contexto recuperado para: ¿Cómo funciona JWT?"}


def test_reasoning_degrada_ante_error_de_recuperacion():
    update = reasoning_node(_state(error="retrieval_unavailable"))

    assert update == {"answer": "No fue posible recuperar contexto para: ¿Cómo funciona JWT?"}
