"""Admin panel API (HITL inbox, conversation history, tool audit) with role-based access."""

import logging
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from app.admin.rbac import AdminUser, require_role
from app.admin.repository import AdminStoreError, AdminStoreOpener, get_admin_store_opener
from app.chat.approvals import ApprovalError, process_approval
from app.conversations.wiring import (
    ServiceNotConfigured,
    ServiceOpener,
    get_conversation_service_opener,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["administración"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]
Store = Annotated[AdminStoreOpener, Depends(get_admin_store_opener)]
AnyPanelUser = Annotated[AdminUser, Depends(require_role("operator", "admin"))]
AdminOnly = Annotated[AdminUser, Depends(require_role("admin"))]


class DecisionIn(BaseModel):
    """Unknown fields (e.g. ``approved_by``) are ignored: the approver comes from the database."""

    thread_id: UUID
    turn_id: UUID
    tool_name: str = Field(min_length=1, max_length=200)
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=1000)


def _unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de administración no disponible"
    )


def _page(items: list[dict[str, Any]], total: int, limit: int, offset: int) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _read(opener: AdminStoreOpener, action):
    """Run ``action(store)`` mapping storage failures to a generic 503."""
    try:
        with opener() as store:
            return action(store)
    except AdminStoreError:
        logger.exception("Falló la consulta del panel")
        raise _unavailable() from None


@router.get("/me")
def me(user: AnyPanelUser) -> dict[str, Any]:
    return {"id": user.id, "external_id": user.external_id, "email": user.email, "role": user.role}


@router.get("/users")
def list_users(
    _: AdminOnly, opener: Store, limit: Limit = DEFAULT_LIMIT, offset: Offset = 0
) -> dict[str, Any]:
    rows, total = _read(opener, lambda store: store.list_users(limit, offset))
    items = [
        {
            "id": str(r["id"]),
            "external_id": r["external_id"],
            "email": r["email"],
            "role": r["role"],
            "created_at": _iso(r["created_at"]),
        }
        for r in rows
    ]
    return _page(items, total, limit, offset)


@router.get("/approvals")
def list_approvals(
    _: AnyPanelUser,
    opener: Store,
    service_opener: Annotated[ServiceOpener, Depends(get_conversation_service_opener)],
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Pending HITL approvals: threads in ``paused_hil`` with their pending tool call."""
    threads, total = _read(opener, lambda store: store.list_paused_threads(limit, offset))
    items: list[dict[str, Any]] = []
    try:
        with service_opener() as service:
            for thread in threads:
                try:
                    pending = service.read_pending_approval(thread["id"])
                except Exception:
                    logger.exception("No se pudo leer el checkpoint pendiente")
                    continue
                if pending is None:
                    continue
                items.append(
                    {
                        "thread_id": str(thread["id"]),
                        "turn_id": pending["turn_id"],
                        "call_id": pending["call_id"],
                        "tool_name": pending["tool_name"],
                        "parameters": pending["parameters"],
                        "channel": thread["channel"],
                        "external_conversation_id": thread["external_conversation_id"],
                        "created_at": _iso(thread["updated_at"]),
                    }
                )
    except ServiceNotConfigured:
        logger.exception("Servicio de conversación no configurado")
        raise _unavailable() from None
    return _page(items, total, limit, offset)


@router.post("/approvals/{call_id}/decision")
async def decide_approval(
    call_id: Annotated[str, Path(min_length=1, max_length=200)],
    body: DecisionIn,
    user: AnyPanelUser,
    service_opener: Annotated[ServiceOpener, Depends(get_conversation_service_opener)],
) -> dict[str, Any]:
    data = {**body.model_dump(mode="json"), "call_id": call_id}
    try:
        result = await process_approval(service_opener, user.external_id, data)
    except ApprovalError as error:
        raise HTTPException(error.status_code, error.message) from error
    return {"call_id": result["call_id"], "status": result["status"]}


@router.get("/conversations")
def list_conversations(
    _: AnyPanelUser,
    opener: Store,
    channel: Literal["web_widget", "whatsapp", "meta"] | None = None,
    user: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    rows, total = _read(opener, lambda store: store.list_threads(channel, user, limit, offset))
    items = [
        {
            "thread_id": str(r["id"]),
            "channel": r["channel"],
            "external_conversation_id": r["external_conversation_id"],
            "status": r["status"],
            "created_at": _iso(r["created_at"]),
            "updated_at": _iso(r["updated_at"]),
        }
        for r in rows
    ]
    return _page(items, total, limit, offset)


@router.get("/conversations/{thread_id}/messages")
def list_conversation_messages(
    thread_id: UUID,
    _: AnyPanelUser,
    opener: Store,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    def action(store):
        thread = store.get_thread(thread_id)
        if thread is None:
            return None
        return thread, *store.list_messages(thread_id, limit, offset)

    found = _read(opener, action)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No se encontró la conversación")
    thread, rows, total = found
    items = [
        {
            "id": str(r["id"]),
            "turn_id": str(r["turn_id"]),
            "role": r["role"],
            "content": r["content"],
            "created_at": _iso(r["created_at"]),
        }
        for r in rows
    ]
    return {
        "thread_id": str(thread["id"]),
        "channel": thread["channel"],
        "external_conversation_id": thread["external_conversation_id"],
        **_page(items, total, limit, offset),
    }


@router.get("/audit/tool-executions")
def list_tool_executions(
    _: AdminOnly,
    opener: Store,
    exec_status: Annotated[
        Literal["processing", "completed", "failed"] | None, Query(alias="status")
    ] = None,
    tool: Annotated[str | None, Query(min_length=1, max_length=100, pattern=r"^[\w.\-]+$")] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> dict[str, Any]:
    rows, total = _read(
        opener, lambda store: store.list_tool_executions(exec_status, tool, limit, offset)
    )
    items = [
        {
            "id": str(r["id"]),
            "thread_id": str(r["thread_id"]),
            "turn_id": str(r["turn_id"]),
            "call_id": r["call_id"],
            "tool_name": r["tool_name"],
            "decision": r["decision"],
            "status": r["status"],
            "approved_by": r["approved_by"],
            "created_at": _iso(r["created_at"]),
            "completed_at": _iso(r["completed_at"]),
        }
        for r in rows
    ]
    return _page(items, total, limit, offset)
