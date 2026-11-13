# Chat en streaming (WebSocket y SSE)

| Canal | Ruta | Uso |
|---|---|---|
| WebSocket | `/chat/ws` | Chat bidireccional del widget; admite varios mensajes por conexión |
| SSE | `POST /chat/stream` | Alternativa para clientes sin WebSocket |

## Autenticación (al abrir la conexión)

- JWT: `Authorization: Bearer <jwt>` o, solo en WebSocket, `?token=<jwt>`.
- API key: cabecera `X-API-Key`.
- Sin credenciales válidas: el WebSocket se cierra con código `1008` y el SSE responde `401`.

`?token=` existe porque el navegador no permite cabeceras al abrir un WebSocket. Queda en los
logs de acceso del proxy: usar siempre el JWT de 15 minutos, nunca la API key, y no loguear
la query string.

## Protocolo

- Cliente → servidor: `{"prompt": "..."}` (1 a 8000 caracteres).
- Servidor → cliente (WebSocket): `{"type": "token", "text": "..."}`, luego `{"type": "done"}`
  o `{"type": "error", "message": "..."}`.
- SSE: eventos `token` (`{"text": "..."}`), `done` y `error`, con datos en JSON.
- Los errores del modelo se registran en el log; el cliente solo recibe un mensaje genérico.

## Conectar el modelo

La fuente de tokens es el protocolo `TokenStreamer` (`app/chat/streaming.py`). Hoy
`get_streamer()` devuelve `EchoStreamer`, que repite el prompt. Para usar Gemini basta con
devolver una clase que implemente `stream(prompt)` como iterador asíncrono de textos.
