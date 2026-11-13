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
