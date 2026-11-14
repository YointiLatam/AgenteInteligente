"""Shared HITL approval logic for ``POST /chat/approval`` and the WebSocket ``approval`` frame.

The approver is always the authenticated client, never a value from the payload. The
``ConversationService`` is synchronous (psycopg + LangGraph), so it runs in a worker thread.
"""

import asyncio
import logging
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, Field, ValidationError

from app.conversations.repository import ConversationPersistenceError
from app.conversations.service import (
    ThreadPausedError,
    ToolDecisionConflictError,
    ToolDecisionOutOfOrderError,
    ToolDecisionRequest,
    ToolExecutionFailedError,
    ToolExecutionUncertainError,
)
from app.conversations.wiring import ServiceNotConfigured, ServiceOpener

logger = logging.getLogger(__name__)

# Message raised by ConversationRepository.get_thread for an unknown thread.
_THREAD_NOT_FOUND = "No se encontró la conversación"
_DECISION_NAMESPACE = UUID("6f1d3c52-8a0e-4b57-9c1a-2d7e5b0a4f31")


class ApprovalIn(BaseModel):
    """Client message. Unknown fields (e.g. ``approved_by``) are ignored on purpose."""

    thread_id: UUID
    turn_id: UUID
    call_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=200)
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=1000)
    decision_id: str | None = Field(default=None, min_length=1, max_length=200)


class ApprovalError(Exception):
    """A failed approval with the HTTP status that describes it and a public message."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def parse_approval(data: Any) -> ApprovalIn:
    try:
        return ApprovalIn.model_validate(data)
    except ValidationError as error:
        raise ApprovalError(422, "Decisión de aprobación no válida") from error


def _decision_id(payload: ApprovalIn) -> str:
    if payload.decision_id:
        return payload.decision_id
    # Deterministic: a repeated decision for the same call maps to the same row (idempotent).
    return str(
        uuid5(_DECISION_NAMESPACE, f"{payload.thread_id}:{payload.call_id}:{payload.decision}")
    )


def _decide(opener: ServiceOpener, request: ToolDecisionRequest) -> dict[str, Any]:
    with opener() as service:
        result = service.decide_tool_call(request)
    status = "executed" if result.execution.decision == "approved" else "rejected"
    return {"type": "approval_result", "call_id": result.execution.call_id, "status": status}


async def process_approval(opener: ServiceOpener, client: str, data: Any) -> dict[str, Any]:
    """Validate, resume the graph and return an ``approval_result`` frame or raise ApprovalError."""
    payload = parse_approval(data)
    request = ToolDecisionRequest(
        decision_id=_decision_id(payload),
        thread_id=payload.thread_id,
        turn_id=payload.turn_id,
        call_id=payload.call_id,
        tool_name=payload.tool_name,
        decision=payload.decision,
        approved_by=client,
        reason=payload.reason,
    )
    try:
        return await asyncio.to_thread(_decide, opener, request)
    except ServiceNotConfigured as error:
        raise ApprovalError(503, "Servicio de conversación no configurado") from error
    except ValueError as error:
        raise ApprovalError(422, "Decisión de aprobación no válida") from error
    except (ToolDecisionConflictError, ToolDecisionOutOfOrderError, ThreadPausedError) as error:
        raise ApprovalError(409, "La acción ya no está pendiente o tiene otra decisión") from error
    except ToolExecutionUncertainError as error:
        raise ApprovalError(409, "La ejecución no pudo confirmarse; no se repetirá") from error
    except ToolExecutionFailedError as error:
        raise ApprovalError(502, "La ejecución de la acción falló") from error
    except ConversationPersistenceError as error:
        if str(error) == _THREAD_NOT_FOUND:
            raise ApprovalError(404, "No se encontró la conversación") from error
        logger.exception("Falló la persistencia al registrar la aprobación")
        raise ApprovalError(503, "No se pudo registrar la decisión") from error
    except Exception as error:
        logger.exception("Falló la aprobación")
        raise ApprovalError(500, "No se pudo procesar la decisión") from error
