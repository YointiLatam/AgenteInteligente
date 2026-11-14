# Herramientas del agente

Cada herramienta hereda de `BaseTool` y define `name`, `description`, `input_model` (Pydantic)
y `execute()`. Todo lo demás lo hace `BaseTool.run()`:

1. valida los argumentos contra `input_model`;
2. si `critical = True` y no hay aprobación, **no ejecuta**: registra `pending_approval`;
3. ejecuta y mide la latencia (`execution_time_ms`);
4. devuelve siempre un `ToolResult` (`executed`, `failed`, `pending_approval`) y **audita** cada
   resultado, también los fallidos.

## Crear una herramienta

```python
class GetStatusInput(BaseModel):
    order_id: str = Field(description="Identificador del pedido")


class GetStatus(BaseTool):
    name = "get_order_status"
    description = "Consulta el estado de un pedido"
    input_model = GetStatusInput

    async def execute(self, args: GetStatusInput) -> dict: ...


TOOLS = [GetStatus]  # en app/tools/catalog/<modulo>.py: se registra sola
```

## Decisiones

- **Errores:** lanzá `ToolError(code, message, retryable)` para fallos esperados. Cualquier otra
  excepción se registra en el log y el agente solo ve `internal_error`, sin detalles.
- **Credenciales:** nunca son argumentos. Las herramientas con API externa declaran `provider` y
  usan `self.http_client()`, que lee el entorno (ver `app/integrations`).
- **Function Calling:** `registry.declarations()` produce la lista para Gemini. Los modelos de
  entrada deben ser planos (sin modelos anidados ni uniones); el registro falla al arrancar si no.
- **Aprobación humana:** `run(..., approved=True, approved_by=...)` es el punto de enganche para
  el flujo HITL; aquí no se implementa.
- **Auditoría:** un fallo al auditar se registra en el log pero no tumba la respuesta.
- **Persistencia:** `PgAuditSink(conexión)` guarda cada ejecución en `tool_audit_log`:
  `build_default_registry(audit=PgAuditSink(connection))`. Sin `audit`, no se registra nada.

## Tool Hub (tokens OAuth2)

`app/integrations/oauth.py` centraliza el token de acceso de Meta, WhatsApp y X (`TokenHub`):
siembra desde el entorno, renueva antes de expirar (margen de 60 s), serializa la renovación por
proveedor (una sola renovación aunque haya llamadas concurrentes) y, ante un 401, renueva una vez
y reintenta (`HubAuth`). Es opt-in: `build_client(..., hub=get_token_hub())`; sin `hub` se usa el
token estático de siempre. Las herramientas de X (`x_read_recent_posts`, `x_publish_post`, esta
última crítica/HITL) usan el hub solo si están `X_CLIENT_ID`, `X_CLIENT_SECRET` y `X_REFRESH_TOKEN`.

Variables (todas opcionales): `X_BEARER_TOKEN`, `X_CLIENT_ID`, `X_CLIENT_SECRET`,
`X_REFRESH_TOKEN`, `X_ACCESS_TOKEN_EXPIRES_AT` (epoch), `X_USER_ID`, `META_APP_ID`,
`META_APP_SECRET` (habilita `fb_exchange_token` para Meta/WhatsApp; sin ellas el token se usa tal cual).

Persistencia: X rota el refresh token en cada renovación. Con `TOKEN_ENCRYPTION_KEY` y `DATABASE_URL`
el hub guarda los tokens cifrados (Fernet) en `oauth_tokens` (`PgTokenStore`, migración
`migrations/20261113_oauth_tokens.sql`) y sobreviven a reinicios y despliegues. Un bloqueo
advisory de PostgreSQL serializa la renovación entre procesos para que dos instancias no gasten el
mismo refresh token. Sin esas variables se usa `InMemoryTokenStore` (se pierde al reiniciar y el hub lo
avisa en el log). La clave se genera con
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`; si
cambia, hay que volver a sembrar los tokens.
