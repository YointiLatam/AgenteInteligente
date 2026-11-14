from uuid import uuid4

from tests.test_admin_rbac import FakeStore, install_store, token_for
from tests.test_admin_rbac import client as _client_fixture

client = _client_fixture


def _thread(channel, external, n=0):
    return {
        "id": uuid4(),
        "channel": channel,
        "external_conversation_id": external,
        "status": "active",
        "created_at": None,
        "updated_at": None,
    }


def test_list_filters_by_channel_and_user(client):
    store = FakeStore({"ana": "operator"})
    store.threads = [
        _thread("whatsapp", "5215"),
        _thread("web_widget", "w1"),
        _thread("whatsapp", "9"),
    ]
    install_store(store)
    headers = token_for("ana")
    body = client.get("/admin/conversations?channel=whatsapp", headers=headers).json()
    assert body["total"] == 2 and len(body["items"]) == 2
    body = client.get("/admin/conversations?channel=whatsapp&user=5215", headers=headers).json()
    assert body["total"] == 1
    assert set(body["items"][0]) == {
        "thread_id",
        "channel",
        "external_conversation_id",
        "status",
        "created_at",
        "updated_at",
    }
    assert store.calls[-1] == ("list_threads", "whatsapp", "5215", 50, 0)


def test_invalid_channel_and_pagination_bounds_are_422(client):
    store = FakeStore({"ana": "operator"})
    install_store(store)
    headers = token_for("ana")
    for query in ("channel=sms", "limit=201", "limit=0", "offset=-1", "user=" + "x" * 256, "user="):
        assert client.get(f"/admin/conversations?{query}", headers=headers).status_code == 422
    assert (
        client.get("/admin/conversations?limit=200&offset=10", headers=headers).status_code == 200
    )
    assert store.calls[-1][-2:] == (200, 10)


def test_pagination_pages_results(client):
    store = FakeStore({"ana": "operator"})
    store.threads = [_thread("meta", str(i)) for i in range(5)]
    install_store(store)
    body = client.get("/admin/conversations?limit=2&offset=4", headers=token_for("ana")).json()
    assert (body["total"], len(body["items"]), body["limit"], body["offset"]) == (5, 1, 2, 4)


def test_messages_of_a_thread(client):
    store = FakeStore({"ana": "operator"})
    thread = _thread("whatsapp", "5215")
    store.threads = [thread]
    store.messages[thread["id"]] = [
        {"id": uuid4(), "turn_id": uuid4(), "role": "user", "content": "hola", "created_at": None},
        {
            "id": uuid4(),
            "turn_id": uuid4(),
            "role": "agent",
            "content": "buenas",
            "created_at": None,
        },
    ]
    install_store(store)
    body = client.get(
        f"/admin/conversations/{thread['id']}/messages?limit=1", headers=token_for("ana")
    ).json()
    assert body["thread_id"] == str(thread["id"])
    assert (body["channel"], body["external_conversation_id"]) == ("whatsapp", "5215")
    assert body["total"] == 2 and [m["content"] for m in body["items"]] == ["hola"]
    assert set(body["items"][0]) == {"id", "turn_id", "role", "content", "created_at"}


def test_unknown_thread_is_404_and_malformed_id_is_422(client):
    install_store(FakeStore({"ana": "operator"}))
    headers = token_for("ana")
    assert (
        client.get(f"/admin/conversations/{uuid4()}/messages", headers=headers).status_code == 404
    )
    assert client.get("/admin/conversations/no-uuid/messages", headers=headers).status_code == 422
