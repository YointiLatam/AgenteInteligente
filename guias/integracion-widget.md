# Guía de integración del widget `<ai-chat-agent>`

Guía para equipos de clientes que quieren embeber el chat del agente en su sitio web. Referencia de la API: [`api.md`](api.md). Contrato de mensajes: [`widget/PROTOCOL.md`](../widget/PROTOCOL.md).

## 1. Qué es

Un Web Component (`<ai-chat-agent>`) en un único archivo JavaScript (`ai-chat-agent.js`), sin dependencias de ejecución. Usa Shadow DOM abierto, así que sus estilos no chocan con los de tu página. Muestra un botón flotante que abre un panel de chat.

Se conecta al backend por WebSocket y, si no puede, cae a SSE. Renderiza las respuestas del agente como Markdown seguro y muestra un modal cuando el agente pide aprobación de una acción (HITL).

**Estado actual del backend:** el chat todavía responde con `EchoStreamer` (repite tu mensaje con el prefijo `Echo:`). El agente no está conectado al chat y nada emite aún la solicitud de aprobación. El widget ya implementa ambos flujos; el backend los activará cuando se conecte el agente.

## 2. Inicio rápido (3 minutos)

1. Pide a quien administra la API: tu **API key**, la **URL de la API** y que agreguen el origen de tu sitio a `CORS_ALLOWED_ORIGINS`.
2. Pega esto antes de `</body>`:

```html
<script src="https://widget.ejemplo.com/ai-chat-agent.js"></script>
<ai-chat-agent
  api-url="https://api.ejemplo.com"
  api-key="TU_API_KEY"
  title="Asistente"
  color="#2563eb"
  position="bottom-right"
></ai-chat-agent>
```

3. Abre la página: aparece el botón de chat en la esquina. Si no, revisa la sección de problemas frecuentes.

## 3. Atributos

| Atributo | Obligatorio | Descripción |
| --- | --- | --- |
| `api-url` | sí | URL base de la API, sin `/chat/ws`. Sin él, el widget muestra "Falta el atributo api-url." |
| `api-key` | no | API key del cliente. Sin ella no se envían credenciales y la API responderá 401. |
| `title` | no | Título del panel. Por defecto `Asistente`. |
| `color` | no | Color de acento: hex, `rgb()`, `hsl()` o nombre CSS. Un valor que no cumpla ese formato se ignora. |
| `position` | no | `bottom-right` (por defecto) o `bottom-left`. Cualquier otro valor se corrige a `bottom-right`. |

`title`, `color` y `position` se pueden cambiar en caliente. `api-url` y `api-key` se leen al conectar el elemento: para cambiarlos, quita el elemento y vuelve a insertarlo.

## 4. De API key a JWT

1. El widget llama `POST {api-url}/auth/token` con la cabecera `X-API-Key`.
2. La API responde un JWT de vida corta (15 minutos por defecto; el campo `expires_in` lo indica).
3. El widget abre `wss://.../chat/ws?token=<jwt>`, porque un navegador no puede enviar cabeceras al abrir un WebSocket.

Cada reconexión canjea un token nuevo. El JWT viaja en la URL del WebSocket y puede quedar en logs de proxies: por eso es de vida corta. En modo SSE no hay JWT: cada petición envía `X-API-Key`.

## 5. Transporte y respaldo SSE

- Se intenta WebSocket primero (también si el navegador no lo soporta, el widget pasa directo a SSE).
- Reconexión con backoff exponencial y jitter: espera aleatoria entre `exp/2` y `exp`, con `exp = min(15000, 500 * 2^intento)` ms. Durante los reintentos muestra "Reconectando...".
- Tras 3 fallos consecutivos sin abrir el socket (o si el canje de token falla), el widget cambia a SSE (`POST /chat/stream`) y ya no vuelve a WebSocket en esa página.
- Las decisiones de aprobación van por el mismo socket, o por `POST /chat/approval` en modo SSE.

## 6. Markdown y protección XSS

Las respuestas se renderizan con un renderer propio: todo el texto se escapa como HTML **antes** de generar marcas, y solo se emiten etiquetas de una lista cerrada (encabezados, párrafos, listas, citas, código, énfasis, enlaces). Los enlaces solo admiten `http://`, `https://` y `mailto:`, se abren en una pestaña nueva con `rel="noopener noreferrer"`; cualquier otro esquema (por ejemplo `javascript:`) se descarta y queda solo el texto. Los bloques de código se muestran escapados. En el modal de aprobación, los parámetros se escriben con `textContent`, nunca como HTML.

## 7. Aprobación de acciones (HITL)

Cuando el backend lo emita, el widget recibe un frame `approval_required` y abre un modal con el nombre de la acción y sus parámetros:

```json
{"type":"approval_required","thread_id":"<uuid>","turn_id":"<uuid>","call_id":"<str>","tool_name":"<str>","parameters":{},"status":"pending_approval"}
```

