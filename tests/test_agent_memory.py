from collections.abc import Iterable

from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_agent_graph, invoke_agent
from app.agent.nodes.memory import (
    MAX_RECENT_MESSAGES,
    MEMORY_TOKEN_BUDGET,
    conversation_context,
    estimate_tokens,
    prepare_memory,
)
from app.agent.nodes.reasoning import _reasoning_prompt
from app.agent.nodes.router import _router_prompt
from app.agent.state import AgentMessage, AgentState
from app.integrations.gemini import GeminiResult


class FakeGemini:
    def __init__(self, responses: Iterable[str] = ()) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> GeminiResult:
        self.prompts.append(prompt)
        return GeminiResult(text=next(self.responses), raw_payload={"provider": "fake"})


class FailingGemini(FakeGemini):
    def generate(self, prompt: str) -> GeminiResult:
        self.prompts.append(prompt)
        raise RuntimeError("summary unavailable")


def _message(role: str, content: str) -> AgentMessage:
    return {"role": role, "content": content, "raw_payload": {}}  # type: ignore[typeddict-item]


def _state(**updates: object) -> AgentState:
    state: AgentState = {
        "query": "consulta actual",
        "retrieval_requested": False,
        "route": None,
        "documents": [],
        "messages": [],
        "conversation_summary": "",
        "summary_cursor": 0,
        "summary_failure_cursor": None,
        "new_turn": False,
        "answer": None,
        "error": None,
    }
    return {**state, **updates}  # type: ignore[return-value]


def test_prepare_memory_crea_ventana_de_ocho_mensajes_y_respeta_presupuesto():
    messages = [
        _message("user" if index % 2 == 0 or index == 9 else "assistant", f"m{index}")
        for index in range(10)
    ]

    context = conversation_context(_state(messages=messages, query="m9"))

    assert context.count("Usuario:") + context.count("Asistente:") == MAX_RECENT_MESSAGES
    assert "m0" not in context
    assert "m9" not in context

    long_messages = [_message("user", "x" * 7_500), _message("assistant", "y" * 7_500)]
    long_context = conversation_context(_state(messages=long_messages, query="consulta distinta"))
    assert "Asistente:" in long_context
    assert "y" * 1_000 in long_context
    assert estimate_tokens(long_context) <= MEMORY_TOKEN_BUDGET


def test_prompts_incorporan_memoria_sin_duplicar_consulta_actual():
    state = _state(
        query="¿Cuál era el distrito?",
        messages=[
            _message("user", "Busco una oficina"),
            _message("assistant", "Consideraré oficinas disponibles."),
            _message("user", "¿Cuál era el distrito?"),
        ],
    )

    assert _router_prompt(state).count("¿Cuál era el distrito?") == 1
    assert _reasoning_prompt(state).count("¿Cuál era el distrito?") == 1


def test_prepare_memory_resume_solo_tramo_antiguo_y_conserva_historial_completo():
    messages = [
        _message("user" if index % 2 == 0 else "assistant", f"dato-{index} " + "x" * 1_200)
        for index in range(12)
    ]
    client = FakeGemini(["El usuario prioriza oficinas cerca al transporte."])

    update = prepare_memory(_state(messages=messages), client)

    assert update["messages"] == messages
    assert update["conversation_summary"] == "El usuario prioriza oficinas cerca al transporte."
    assert update["summary_cursor"] > 0
    assert len(client.prompts) == 1

    repeated = prepare_memory(_state(**{**_state(messages=messages), **update}), client)
    assert "conversation_summary" not in repeated
    assert len(client.prompts) == 1


def test_historial_ya_resumido_no_activa_resumen_por_el_tamano_total():
    summarized = [_message("user", "historial anterior " + "x" * 1_500) for _ in range(6)]
    recent = [_message("assistant", "mensaje reciente " + "y" * 90) for _ in range(9)]
    client = FakeGemini()

    update = prepare_memory(
        _state(
            messages=[*summarized, *recent],
            conversation_summary="s" * 1_500,
            summary_cursor=len(summarized),
        ),
        client,
    )

    assert "conversation_summary" not in update
    assert "summary_cursor" not in update
    assert client.prompts == []


def test_fallo_del_resumen_no_pierde_historial_ni_reintenta_el_mismo_bloque():
    messages = [
        _message("user" if index % 2 == 0 else "assistant", "x" * 1_200)
        for index in range(12)
    ]
    client = FailingGemini()

    failed = prepare_memory(_state(messages=messages), client)
    retried = prepare_memory(_state(**{**_state(messages=messages), **failed}), client)

    assert failed["messages"] == messages
    assert "conversation_summary" not in failed
    assert "summary_cursor" not in failed
    assert failed["summary_failure_cursor"] is not None
    assert len(client.prompts) == 1
    assert retried["messages"] == messages


def test_turnos_persistidos_no_sobrescriben_historial_vacio_y_se_recuperan():
    saver = InMemorySaver()
    first = _state(query="Mi nombre es Ana")
    second = _state(query="¿Cómo me llamo?")

    invoke_agent(
        first,
        gemini_client=FakeGemini(["reasoning", "Entendido, Ana."]),
        checkpointer=saver,
        thread_id="ana",
        recursion_limit=3,
    )
    second_client = FakeGemini(["reasoning", "Te llamas Ana."])
    result = invoke_agent(
        second,
        gemini_client=second_client,
        checkpointer=saver,
        thread_id="ana",
        recursion_limit=3,
    )
    rebuilt = build_agent_graph(checkpointer=saver)
    snapshot = rebuilt.get_state({"configurable": {"thread_id": "ana"}})

    conversational = [message for message in result["messages"] if message["role"] != "router"]
    assert [message["content"] for message in conversational] == [
        "Mi nombre es Ana",
        "Entendido, Ana.",
        "¿Cómo me llamo?",
        "Te llamas Ana.",
    ]
    assert snapshot.values["messages"] == result["messages"]
    reasoning_prompt = second_client.prompts[1]
    assert "Mi nombre es Ana" in reasoning_prompt
    assert "Entendido, Ana." in reasoning_prompt


def test_resumen_se_persiste_por_thread_y_no_se_comparte():
    saver = InMemorySaver()
    messages = [
        _message("user" if index % 2 == 0 else "assistant", "x" * 1_200)
        for index in range(12)
    ]

    graph = build_agent_graph(
        gemini_client=FakeGemini(["resumen de Ana", "reasoning", "respuesta"]),
        checkpointer=saver,
    )
    graph.invoke(
        _state(messages=messages),
        {"configurable": {"thread_id": "ana"}, "recursion_limit": 4},
    )
    rebuilt = build_agent_graph(checkpointer=saver)
    ana = rebuilt.get_state({"configurable": {"thread_id": "ana"}}).values
    other = rebuilt.get_state({"configurable": {"thread_id": "otra"}}).values

    assert ana["conversation_summary"] == "resumen de Ana"
    assert ana["messages"][:12] == messages
    assert not other
