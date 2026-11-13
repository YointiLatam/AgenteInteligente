import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth.security import create_access_token, hash_api_key
from app.auth.settings import get_auth_settings
from app.chat.streaming import get_streamer
from app.main import app

API_KEY = "clave-de-prueba"


class FakeStreamer:
    def __init__(self, fail_after=None):
        self.fail_after = fail_after

    async def stream(self, prompt):
        for index, token in enumerate(["Hola", " ", prompt]):
            if self.fail_after == index:
                raise RuntimeError("modelo caído: secreto-interno")
            yield token


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("AUTH_API_KEYS", json.dumps({"demo": hash_api_key(API_KEY)}))
    get_auth_settings.cache_clear()
    app.dependency_overrides[get_streamer] = FakeStreamer
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_auth_settings.cache_clear()


def _jwt() -> str:
    return create_access_token("demo", get_auth_settings())


def _frames(ws, until="done"):
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in (until, "error"):
            return frames


def test_ws_transmite_tokens_con_api_key(client):
    with client.websocket_connect("/chat/ws", headers={"X-API-Key": API_KEY}) as ws:
        ws.send_json({"prompt": "mundo"})

        frames = _frames(ws)

    assert [f["text"] for f in frames if f["type"] == "token"] == ["Hola", " ", "mundo"]
    assert frames[-1] == {"type": "done"}


def test_ws_acepta_jwt_por_query_y_atiende_varios_mensajes(client):
    with client.websocket_connect(f"/chat/ws?token={_jwt()}") as ws:
        for prompt in ("uno", "dos"):
            ws.send_json({"prompt": prompt})
            assert _frames(ws)[-1] == {"type": "done"}


@pytest.mark.parametrize("url", ["/chat/ws", "/chat/ws?token=falso"])
def test_ws_rechaza_conexiones_sin_credenciales_validas(client, url):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(url):
            pass

    assert error.value.code == 1008


def test_ws_mensaje_invalido_no_cierra_la_conexion(client):
    with client.websocket_connect("/chat/ws", headers={"X-API-Key": API_KEY}) as ws:
        ws.send_text("no es json")
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"prompt": "ok"})

        assert _frames(ws)[-1] == {"type": "done"}


def test_ws_error_del_modelo_no_filtra_detalles(client):
    app.dependency_overrides[get_streamer] = lambda: FakeStreamer(fail_after=1)
    with client.websocket_connect("/chat/ws", headers={"X-API-Key": API_KEY}) as ws:
        ws.send_json({"prompt": "x"})

        last = _frames(ws)[-1]

    assert last["type"] == "error"
    assert "secreto-interno" not in last["message"]


def test_sse_emite_eventos_token_y_done(client):
    response = client.post(
        "/chat/stream", json={"prompt": "mundo"}, headers={"Authorization": f"Bearer {_jwt()}"}
    )

    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.count("event: token") == 3
    assert 'data: {"text": "mundo"}' in response.text
    assert response.text.endswith("event: done\ndata: {}\n\n")


def test_sse_acepta_api_key(client):
    response = client.post("/chat/stream", json={"prompt": "x"}, headers={"X-API-Key": API_KEY})

    assert response.status_code == 200


def test_sse_exige_autenticacion_y_prompt_valido(client):
    assert client.post("/chat/stream", json={"prompt": "x"}).status_code == 401
    bad = client.post("/chat/stream", json={"prompt": ""}, headers={"X-API-Key": API_KEY})
    assert bad.status_code == 422


def test_sse_error_del_modelo_se_informa_como_evento(client):
    app.dependency_overrides[get_streamer] = lambda: FakeStreamer(fail_after=0)

    response = client.post("/chat/stream", json={"prompt": "x"}, headers={"X-API-Key": API_KEY})

    assert "event: error" in response.text
    assert "secreto-interno" not in response.text
