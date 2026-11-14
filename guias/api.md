# Referencia técnica de la API

API del agente (FastAPI). Esta guía describe lo que existe hoy en el código. Lo que aún no está conectado se marca como **pendiente**.

Los encabezados `### MÉTODO /ruta` de este documento son verificados por `tests/test_guias_endpoints.py`: si una ruta cambia o desaparece, la prueba falla. `WS` indica WebSocket.

## 1. URL base y entornos

| Entorno | URL base | Notas |
| --- | --- | --- |
| Local | `http://localhost:8000` | `poetry run uvicorn app.main:app --reload` o `docker compose up --build` |
| Staging / producción | `https://api.ejemplo.com` | Detrás de nginx con TLS (ver `deploy/README.md`) |

El entorno activo se ve en `GET /health` (campo `environment`, variable `ENVIRONMENT`). En los ejemplos se usa `https://api.ejemplo.com`; sustitúyelo por tu dominio.

## 2. Autenticación

La API acepta tres credenciales según el endpoint:

1. **API key** en la cabecera `X-API-Key`. El servidor guarda solo el SHA-256 de cada clave (`AUTH_API_KEYS`, JSON `{"cliente": "<sha256 hex>"}`) y la compara en tiempo constante.
2. **JWT Bearer** en `Authorization: Bearer <token>`. Se obtiene canjeando la API key en `POST /auth/token`. Firma HS256 por defecto (`AUTH_JWT_ALGORITHM`); el campo `sub` es el id del cliente.
3. **`?token=<JWT>`** en la URL, **solo para el WebSocket** (`/chat/ws`), porque los navegadores no pueden enviar cabeceras al abrir un WebSocket.

Vida del token: `AUTH_JWT_TTL_MINUTES` (por defecto 15 minutos). La respuesta de `/auth/token` informa `expires_in` en segundos. No hay refresh token: se canjea la API key de nuevo.

Generar el hash de una API key:

```bash
printf '%s' 'MI_API_KEY' | shasum -a 256 | cut -d' ' -f1
```

Resumen de qué acepta cada endpoint:

| Endpoint | API key | Bearer | `?token=` |
| --- | --- | --- | --- |
| `POST /auth/token` | sí | no | no |
| `GET /auth/me` | no | sí | no |
| `GET /integrations/status` | no | sí | no |
| `POST /chat/stream` | sí | sí | no |
| `POST /chat/approval` | sí | sí | no |
| `WS /chat/ws` | sí (cabecera) | sí (cabecera) | sí |
| `/webhooks/whatsapp` | no (firma HMAC) | no | no |

En `/chat/*`, si llegan `Authorization: Bearer` y `X-API-Key` a la vez, se usa el Bearer.

## 3. Formato de errores

Los errores HTTP usan el formato estándar de FastAPI:

```json
{"detail": "Credenciales inválidas o ausentes"}
```

Los errores de validación del cuerpo devuelven `422` con `detail` como lista de objetos (`loc`, `msg`, `type`). Los mensajes públicos no incluyen detalles internos. En WebSocket los errores llegan como frame `{"type":"error","message":"..."}` (ver la sección de `/chat/ws`).

La API entrante **no aplica límite de peticiones propio**: si lo necesitas, ponlo en el proxy (nginx o Cloudflare).

## 4. Endpoints

### GET /health

Sin autenticación. Estado del servicio.

```bash
curl https://api.ejemplo.com/health
```

```json
{"status": "ok", "app": "Agente Inteligente", "environment": "local"}
```

### POST /auth/token

Canjea una API key por un JWT de vida corta.

```bash
curl -X POST https://api.ejemplo.com/auth/token -H "X-API-Key: MI_API_KEY"
```

```json
{"access_token": "<jwt>", "token_type": "bearer", "expires_in": 900}
```

| Código | Causa |
| --- | --- |
| 200 | Canje correcto |
| 401 | `API key inválida o ausente` |

