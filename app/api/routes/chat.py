import json
import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from app.auth.dependencies import Settings
from app.chat.auth import resolve_client
from app.chat.streaming import TokenStreamer, get_streamer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

Streamer = Annotated[TokenStreamer, Depends(get_streamer)]
GENERIC_ERROR = "No se pudo generar la respuesta"


class PromptIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


def _sse(event: str, data: dict) -> str:
    # El JSON escapa los saltos de línea, que de otro modo romperían el framing de SSE
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def stream_sse(
    body: PromptIn,
    settings: Settings,
    streamer: Streamer,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Alternativa SSE para clientes sin WebSocket (POST + fetch con lectura en streaming)."""
    if resolve_client(settings, authorization=authorization, api_key=x_api_key) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales inválidas o ausentes")

    async def events():
        try:
            async for token in streamer.stream(body.prompt):
                yield _sse("token", {"text": token})
        except Exception:
            logger.exception("Falló el streaming SSE")
            yield _sse("error", {"message": GENERIC_ERROR})
            return
        yield _sse("done", {})

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


@router.websocket("/ws")
async def chat_ws(websocket: WebSocket, settings: Settings, streamer: Streamer) -> None:
    """Chat bidireccional: el cliente envía {"prompt": "..."} y recibe token/done/error."""
    client = resolve_client(
        settings,
        authorization=websocket.headers.get("authorization"),
        api_key=websocket.headers.get("x-api-key"),
        token=websocket.query_params.get("token"),
    )
    if client is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    try:
        while True:
            try:
                prompt = PromptIn.model_validate_json(await websocket.receive_text()).prompt
            except ValidationError:
                await websocket.send_json(
                    {"type": "error", "message": 'Se espera {"prompt": "..."}'}
                )
                continue
            try:
                async for token in streamer.stream(prompt):
                    await websocket.send_json({"type": "token", "text": token})
            except WebSocketDisconnect:
                raise
            except Exception:
                logger.exception("Falló el streaming por WebSocket")
                await websocket.send_json({"type": "error", "message": GENERIC_ERROR})
                continue
            await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        return
