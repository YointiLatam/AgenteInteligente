from app.agent.nodes.retrieval import empty_retriever, rag_retrieval_node
from app.agent.state import AgentState, RetrievedDocument


def _state() -> AgentState:
    return {
        "query": "manual de autenticación",
        "retrieval_requested": True,
        "route": "rag",
        "documents": [],
        "answer": None,
        "error": None,
    }


def test_recuperacion_vacia_no_agrega_error():
    update = rag_retrieval_node(_state(), empty_retriever)

    assert update == {"documents": [], "error": None}


def test_recuperacion_simulada_devuelve_documentos():
    document: RetrievedDocument = {
        "id": "auth-1",
        "content": "La autenticación usa JWT.",
        "score": 0.98,
    }

    update = rag_retrieval_node(_state(), lambda _: [document])

    assert update == {"documents": [document], "error": None}


def test_error_del_recuperador_se_controla():
    def failing_retriever(_: str) -> list[RetrievedDocument]:
        raise RuntimeError("servicio no disponible")

    update = rag_retrieval_node(_state(), failing_retriever)

    assert update == {"documents": [], "error": "retrieval_unavailable"}
