from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InboundText:
    message_id: str
    wa_id: str
    body: str
    raw: dict[str, Any]


def parse_text_messages(payload: Any) -> list[InboundText]:
    """Extract text messages; statuses and unsupported types are ignored without error."""
    messages: list[InboundText] = []
    if not isinstance(payload, dict):
        return messages
    for entry in _dicts(payload.get("entry")):
        for change in _dicts(entry.get("changes")):
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            for message in _dicts(value.get("messages")):
                text = message.get("text")
                body = text.get("body") if isinstance(text, dict) else None
                message_id, sender = message.get("id"), message.get("from")
                if (
                    message.get("type") == "text"
                    and isinstance(body, str)
                    and body.strip()
                    and isinstance(message_id, str)
                    and isinstance(sender, str)
                ):
                    messages.append(InboundText(message_id, sender, body, message))
    return messages


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
