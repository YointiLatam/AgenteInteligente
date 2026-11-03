"""Construcción del StateGraph inicial del agente."""

from functools import partial

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.reasoning import reasoning_node
from app.agent.nodes.retrieval import Retriever, empty_retriever, rag_retrieval_node
from app.agent.nodes.router import route_after_router, router_node
from app.agent.state import AgentState


def build_agent_graph(retriever: Retriever = empty_retriever):
    """Compila el flujo Router -> (RAG | Reasoning) -> Reasoning -> END."""
    builder = StateGraph(AgentState)
    builder.add_node("router", router_node)
    builder.add_node("rag_retrieval", partial(rag_retrieval_node, retriever=retriever))
    builder.add_node("reasoning", reasoning_node)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        route_after_router,
        {"rag": "rag_retrieval", "reasoning": "reasoning"},
    )
    builder.add_edge("rag_retrieval", "reasoning")
    builder.add_edge("reasoning", END)
    return builder.compile()


def invoke_agent(
    state: AgentState,
    *,
    retriever: Retriever = empty_retriever,
    recursion_limit: int,
) -> AgentState:
    """Ejecuta el grafo aplicando explícitamente el límite de recursión indicado."""
    graph = build_agent_graph(retriever)
    return graph.invoke(state, {"recursion_limit": recursion_limit})
