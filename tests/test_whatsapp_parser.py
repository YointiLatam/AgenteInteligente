from app.whatsapp.parser import parse_text_messages


def _payload(messages=None, statuses=None):
    value = {"messaging_product": "whatsapp"}
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {"entry": [{"changes": [{"value": value}]}]}


def test_text_message():
    msg = {"id": "wamid.1", "from": "5215512345678", "type": "text", "text": {"body": "Hola"}}
    [parsed] = parse_text_messages(_payload([msg]))
    assert (parsed.message_id, parsed.wa_id, parsed.body) == ("wamid.1", "5215512345678", "Hola")


def test_status_only_payload_is_ignored():
    assert parse_text_messages(_payload(statuses=[{"id": "wamid.1", "status": "read"}])) == []


def test_unsupported_type_is_ignored():
    image = {"id": "wamid.2", "from": "521", "type": "image", "image": {"id": "x"}}
    assert parse_text_messages(_payload([image])) == []


def test_mixed_messages_keep_only_text():
    text = {"id": "a", "from": "1", "type": "text", "text": {"body": "uno"}}
    image = {"id": "b", "from": "1", "type": "image"}
    assert [m.message_id for m in parse_text_messages(_payload([image, text]))] == ["a"]


def test_malformed_payloads_do_not_raise():
    for payload in (None, [], {}, {"entry": "x"}, {"entry": [{"changes": [{"value": 1}]}]}):
        assert parse_text_messages(payload) == []
    broken = {"id": 1, "from": "1", "type": "text", "text": {"body": "x"}}
    assert parse_text_messages(_payload([broken, {"type": "text", "text": "x"}])) == []