### GET /auth/me

Devuelve el cliente al que pertenece el JWT.

```bash
curl https://api.ejemplo.com/auth/me -H "Authorization: Bearer $TOKEN"
```

```json
{"client_id": "cliente-demo"}
```

| Código | Causa |
| --- | --- |
| 200 | Token válido |
| 401 | `Falta el token Bearer` o `Token inválido o expirado` |

### GET /integrations/status

Requiere Bearer. Indica qué proveedores externos tienen credenciales configuradas, sin exponer valores.

```bash
curl https://api.ejemplo.com/integrations/status -H "Authorization: Bearer $TOKEN"
```

```json
{"sandbox": true, "providers": {"meta": false, "whatsapp": false, "x": false}}
```

`sandbox` refleja `INTEGRATIONS_SANDBOX`. Códigos: 200, 401.

### POST /chat/stream

Chat por SSE para clientes sin WebSocket. Acepta API key o Bearer.

Cuerpo: `{"prompt": "..."}` (1 a 8000 caracteres).

```bash
curl -N -X POST https://api.ejemplo.com/chat/stream \
  -H "X-API-Key: MI_API_KEY" -H "Content-Type: application/json" \
  -d '{"prompt": "Hola"}'
```

Respuesta `200` con `Content-Type: text/event-stream`, `Cache-Control: no-cache` y `X-Accel-Buffering: no`. Cada frame es `event: <nombre>` + `data: <JSON>` + línea en blanco:

```text
event: token
data: {"text": "Echo: "}

event: token
data: {"text": "Hola "}

event: done
data: {}
```

| Evento | `data` | Significado |
| --- | --- | --- |
| `token` | `{"text": "..."}` | Fragmento de la respuesta (repetido) |
| `done` | `{}` | Fin correcto |
| `error` | `{"message": "No se pudo generar la respuesta"}` | Falló la generación; el stream termina |

Códigos: 200, 401 (`Credenciales inválidas o ausentes`), 422 (cuerpo inválido). Si el error ocurre ya iniciado el stream, el estado HTTP sigue siendo 200 y el fallo llega como evento `error`.

**Pendiente:** el streamer actual es `EchoStreamer` (devuelve el prompt palabra por palabra con el prefijo `Echo:`). El agente aún no está conectado al chat. Tampoco se emite todavía `event: approval_required` en SSE.

### WS /chat/ws

Chat bidireccional. Autenticación: `Authorization`, `X-API-Key` o `?token=<JWT>`. Con credenciales inválidas el servidor cierra con código `1008` antes de aceptar.

```text
wss://api.ejemplo.com/chat/ws?token=<jwt>
```

Frames del cliente al servidor:

| Frame | Efecto |
| --- | --- |
| `{"prompt": "..."}` | Inicia una respuesta |
| `{"type": "approval", ...}` | Decisión HITL (ver más abajo) |

Frames del servidor al cliente:

| Frame | Cuándo |
| --- | --- |
| `{"type":"token","text":"..."}` | Fragmento de respuesta (repetido) |
| `{"type":"done"}` | Fin de la respuesta |
| `{"type":"error","message":"..."}` | Error de generación o frame inválido (`Se espera {"prompt": "..."}`) |
| `{"type":"approval_result","call_id":"...","status":"executed"\|"rejected"}` | Resultado de una decisión |
| `{"type":"error","message":"...","status":<HTTP>}` | Fallo de una decisión de aprobación |
| `{"type":"approval_required",...}` | Solicitud de aprobación (**pendiente**: nada lo emite aún) |

Un frame inválido nunca cierra el socket. Frame `approval` del cliente:

```json
{"type":"approval","thread_id":"<uuid>","turn_id":"<uuid>","call_id":"<str>","tool_name":"<str>","decision":"approved","reason":"opcional"}
```

Frame `approval_required` definido en el contrato (ver `widget/PROTOCOL.md`):

