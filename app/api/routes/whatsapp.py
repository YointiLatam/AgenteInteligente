import hmac
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import PlainTextResponse

from app.conversations.wiring import ServiceOpener, get_conversation_service_opener
from app.integrations.clients import Provider, ProviderNotConfigured, build_client
from app.integrations.settings import get_integrations_settings
from app.whatsapp.handler import WhatsAppSender, handle_messages
from app.whatsapp.parser import InboundText, parse_text_messages
from app.whatsapp.settings import WhatsAppWebhookSettings, get_whatsapp_webhook_settings
from app.whatsapp.signature import verify_signature

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])

WebhookSettings = Annotated[WhatsAppWebhookSettings, Depends(get_whatsapp_webhook_settings)]
Opener = Annotated[ServiceOpener, Depends(get_conversation_service_opener)]


SenderFactory = Callable[[], AbstractAsyncContextManager[WhatsAppSender | None]]


@asynccontextmanager
async def _default_sender() -> AsyncIterator[WhatsAppSender | None]:
    settings = get_integrations_settings()
    try:
        client = build_client(Provider.WHATSAPP, settings)
    except ProviderNotConfigured:
        yield None
        return
    async with client:
        yield WhatsAppSender(client, settings.whatsapp_phone_number_id or "")


def get_sender_factory() -> SenderFactory:
    """Dependency so tests can swap the outbound WhatsApp client."""
    return _default_sender


async def _deliver(
    messages: list[InboundText], opener: ServiceOpener, sender_factory: SenderFactory
) -> None:
    async with sender_factory() as sender:
        if sender is None:
            logger.error(
                "WhatsApp saliente no configurado; se descartan %d mensajes", len(messages)
            )
            return
        await handle_messages(messages, opener=opener, sender=sender)


@router.get("", response_class=PlainTextResponse)
async def verify(
    settings: WebhookSettings,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    """Meta verification handshake."""
    expected = settings.whatsapp_verify_token
    if expected is None:
        logger.error("WHATSAPP_VERIFY_TOKEN no está configurado")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook no configurado")
    if mode != "subscribe" or token is None or challenge is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Verificación inválida")
    if not hmac.compare_digest(token.encode(), expected.get_secret_value().encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Verificación inválida")
    return challenge


@router.post("")
async def receive(
    request: Request,
    background: BackgroundTasks,
    settings: WebhookSettings,
    opener: Opener,
    sender_factory: Annotated[SenderFactory, Depends(get_sender_factory)],
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    """Inbound events. The HMAC signature is the authentication (Meta sends no JWT)."""
    secret = settings.whatsapp_app_secret
    if secret is None:
        logger.error("WHATSAPP_APP_SECRET no está configurado; se rechaza el webhook")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook no configurado")
    body = await request.body()
    if not verify_signature(secret.get_secret_value(), body, x_hub_signature_256):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Firma inválida")
    try:
        messages = parse_text_messages(json.loads(body))
    except ValueError:
        messages = []
    if messages:
        background.add_task(_deliver, messages, opener, sender_factory)
    return {"status": "ok"}
