from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_agent_graph
from app.agent.state import AgentState
from app.agent.tools import AgentTool, ToolEffect, ToolRegistry
from app.integrations.gemini import GeminiResult


class FakeGemini:
    def __init__(self, *responses: str) -> None:
        self._responses = iter(responses)

    def generate(self, _: str) -> GeminiResult:
        return GeminiResult(text=next(self._responses), raw_payload={"provider": "fake"})


def _state(tool_name: str | None = None) -> AgentState:
    state: AgentState = {
        "query": "Ejecuta la acción",
        "retrieval_requested": False,
        "route": None,
        "documents": [],
        "messages": [],
        "conversation_summary": "",
        "summary_cursor": 0,
        "summary_failure_cursor": None,
        "new_turn": True,
        "turn_id": "turn-1",
        "completed_turn_id": None,
        "answer": None,
        "error": None,
        "execution_status": "running",
        "tool_call": None,
        "pending_tool_call": None,
        "tool_result": None,
    }
    if tool_name is not None:
        state["tool_call"] = {
            "call_id": "call-1",
            "tool_name": tool_name,
            "arguments": {"value": 7},
        }
    return state


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 8}


def test_herramienta_segura_se_ejecuta_y_el_grafo_finaliza():
    calls: list[dict] = []
    registry = ToolRegistry(
        {
            "lookup": AgentTool(
                "lookup",
                ToolEffect.READ,
                lambda arguments: calls.append(arguments) or {"found": True},
            )
        }
    )
    saver = InMemorySaver()
    graph = build_agent_graph(
        gemini_client=FakeGemini("reasoning", "Respuesta"),
        checkpointer=saver,
        tool_registry=registry,
    )

    result = graph.invoke(_state("lookup"), _config("safe-thread"))
    snapshot = graph.get_state(_config("safe-thread"))

    assert calls == [{"value": 7}]
    assert result["tool_result"] == {"found": True}
    assert result["execution_status"] == "completed"
    assert snapshot.next == ()


def test_herramienta_critica_se_persiste_y_se_interrumpe_antes_del_efecto():
    calls: list[dict] = []
    registry = ToolRegistry(
        {
            "publish": AgentTool(
                "publish",
                ToolEffect.EXTERNAL_WRITE,
                lambda arguments: calls.append(arguments) or {"published": True},
            )
        }
    )
    saver = InMemorySaver()
    graph = build_agent_graph(
        gemini_client=FakeGemini("reasoning", "Preparado"),
        checkpointer=saver,
        tool_registry=registry,
    )

    result = graph.invoke(_state("publish"), _config("critical-thread"))
    rebuilt = build_agent_graph(checkpointer=saver, tool_registry=registry)
    snapshot = rebuilt.get_state(_config("critical-thread"))

    assert calls == []
    assert result["execution_status"] == "pending_approval"
    assert snapshot.next == ("critical_tools",)
    assert snapshot.values["turn_id"] == "turn-1"
    assert snapshot.values["pending_tool_call"] == {
        "call_id": "call-1",
        "tool_name": "publish",
        "arguments": {"value": 7},
        "effect": "external_write",
    }


def test_herramienta_desconocida_falla_cerrada_y_no_se_ejecuta():
    saver = InMemorySaver()
    graph = build_agent_graph(
        gemini_client=FakeGemini("reasoning", "Preparado"),
        checkpointer=saver,
    )

    result = graph.invoke(_state("not_registered"), _config("unknown-thread"))
    snapshot = graph.get_state(_config("unknown-thread"))

    assert result["execution_status"] == "pending_approval"
    assert result["pending_tool_call"]["effect"] == "unknown"
    assert snapshot.next == ("critical_tools",)


def test_registro_vacio_conserva_el_recorrido_normal_sin_herramientas():
    graph = build_agent_graph(gemini_client=FakeGemini("reasoning", "Respuesta"))

    result = graph.invoke(_state(), {"recursion_limit": 5})

    assert result["answer"] == "Respuesta"
    assert result["execution_status"] == "completed"
    assert result["pending_tool_call"] is None


def test_aprobacion_reanuda_el_checkpoint_y_ejecuta_una_sola_vez():
    calls: list[dict] = []
    registry = ToolRegistry(
        {
            "publish": AgentTool(
                "publish",
                ToolEffect.EXTERNAL_WRITE,
                lambda arguments: calls.append(arguments) or {"published": True},
            )
        }
    )
    saver = InMemorySaver()
    graph = build_agent_graph(
        gemini_client=FakeGemini("reasoning", "Preparado"),
        checkpointer=saver,
        tool_registry=registry,
    )
    config = _config("approved-thread")
    graph.invoke(_state("publish"), config)

    rebuilt = build_agent_graph(checkpointer=saver, tool_registry=registry)
    result = rebuilt.invoke(None, config)
    replay = rebuilt.invoke(None, config)

    assert calls == [{"value": 7}]
    assert result["tool_result"] == {"published": True}
    assert result["execution_status"] == "completed"
    assert replay["tool_result"] == {"published": True}
    assert rebuilt.get_state(config).next == ()


def test_rechazo_resuelve_checkpoint_sin_ejecutar_herramienta():
    calls: list[dict] = []
    registry = ToolRegistry(
        {
            "publish": AgentTool(
                "publish",
                ToolEffect.EXTERNAL_WRITE,
                lambda arguments: calls.append(arguments) or {"published": True},
            )
        }
    )
    saver = InMemorySaver()
    graph = build_agent_graph(
        gemini_client=FakeGemini("reasoning", "Preparado"),
        checkpointer=saver,
        tool_registry=registry,
    )
    config = _config("rejected-thread")
    graph.invoke(_state("publish"), config)

    rebuilt = build_agent_graph(checkpointer=saver, tool_registry=registry)
    rebuilt.update_state(
        config,
        {
            "execution_status": "completed",
            "pending_tool_call": None,
            "tool_call": None,
            "completed_turn_id": "turn-1",
            "tool_result": {
                "status": "rejected",
                "reason": "Operación no autorizada",
            },
        },
        as_node="critical_tools",
    )
    result = rebuilt.invoke(None, config)

    assert calls == []
    assert result["execution_status"] == "completed"
    assert result["tool_result"] == {
        "status": "rejected",
        "reason": "Operación no autorizada",
    }
    assert rebuilt.get_state(config).next == ()
