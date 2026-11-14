from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.auth.dependencies import Settings
from app.chat.approvals import ApprovalError, ApprovalIn, process_approval
from app.chat.auth import resolve_client
from app.conversations.wiring import ServiceOpener, get_conversation_service_opener

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/approval")
async def post_approval(
    body: ApprovalIn,
    settings: Settings,
    opener: Annotated[ServiceOpener, Depends(get_conversation_service_opener)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> dict:
    """Aprueba o rechaza una acción pendiente (HITL) y reanuda el grafo."""
    client = resolve_client(settings, authorization=authorization, api_key=x_api_key)
    if client is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas o ausentes")
    try:
        result = await process_approval(opener, client, body.model_dump())
    except ApprovalError as error:
        raise HTTPException(error.status_code, error.message) from error
    return {k: v for k, v in result.items() if k != "type"}
