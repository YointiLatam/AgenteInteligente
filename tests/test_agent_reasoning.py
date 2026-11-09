from app.agent.nodes.reasoning import reasoning_node
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
