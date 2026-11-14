# Widget `<ai-chat-agent>`

Web Component embebible (Shadow DOM abierto, sin dependencias de ejecución) que conecta con el backend del agente por WebSocket, con respaldo SSE.

## Uso

```html
<script src="https://TU-DOMINIO/ai-chat-agent.js"></script>
<ai-chat-agent
  api-url="https://api.tu-dominio.com"
  api-key="TU_API_KEY"
  title="Asistente"
  color="#2563eb"
  position="bottom-right"
></ai-chat-agent>
```

| Atributo | Obligatorio | Descripción |
| --- | --- | --- |
| `api-url` | sí | URL base del backend (sin `/chat/ws`). |
| `api-key` | no | API key del cliente. Se canjea por un JWT en `POST /auth/token` para el WebSocket; el SSE la envía en `X-API-Key`. |
| `title` | no | Título del panel (por defecto "Asistente"). |
| `color` | no | Color de acento (hex, `rgb()`, `hsl()` o nombre CSS). |
| `position` | no | `bottom-right` (por defecto) o `bottom-left`. |

Accesibilidad: el registro de mensajes usa `aria-live`, `Esc` cierra el panel, se respeta `prefers-reduced-motion` y en pantallas de 480 px o menos el panel ocupa toda la pantalla.

Nota de seguridad: una API key en una página pública es visible para cualquiera. Usa claves de alcance limitado por dominio. Detalles del protocolo en `PROTOCOL.md`.

## Desarrollo

```bash
npm install
npm run lint     # ESLint
npm test         # node --test (markdown, SSE, backoff, aprobación)
npm run build    # genera dist/ai-chat-agent.js (un solo IIFE minificado)
```

Estructura: `src/markdown.js` (renderer seguro), `src/transport.js` (WS/SSE), `src/approval.js` (modal HITL), `src/ai-chat-agent.js` (elemento).
