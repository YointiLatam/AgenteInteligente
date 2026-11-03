"""Respuesta determinista temporal, sin proveedor de modelos."""

from app.agent.state import AgentState


def reasoning_node(state: AgentState) -> dict[str, str]:
    """Genera una respuesta basada en el contexto recuperado disponible."""
    if state["documents"]:
        context = " ".join(document["content"] for document in state["documents"])
        return {"answer": f"Respuesta basada en contexto: {context}"}
    if state["error"]:
        return {"answer": f"No fue posible recuperar contexto para: {state['query']}"}
    return {"answer": f"Respuesta sin contexto recuperado para: {state['query']}"}
