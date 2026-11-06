"""Construcción del StateGraph inicial del agente."""

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.agent.nodes.reasoning import reasoning_node
from app.agent.nodes.retrieval import Retriever, rag_retrieval_node
from app.agent.nodes.router import route_after_router, router_node
from app.agent.state import AgentState
from app.integrations.gemini import GeminiClient, GeminiNotConfigured, TextGenerator
from app.integrations.settings import get_integrations_settings
from app.knowledge.retrieval import configured_retriever


def build_agent_graph(
    retriever: Retriever | None = None,
    gemini_client: TextGenerator | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """Compila el flujo Router -> (RAG | Reasoning) -> Reasoning -> END."""
    client = gemini_client
    if client is None:
        try:
            client = GeminiClient(get_integrations_settings())
        except GeminiNotConfigured:
            client = None
    builder = StateGraph(AgentState)
    builder.add_node("router", partial(router_node, gemini_client=client))
    active_retriever = retriever if retriever is not None else configured_retriever
    builder.add_node("rag_retrieval", partial(rag_retrieval_node, retriever=active_retriever))
    builder.add_node("reasoning", partial(reasoning_node, gemini_client=client))

    builder.add_edge(START, "router")
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
    """Ejecuta el grafo aplicando explícitamente el límite de recursión indicado."""
    if thread_id is not None and checkpointer is None:
        raise ValueError("thread_id requiere un checkpointer configurado")
    graph = build_agent_graph(retriever, gemini_client, checkpointer)
    config: dict = {"recursion_limit": recursion_limit}
    if thread_id is not None:
        config["configurable"] = {"thread_id": thread_id}
    return graph.invoke(state, config)