El modal es accesible (`aria-modal`, foco atrapado). `Esc` solo mueve el foco a "Rechazar"; nunca decide. Al pulsar "Aprobar" o "Rechazar" se envía:

```json
{"type":"approval","thread_id":"<uuid>","turn_id":"<uuid>","call_id":"<str>","tool_name":"<str>","decision":"approved"}
```

El servidor responde `{"type":"approval_result","call_id":"...","status":"executed"|"rejected"}` y el widget lo muestra como mensaje de sistema. Repetir la misma decisión es inofensivo (idempotente). Un frame `approval_required` mal formado no abre el modal y muestra "Se recibió una solicitud de aprobación no válida.". Detalle de errores y códigos: `widget/PROTOCOL.md` y [`api.md`](api.md).

## 8. Requisitos en el sitio del cliente

### CORS (configuración en la API)

La API solo responde a los orígenes de `CORS_ALLOWED_ORIGINS` (sin comodín). Pide que agreguen exactamente el origen de tu sitio (esquema + dominio + puerto, sin barra final), por ejemplo `https://www.cliente.example.com`. Aplica a `/auth/token`, `/chat/stream` y `/chat/approval`. El WebSocket no pasa por CORS del navegador.

### CSP (configuración en tu sitio)

Si tu sitio define Content-Security-Policy, permite como mínimo:

```text
script-src  https://widget.ejemplo.com
connect-src https://api.ejemplo.com wss://api.ejemplo.com
```

El widget inserta un elemento `<style>` dentro de su Shadow DOM; con una política de estilos estricta puede hacer falta `style-src 'unsafe-inline'` (o equivalente). Este punto se deduce del código y no se probó contra todas las combinaciones de CSP.

### Origen del script

El script se sirve desde el dominio del widget; el nginx de despliegue lo entrega con `Access-Control-Allow-Origin: *` para poder cargarlo desde cualquier sitio.

## 9. Alcance de la API key

Una API key escrita en el HTML es visible para cualquiera que abra la página. Recomendaciones:

- Usa una clave **exclusiva del widget** por cliente o sitio, no una clave compartida con otras integraciones.
- Restringe el origen con `CORS_ALLOWED_ORIGINS` (limita navegadores, no scripts del lado servidor).
- Si una clave se filtra, pide que la retiren de `AUTH_API_KEYS` y emite otra: el servidor solo guarda hashes SHA-256.
- La API no aplica hoy límite de peticiones por clave; considera uno en el proxy.

## 10. Móvil y accesibilidad

- En pantallas de 480 px o menos el panel ocupa toda la pantalla (usa `100dvh`).
- En pantallas más grandes mide 380 px de ancho como máximo y se adapta al alto disponible.
- El registro de mensajes usa `aria-live="polite"`; `Esc` cierra el panel.
- Respeta `prefers-reduced-motion` y el modo oscuro del sistema (`prefers-color-scheme`).

## 11. Versionado y caché del script

- El build genera un solo archivo (`widget/dist/ai-chat-agent.js`, versión del paquete `0.1.0`).
- La plantilla nginx del despliegue lo sirve con `Cache-Control: public, max-age=300`: una actualización llega a los navegadores en unos 5 minutos.
- La URL no lleva versión hoy. Si necesitas congelar una versión, copia el archivo a tu propio hosting o pide una URL versionada al equipo del widget. No hay política de versionado semántico publicada todavía.

## 12. Problemas frecuentes

| Síntoma | Causa probable | Qué hacer |
| --- | --- | --- |
| Consola: "blocked by CORS policy" al llamar `/auth/token` o `/chat/stream` | Tu origen no está en `CORS_ALLOWED_ORIGINS`, o difiere en esquema/puerto/barra final | Pide que agreguen el origen exacto |
| El widget muestra un error y la API responde 401 | `api-key` ausente, incorrecta o su hash no está en `AUTH_API_KEYS` | Verifica la clave y que el atributo `api-key` esté escrito |
| 401 en `/chat/ws` (el socket se cierra con 1008) | Token expirado o inválido | El widget canjea uno nuevo al reconectar; si persiste, revisa la API key |
| WebSocket bloqueado (proxy corporativo, CDN sin WebSockets) | El proxy no permite el `Upgrade` | El widget cae solo a SSE tras 3 intentos; en Cloudflare activa WebSockets |
| Bucle de "Reconectando..." | Falla el canje de token o la conexión; el proxy corta conexiones inactivas | Revisa la pestaña Red, la API key y el CORS; tras 3 fallos pasa a SSE |
| Respuesta SSE llega de golpe al final | Un proxy hace buffering de `/chat/stream` | Desactiva el buffering y la caché para esa ruta (`X-Accel-Buffering: no` ya se envía) |
| No aparece nada en la página | Falló la carga del script (CSP, ruta, bloqueador) | Revisa `script-src` y la URL del script |
| "Falta el atributo api-url." | Falta `api-url` | Agrégalo |
| El agente responde `Echo: ...` | El agente aún no está conectado al chat | Es el comportamiento actual esperado |
