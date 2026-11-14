import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

import httpx

from app.conversations.service import (
    ConversationService,
    InputBlockedError,
    PendingApprovalResult,
    TurnResult,
    UserTurnRequest,
)
from app.whatsapp.parser import InboundText

logger = logging.getLogger(__name__)

PENDING_NOTICE = (
    "Tu solicitud requiere aprobación antes de ejecutarse. Te avisaremos al resolverla."
)
BLOCKED_NOTICE = "No puedo ayudarte con esa solicitud."

ServiceOpener = Callable[[], AbstractContextManager[ConversationService]]


class WhatsAppSender:
    """Sends text messages through the Cloud API using an injected async client."""

    def __init__(self, client: httpx.AsyncClient, phone_number_id: str) -> None:
        self._client = client
        self._phone_number_id = phone_number_id

    async def send_text(self, to: str, body: str) -> None:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body[:4096]},
        }
        response = await self._client.post(f"/{self._phone_number_id}/messages", json=payload)
        response.raise_for_status()


def _process(opener: ServiceOpener, message: InboundText) -> str | None:
    """Run one turn (sync, DB-bound). Returns the reply text, or None when nothing is sent."""
    request = UserTurnRequest(
        channel="whatsapp",
        external_conversation_id=message.wa_id,
        request_id=message.message_id,  # idempotency: Meta redeliveries reuse the message id
        content=message.body,
        raw_payload={"whatsapp_message_id": message.message_id},
    )
    try:
        with opener() as service:
            result = service.process_user_turn(request)
    except InputBlockedError:
        return BLOCKED_NOTICE
    if result.replayed:
        return None
    if isinstance(result, PendingApprovalResult):
        return PENDING_NOTICE
    assert isinstance(result, TurnResult)
    return result.message.content


async def handle_messages(
    messages: list[InboundText],
    *,
    opener: ServiceOpener,
    sender: WhatsAppSender,
) -> None:
    """Process each message; no exception ever escapes (Meta must not retry because of us)."""
    for message in messages:
        try:
            reply = await asyncio.to_thread(_process, opener, message)
            if reply:
                await sender.send_text(message.wa_id, reply)
        except Exception:
            logger.exception(
                "Falló el procesamiento del mensaje de WhatsApp %s", message.message_id
            )