```json
{"type":"approval_required","thread_id":"<uuid>","turn_id":"<uuid>","call_id":"<str>","tool_name":"<str>","parameters":{},"status":"pending_approval"}
```

### POST /chat/approval

Aprueba o rechaza una acción pendiente (HITL) y reanuda el grafo. Es el equivalente HTTP del frame `approval`, pensado para el modo SSE. Acepta API key o Bearer. El aprobador es siempre el cliente autenticado; un `approved_by` en el cuerpo se ignora. **Solo pueden aprobar los usuarios con rol `operator` o `admin`** en la tabla `users` (el nombre del cliente de la API key debe coincidir con `users.external_id`); el rol se lee de la base en cada petición. Un cliente sin ese rol recibe `403`: puede conversar, pero no aprobar.

| Campo | Tipo | Obligatorio | Notas |
| --- | --- | --- | --- |
| `thread_id` | UUID | sí | |
| `turn_id` | UUID | sí | |
| `call_id` | string | sí | 1 a 200 caracteres |
| `tool_name` | string | sí | 1 a 200 caracteres |
| `decision` | `"approved"` o `"rejected"` | sí | |
| `reason` | string | no | Máx. 1000 caracteres |
| `decision_id` | string | no | 1 a 200; si falta, el servidor deriva uno estable de `thread_id`, `call_id` y `decision` |

```bash
curl -X POST https://api.ejemplo.com/chat/approval \
  -H "X-API-Key: MI_API_KEY" -H "Content-Type: application/json" \
  -d '{"thread_id":"<uuid>","turn_id":"<uuid>","call_id":"call-1","tool_name":"x_publish_post","decision":"approved"}'
```

```json
{"call_id": "call-1", "status": "executed"}
```

`status` es `executed` (aprobada) o `rejected`. Repetir la misma decisión devuelve el mismo resultado (idempotente).

| Código | Causa |
| --- | --- |
| 200 | Decisión registrada |
| 401 | Credenciales inválidas o ausentes |
| 403 | `No tienes permisos para aprobar acciones` (el rol en `users` no es `operator` ni `admin`) |
| 404 | `No se encontró la conversación` |
| 409 | La acción ya no está pendiente, tiene otra decisión o la ejecución no pudo confirmarse |
| 422 | Cuerpo inválido |
| 502 | La ejecución de la acción falló |
| 503 | Servicio de conversación no configurado, servicio de administración no disponible o fallo al registrar la decisión |
| 500 | Error inesperado (`No se pudo procesar la decisión`) |

**Pendiente:** como el agente no está conectado al chat, hoy no existen `thread_id`/`turn_id` reales entregados al cliente por `/chat/*`.

### GET /webhooks/whatsapp

Verificación (handshake) de Meta. Sin JWT. Meta envía `hub.mode=subscribe`, `hub.verify_token` y `hub.challenge`; el servidor devuelve el `challenge` como texto plano si el token coincide con `WHATSAPP_VERIFY_TOKEN` (comparación en tiempo constante).

```bash
curl "https://api.ejemplo.com/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=TOKEN&hub.challenge=1234"
```

| Código | Causa |
| --- | --- |
| 200 | Devuelve el `challenge` |
| 403 | `Verificación inválida` (modo, token o challenge incorrectos o ausentes) |
| 503 | `Webhook no configurado` (falta `WHATSAPP_VERIFY_TOKEN`) |

### POST /webhooks/whatsapp

Eventos entrantes de WhatsApp Cloud API. La autenticación es la firma HMAC-SHA256 del cuerpo crudo, enviada por Meta en `X-Hub-Signature-256: sha256=<hex>` y calculada con `WHATSAPP_APP_SECRET`.

Ejemplo de firma para pruebas (con un secreto de prueba, nunca el real):

