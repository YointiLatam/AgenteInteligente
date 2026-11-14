# Canal de WhatsApp entrante

Webhook de la WhatsApp Cloud API conectado al agente. Endpoint: `/webhooks/whatsapp`.

## Variables de entorno

| Variable | Uso |
| --- | --- |
| `WHATSAPP_VERIFY_TOKEN` | Token que tú eliges; Meta lo envía en el handshake `GET`. |
| `WHATSAPP_APP_SECRET` | App Secret de la app de Meta; firma de cada `POST` (`X-Hub-Signature-256`). |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | Ya existentes; se usan para responder. |
| `DATABASE_URL`, `GEMINI_API_KEY` | Necesarias para construir el servicio de conversación (`app/conversations/wiring.py`). |

Sin `WHATSAPP_APP_SECRET` el `POST` responde 503 y no procesa nada; sin `WHATSAPP_VERIFY_TOKEN`
el `GET` responde 503. Nunca se acepta tráfico sin firma.

## Configuración en Meta

1. En la app de Meta for Developers, agrega el producto WhatsApp y copia el App Secret.
2. En Configuración del webhook, indica `https://<tu-dominio>/webhooks/whatsapp` y tu verify token.
3. Meta llama al `GET` con `hub.mode=subscribe`; la API devuelve `hub.challenge` en texto plano.
4. Suscríbete al campo `messages`.
5. Para pruebas usa el número de prueba y agrega tu teléfono como destinatario permitido.

## Flujo

```
Meta --POST--> valida HMAC del cuerpo crudo --(falla)--> 401
                  | ok
                  v
          200 inmediato + tarea en segundo plano
                  v
   parser (solo texto; estados y otros tipos se ignoran)
                  v
   process_user_turn(channel="whatsapp", external_conversation_id=<wa_id>,
                     request_id=<id del mensaje de WhatsApp>)   [hilo aparte]
                  v
   respuesta  -> envío de texto por WhatsApp
   pendiente de aprobación -> aviso breve en español
   entrada bloqueada       -> respuesta neutral
   replayed=True           -> no se envía nada
```

## Idempotencia

El id del mensaje de WhatsApp (`wamid...`) es el `request_id` del turno. Si Meta reintenta la
entrega, el servicio reconoce el turno ya guardado y devuelve `replayed=True`; el handler no
vuelve a responder. Los errores se registran y nunca se propagan a Meta, que de otro modo
reintentaría. Limitación: si el proceso cae después de guardar la respuesta pero antes de
enviarla, el reintento no reenvía el mensaje.
