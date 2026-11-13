"""Construcción del StateGraph inicial del agente."""

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.memory import prepare_memory
from app.agent.nodes.reasoning import reasoning_node
from app.agent.nodes.retrieval import Retriever, rag_retrieval_node
from app.agent.nodes.router import route_after_router, router_node
from app.agent.nodes.tools import (
    critical_tools,
    mark_pending_approval,
    route_after_reasoning,
    safe_tools,
)
from app.agent.state import AgentState
from app.agent.tools import EMPTY_TOOL_REGISTRY, ToolRegistry
from app.integrations.gemini import GeminiClient, GeminiNotConfigured, TextGenerator
from app.integrations.settings import get_integrations_settings
from app.knowledge.retrieval import configured_retriever


class PendingCheckpointError(RuntimeError):
    """Un hilo tiene trabajo pendiente y debe reanudarse antes de otro turno."""


def _new_turn_input(state: AgentState) -> dict[str, object]:
    """Descarta valores históricos de la entrada y reinicia solo campos transitorios."""
    return {
        "query": state["query"],
        "retrieval_requested": state["retrieval_requested"],
        "route": None,
        "fallback_eligible": False,
        "fallback_attempted": False,
        "documents": [],
        "answer": None,
        "error": None,
        "new_turn": True,
        "turn_id": state.get("turn_id"),
        "completed_turn_id": None,
        "execution_status": "running",
        "tool_call": None,
        "pending_tool_call": None,
        "tool_result": None,
    }


def build_agent_graph(
    retriever: Retriever | None = None,
    gemini_client: TextGenerator | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    tool_registry: ToolRegistry | None = None,
):
    """Compila el flujo conversacional y la frontera HITL de herramientas."""
    client = gemini_client
    if client is None:
        try:
            client = GeminiClient(get_integrations_settings())
        except GeminiNotConfigured:
            client = None
    builder = StateGraph(AgentState)
    registry = tool_registry or EMPTY_TOOL_REGISTRY
    builder.add_node("prepare_memory", partial(prepare_memory, gemini_client=client))
    builder.add_node("router", partial(router_node, gemini_client=client))
    active_retriever = retriever if retriever is not None else configured_retriever
    builder.add_node("rag_retrieval", partial(rag_retrieval_node, retriever=active_retriever))
    builder.add_node("reasoning", partial(reasoning_node, gemini_client=client))
    builder.add_node("mark_pending_approval", partial(mark_pending_approval, registry=registry))
    builder.add_node("safe_tools", partial(safe_tools, registry=registry))
    builder.add_node("critical_tools", partial(critical_tools, registry=registry))

    builder.add_edge(START, "prepare_memory")
    builder.add_edge("prepare_memory", "router")
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {"rag": "rag_retrieval", "reasoning": "reasoning"},
    )
    builder.add_edge("rag_retrieval", "reasoning")
    builder.add_conditional_edges(
        "reasoning",
        partial(route_after_reasoning, registry=registry),
        {
            "end": END,
            "safe_tools": "safe_tools",
            "mark_pending_approval": "mark_pending_approval",
        },
    )
    builder.add_edge("safe_tools", END)
    builder.add_edge("mark_pending_approval", "critical_tools")
    builder.add_edge("critical_tools", END)
    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["critical_tools"],
    )


def invoke_agent(
    state: AgentState,
    *,
    retriever: Retriever | None = None,
    gemini_client: TextGenerator | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    tool_registry: ToolRegistry | None = None,
    thread_id: str | None = None,
    recursion_limit: int,
) -> AgentState:
    """Ejecuta un turno nuevo sin aceptar memoria histórica desde la entrada."""
    if thread_id is not None and checkpointer is None:
        raise ValueError("thread_id requiere un checkpointer configurado")
    graph = build_agent_graph(
        retriever=retriever,
        gemini_client=gemini_client,
        checkpointer=checkpointer,
        tool_registry=tool_registry,
    )
    # prepare_memory agrega un paso interno; conservar el presupuesto público previo.
    config: dict = {"recursion_limit": recursion_limit + 1}
    if thread_id is not None:
        config["configurable"] = {"thread_id": thread_id}
        snapshot = graph.get_state(config)
        if snapshot.next:
            raise PendingCheckpointError(
                "El hilo tiene trabajo pendiente; debe reanudarse antes de iniciar otro turno"
            )
    return graph.invoke(_new_turn_input(state), config)
