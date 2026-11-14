from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.admin.repository import AdminStoreOpener, get_admin_store_opener
from app.auth.dependencies import Settings
from app.chat.approvals import ApprovalError, ApprovalIn, authorize_approver, process_approval
from app.chat.auth import resolve_client
from app.conversations.wiring import ServiceOpener, get_conversation_service_opener

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/approval")
async def post_approval(
    body: ApprovalIn,
    settings: Settings,
    opener: Annotated[ServiceOpener, Depends(get_conversation_service_opener)],
    admin_opener: Annotated[AdminStoreOpener, Depends(get_admin_store_opener)],
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> dict:
    """Aprueba o rechaza una acción pendiente (HITL) y reanuda el grafo.

    Exige que el cliente autenticado sea un usuario con rol operator o admin."""
    client = resolve_client(settings, authorization=authorization, api_key=x_api_key)
    if client is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas o ausentes")
    try:
        await authorize_approver(admin_opener, client)
        result = await process_approval(opener, client, body.model_dump())
    except ApprovalError as error:
        raise HTTPException(error.status_code, error.message) from error
    return {k: v for k, v in result.items() if k != "type"}
