# Protocolo del widget `<ai-chat-agent>`

Contrato entre el widget (`widget/`) y el backend. Las secciones marcadas **[existe]** ya están implementadas en el backend; las marcadas **[pendiente]** son el contrato que el backend debe implementar para el flujo de aprobación (HITL).

## Autenticación **[existe]**

El backend acepta `Authorization: Bearer <JWT>`, `?token=<JWT>` (solo WebSocket) o `X-API-Key`.

Los navegadores no pueden enviar cabeceras personalizadas en un WebSocket. Por eso el widget, si recibe `api-key`:

1. Llama `POST {api-url}/auth/token` con la cabecera `X-API-Key` (existe en `app/api/routes/auth.py`) y recibe `{"access_token", "token_type", "expires_in"}`.
2. Abre `wss://.../chat/ws?token=<access_token>`.

El SSE (`POST /chat/stream`) envía `X-API-Key` directamente. Si el canje falla o el WebSocket no logra abrir tras 3 intentos, el widget cae a SSE.

Limitaciones conocidas: el JWT viaja en la URL del WebSocket y puede quedar en logs de proxies (usar tokens de vida corta). Cada reconexión canjea un token nuevo. La CORS del endpoint `/auth/token` debe permitir el origen que embebe el widget.

## Chat **[existe]**

- WebSocket `/chat/ws`. Cliente envía `{"prompt": "..."}`. Servidor responde `{"type":"token","text":"..."}` (repetido), y termina con `{"type":"done"}` o `{"type":"error","message":"..."}`.
- SSE `POST /chat/stream` con cuerpo `{"prompt": "..."}`. Frames `event: token|error|done` y `data:` con el mismo JSON (sin `type` obligatorio; el widget usa el nombre del evento).

## Reconexión

Backoff exponencial con jitter: `exp = min(15000, 500 * 2^intento)` ms; espera aleatoria en `[exp * 0.5, exp]`. Tras 3 fallos consecutivos sin abrir, se pasa a SSE. Durante los reintentos el widget muestra "Reconectando...".

## Aprobación de acciones (HITL)

### Servidor a cliente

```json
{"type":"approval_required","thread_id":"<uuid>","turn_id":"<uuid>","call_id":"<str>","tool_name":"<str>","parameters":{"...":"..."},"status":"pending_approval"}
```

Se emite por WebSocket, o como `event: approval_required` en el stream SSE, cuando el grafo se detiene esperando aprobación. `thread_id`, `turn_id` y `call_id` identifican la ejecución pendiente. `parameters` es un objeto; el widget lo muestra como texto escapado.

### Cliente a servidor

```json
{"type":"approval","thread_id":"<uuid>","turn_id":"<uuid>","call_id":"<str>","tool_name":"<str>","decision":"approved","reason":"opcional"}
```

El cliente devuelve tal cual `thread_id`, `turn_id`, `call_id` y `tool_name` del frame `approval_required`. `decision` es `approved` o `rejected`; `reason` es opcional (máx. 1000 caracteres). `decision_id` es opcional: si falta, el servidor deriva uno estable de `thread_id`, `call_id` y `decision`, de modo que repetir el mismo mensaje es inofensivo (idempotente). El aprobador (`approved_by`) es siempre el cliente autenticado; cualquier `approved_by` del cuerpo se ignora.

En WebSocket se envía por el mismo socket. En modo SSE se envía `POST /chat/approval` con el mismo cuerpo JSON y las mismas cabeceras de autenticación (`X-API-Key` o `Authorization`).

### Resultado

```json
{"type":"approval_result","call_id":"<str>","status":"executed","message":"opcional"}
```

`status` es `executed` (aprobada y ejecutada) o `rejected`. Por WebSocket llega como frame; en `POST /chat/approval` la respuesta JSON es `{"call_id","status","message?"}` (el widget le añade `type`). El widget lo muestra como mensaje de sistema en la conversación.

### Errores

| Situación | HTTP (`POST /chat/approval`) | WebSocket |
| --- | --- | --- |
| Sin credenciales o inválidas | 401 | cierre 1008 al conectar |
| Cuerpo mal formado, `decision` inválida, ids no UUID | 422 | `{"type":"error","message":"...","status":422}` |
| Hilo inexistente | 404 | `status: 404` |
| Acción no pendiente, ya decidida de otro modo, o ejecución sin confirmar | 409 | `status: 409` |
| La ejecución de la acción falló | 502 | `status: 502` |
| Servicio de conversación no configurado | 503 | `status: 503` |

Repetir la misma decisión devuelve el mismo resultado (200). En WebSocket un frame inválido nunca cierra el socket. Los mensajes de error no incluyen detalles internos.

## CORS

La API solo envía cabeceras CORS a los orígenes listados en `CORS_ALLOWED_ORIGINS` (lista JSON o separada por comas, por ejemplo `https://cliente.example.com,https://otro.example.com`). Vacío (por defecto) no añade ninguna cabecera. No se admite `*` ni credenciales (cookies). Se permiten los métodos `GET`, `POST` y `OPTIONS`, las cabeceras `Authorization`, `Content-Type` y `X-API-Key`, y el preflight se cachea `CORS_MAX_AGE` segundos (600 por defecto). Los WebSocket no pasan por CORS del navegador.

## Quién implementa qué

- Widget: validación del frame, modal accesible (foco atrapado; `Esc` solo mueve el foco a "Rechazar", nunca decide), envío de la decisión con los identificadores y reflejo del resultado.
- Backend (hecho): `POST /chat/approval` y frame `approval` en `WS /chat/ws`, que reanudan el grafo con `ConversationService.decide_tool_call` y responden `approval_result`.
- Pendiente: el chat (`/chat/ws`, `/chat/stream`) todavía usa `EchoStreamer`; el agente no está conectado al chat, así que nadie emite aún `approval_required` ni `thread_id`/`turn_id` reales.
