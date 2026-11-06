"""Construcción del StateGraph inicial del agente."""

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.memory import prepare_memory
from app.agent.nodes.reasoning import reasoning_node
from app.agent.nodes.retrieval import Retriever, rag_retrieval_node
from app.agent.nodes.router import route_after_router, router_node
from app.agent.state import AgentState
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
        "documents": [],
        "answer": None,
        "error": None,
        "new_turn": True,
    }


def build_agent_graph(
    retriever: Retriever | None = None,
    gemini_client: TextGenerator | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Compila el flujo Memoria -> Router -> (RAG | Reasoning) -> Reasoning -> END."""
    client = gemini_client
    if client is None:
        try:
            client = GeminiClient(get_integrations_settings())
        except GeminiNotConfigured:
            client = None
    builder = StateGraph(AgentState)
    builder.add_node("prepare_memory", partial(prepare_memory, gemini_client=client))
    builder.add_node("router", partial(router_node, gemini_client=client))
    active_retriever = retriever if retriever is not None else configured_retriever
    builder.add_node("rag_retrieval", partial(rag_retrieval_node, retriever=active_retriever))
    builder.add_node("reasoning", partial(reasoning_node, gemini_client=client))

    builder.add_edge(START, "prepare_memory")
    builder.add_edge("prepare_memory", "router")
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {"rag": "rag_retrieval", "reasoning": "reasoning"},
    )
    builder.add_edge("rag_retrieval", "reasoning")
    builder.add_edge("reasoning", END)
    return builder.compile(checkpointer=checkpointer)


def invoke_agent(
    state: AgentState,
    *,
    retriever: Retriever | None = None,
    gemini_client: TextGenerator | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    thread_id: str | None = None,
    recursion_limit: int,
) -> AgentState:
    """Ejecuta un turno nuevo sin aceptar memoria histórica desde la entrada."""
    if thread_id is not None and checkpointer is None:
        raise ValueError("thread_id requiere un checkpointer configurado")
    graph = build_agent_graph(retriever, gemini_client, checkpointer)
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
