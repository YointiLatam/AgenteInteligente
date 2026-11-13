from pydantic import BaseModel, Field

from app.integrations.clients import Provider
from app.tools.base import BaseTool, ToolError


class SendMessageInput(BaseModel):
    to: str = Field(
        pattern=r"^\d{8,15}$", description="Teléfono del destinatario con código de país, sin +"
    )
    body: str = Field(min_length=1, max_length=4096, description="Texto del mensaje")


class WhatsAppSendMessage(BaseTool):
    name = "whatsapp_send_message"
    description = "Envía un mensaje de texto por WhatsApp Cloud API. Requiere aprobación."
    input_model = SendMessageInput
    provider = Provider.WHATSAPP
    critical = True

    async def execute(self, args: SendMessageInput) -> dict:
        phone_number_id = self._settings_or_default().whatsapp_phone_number_id
        if not phone_number_id:
            raise ToolError("not_configured", "Falta WHATSAPP_PHONE_NUMBER_ID en el entorno")
        payload = {
            "messaging_product": "whatsapp",
            "to": args.to,
            "type": "text",
            "text": {"body": args.body},
        }
        async with self.http_client() as client:
            response = await client.post(f"/{phone_number_id}/messages", json=payload)
            response.raise_for_status()
        return {"message_id": response.json()["messages"][0]["id"]}


TOOLS = [WhatsAppSendMessage]