```bash
BODY='{"entry":[{"changes":[{"value":{"messages":[{"id":"wamid.1","from":"5215555550100","type":"text","text":{"body":"Hola"}}]}}]}]}'
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac 'SECRETO_DE_PRUEBA' | awk '{print $NF}')"
curl -X POST https://api.ejemplo.com/webhooks/whatsapp \
  -H "Content-Type: application/json" -H "X-Hub-Signature-256: $SIG" -d "$BODY"
```

```json
{"status": "ok"}
```

| Código | Causa |
| --- | --- |
| 200 | Firma válida. Responde `{"status":"ok"}` también si el cuerpo no contiene mensajes de texto (estados, otros tipos o JSON inválido se ignoran) |
| 401 | `Firma inválida` |
| 503 | `Webhook no configurado` (falta `WHATSAPP_APP_SECRET`) |

Comportamiento:

- Solo se procesan mensajes de tipo `text` con cuerpo no vacío. El procesamiento ocurre en segundo plano, después de responder 200.
- **Idempotencia:** el `id` del mensaje de Meta se usa como `request_id` del turno. Si Meta reentrega el mismo mensaje, el turno se detecta como repetido y no se envía otra respuesta.
- Si la entrada es bloqueada por el filtro de contenido se responde `No puedo ayudarte con esa solicitud.`; si requiere aprobación, `Tu solicitud requiere aprobación antes de ejecutarse. Te avisaremos al resolverla.`
- Las respuestas salientes se truncan a 4096 caracteres. Si faltan las credenciales salientes (`WHATSAPP_ACCESS_TOKEN` y `WHATSAPP_PHONE_NUMBER_ID`) el mensaje se descarta y se registra un error.
- Ningún error del procesamiento se propaga a Meta, para que no reintente por fallos propios.

## 5. Herramientas salientes: reintentos y límites de tasa

Las herramientas del agente que llaman a APIs externas (Meta, WhatsApp, X) reintentan de forma acotada (`app/tools/base.py`, `app/tools/retry.py`):

- Política por defecto: hasta 3 intentos en total, espera exponencial con jitter (base 0,5 s, máximo 8 s).
- `429`: se reintenta siempre (la API lo rechazó antes de procesar). Si la API indica cuánto esperar (`Retry-After` u otras cabeceras de uso) y es mayor a 60 s, no se espera: la herramienta falla con `upstream_error` y un `retry_after_seconds` en la auditoría.
- `502`, `503`, `504`, errores de conexión y timeouts: se reintentan solo si la herramienta es segura de repetir. Las herramientas críticas (publicar o enviar) no se reintentan si la petición pudo llegar al servidor.
- `invalid_arguments` y `not_configured` nunca se reintentan.
- Cada ejecución, incluso las fallidas y los reintentos, queda en la auditoría.

## 6. CORS

El middleware solo se activa si `CORS_ALLOWED_ORIGINS` no está vacío. **Por defecto está vacío: no se envía ninguna cabecera CORS** y los navegadores bloquean las llamadas desde otros orígenes.

- Formato: lista JSON (`["https://cliente.example.com"]`) o separada por comas.
- No se admite `*` (el arranque falla si aparece) ni credenciales (`allow_credentials=False`).
- Métodos permitidos: `GET`, `POST`, `OPTIONS`. Cabeceras: `Authorization`, `Content-Type`, `X-API-Key`.
- El preflight se cachea `CORS_MAX_AGE` segundos (600 por defecto).
- Los WebSocket no pasan por CORS del navegador.
- Las barras finales de cada origen se eliminan al leer la configuración.

## 7. OpenAPI y Swagger

La documentación automática de FastAPI solo está disponible en el entorno local (`ENVIRONMENT=local`, el valor por defecto):

| Ruta | Contenido |
| --- | --- |
| `/docs` | Swagger UI |
| `/redoc` | ReDoc |
| `/openapi.json` | Esquema OpenAPI |

