from app.agent.nodes.reasoning import _reasoning_prompt, reasoning_node
from app.agent.state import AgentState
from app.integrations.gemini import GeminiGenerationError, GeminiResult


def _state(**updates) -> AgentState:
    state: AgentState = {
        "query": "¿Cómo funciona JWT?",
        "retrieval_requested": False,
        "route": "reasoning",
        "documents": [],
        "messages": [],
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


class FallbackClient:
    def __init__(self, result: GeminiResult | Exception) -> None:
        self.result = result
        self.calls: list[dict] = []

    def generate(self, _: str) -> GeminiResult:
        raise AssertionError("reasoning debe usar la interfaz de fallback")

    def generate_with_fallback(self, prompt: str, **kwargs) -> GeminiResult:
        self.calls.append({"prompt": prompt, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_reasoning_transmite_elegibilidad_al_cliente_fallback():
    client = FallbackClient(
        GeminiResult(
            text="Respuesta reforzada.",
            raw_payload={"model": "gemini-3.8-flash", "fallback_used": True},
        )
    )

    update = reasoning_node(_state(fallback_eligible=True), client)  # type: ignore[arg-type]

    assert update["answer"] == "Respuesta reforzada."
    assert client.calls[0]["fallback_eligible"] is True
    assert client.calls[0]["estimated_input_tokens"] > 0
    assert update["messages"][-1]["raw_payload"]["fallback_used"] is True


def test_reasoning_conserva_auditoria_si_fallan_ambos_modelos():
    payload = {"attempt_count": 2, "fallback_used": True, "attempts": []}
    client = FallbackClient(GeminiGenerationError(payload))

    update = reasoning_node(_state(fallback_eligible=True), client)  # type: ignore[arg-type]

    assert update["answer"] == "Respuesta sin contexto recuperado para: ¿Cómo funciona JWT?"
    assert update["messages"][-1]["raw_payload"] == payload


def test_prompt_delimita_consulta_memoria_y_contexto_rag_no_confiables():
    prompt = _reasoning_prompt(
        _state(
            query="¿Qué dice el documento?",
            conversation_summary="Memoria previa.",
            documents=[{"id": "doc-1", "content": "Contexto recuperado.", "score": 0.9}],
        )
    )

    assert "La consulta actual es la tarea legítima" in prompt
    assert "<MEMORIA_CONVERSACIONAL_NO_CONFIABLE>" in prompt
    assert "Memoria previa." in prompt
    assert "<CONSULTA_ACTUAL>\n¿Qué dice el documento?\n</CONSULTA_ACTUAL>" in prompt
    assert "<CONTEXTO_DOCUMENTAL_RAG_NO_CONFIABLE>" in prompt
    assert "Contexto recuperado." in prompt