En staging y producción (el despliegue define `ENVIRONMENT` con el nombre del entorno) esas rutas responden `404`, y nginx las bloquea además como segunda barrera. Para forzarlo en cualquier entorno existe `DOCS_ENABLED=true|false`, que tiene prioridad sobre `ENVIRONMENT`. Los WebSocket no aparecen en el esquema OpenAPI.

## 8. Variables de entorno

Solo nombres; nunca pongas valores reales en el repositorio. La plantilla de despliegue es `deploy/env.example`.

### Núcleo

| Variable | Requerida | Notas |
| --- | --- | --- |
| `APP_NAME` | no | Por defecto `Agente Inteligente` |
| `ENVIRONMENT` | no | Por defecto `local`; el despliegue lo fija al nombre del entorno |
| `DOCS_ENABLED` | no | `true`/`false`; fuerza `/docs`, `/redoc` y `/openapi.json` (por defecto, solo en `local`) |
| `CORS_ALLOWED_ORIGINS` | no | Vacío = sin CORS |
| `CORS_MAX_AGE` | no | 600 |

### Autenticación

| Variable | Requerida | Notas |
| --- | --- | --- |
| `AUTH_JWT_SECRET` | sí | Mínimo 32 caracteres |
| `AUTH_API_KEYS` | sí, para poder autenticar clientes | JSON `{"cliente": "<sha256 hex>"}`; por defecto vacío |
| `AUTH_JWT_ALGORITHM` | no | `HS256` |
| `AUTH_JWT_TTL_MINUTES` | no | 15 |

### Base de datos

| Variable | Requerida | Notas |
| --- | --- | --- |
| `DATABASE_URL` | sí para conversaciones, aprobaciones, checkpoints, RAG y tokens OAuth persistentes | PostgreSQL |
| `POSTGRES_USER`, `POSTGRES_DB`, `POSTGRES_PASSWORD` | solo despliegue con Compose | Los consume el contenedor de base de datos, no el código de la app |
| `REDIS_URL` | no verificado | Aparece en `deploy/env.example`, pero el código de `app/` no la lee |

### Modelo e integraciones

| Variable | Requerida | Notas |
| --- | --- | --- |
| `GEMINI_API_KEY` | para el modelo | Aún sin conectar al chat |
| `GEMINI_PRIMARY_MODEL`, `GEMINI_FALLBACK_MODEL`, `GEMINI_FALLBACK_ENABLED`, `GEMINI_FALLBACK_MAX_ATTEMPTS`, `GEMINI_FALLBACK_INPUT_TOKEN_LIMIT`, `GEMINI_FALLBACK_MAX_OUTPUT_TOKENS` | no | Tienen valor por defecto |
| `INTEGRATIONS_SANDBOX` | no | `true` por defecto; ponla en `false` en producción |
| `META_GRAPH_VERSION` | no | `v21.0` |
| `META_ACCESS_TOKEN` | no | Herramientas de Meta |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | no | Envío saliente de WhatsApp |
| `X_BEARER_TOKEN` | no | X en modo estático |
| `X_CLIENT_ID`, `X_CLIENT_SECRET`, `X_REFRESH_TOKEN`, `X_ACCESS_TOKEN_EXPIRES_AT`, `X_USER_ID` | no | OAuth2 de X (Tool Hub) |
| `META_APP_ID`, `META_APP_SECRET` | no | Renovación de tokens de Meta y WhatsApp |
| `TOKEN_ENCRYPTION_KEY` | no | Clave Fernet para tokens OAuth guardados en PostgreSQL |

### Webhook de WhatsApp

| Variable | Requerida | Notas |
| --- | --- | --- |
| `WHATSAPP_VERIFY_TOKEN` | sí, para el handshake | Sin ella el `GET` responde 503 |
| `WHATSAPP_APP_SECRET` | sí, para recibir eventos | Sin ella el `POST` responde 503 |

Los nombres con prefijo `AUTH_` se leen con `env_prefix`; el resto, por el nombre del campo en mayúsculas.
