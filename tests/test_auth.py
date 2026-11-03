from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.auth.security import hash_api_key
from app.auth.settings import get_auth_settings
from app.main import app

SECRET = "s" * 40
API_KEY = "clave-de-prueba"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("AUTH_API_KEYS", f'{{"cliente-demo": "{hash_api_key(API_KEY)}"}}')
    get_auth_settings.cache_clear()
    yield TestClient(app)
    get_auth_settings.cache_clear()


def _token(client) -> str:
    return client.post("/auth/token", headers={"X-API-Key": API_KEY}).json()["access_token"]


def test_token_con_api_key_valida(client):
    response = client.post("/auth/token", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "otra-clave"}])
def test_token_rechaza_api_key_invalida_o_ausente(client, headers):
    assert client.post("/auth/token", headers=headers).status_code == 401


def test_me_con_token_valido(client):
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {_token(client)}"})

    assert response.status_code == 200
    assert response.json() == {"client_id": "cliente-demo"}


def test_me_rechaza_token_expirado(client):
    claims = {"sub": "cliente-demo", "exp": datetime.now(UTC) - timedelta(minutes=1)}
    expired = jwt.encode(claims, SECRET, "HS256")

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


def test_me_rechaza_token_firmado_con_otra_clave(client):
    claims = {"sub": "x", "exp": datetime.now(UTC) + timedelta(minutes=5)}
    forged = jwt.encode(claims, "o" * 40, "HS256")

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401


def test_me_sin_token(client):
    assert client.get("/auth/me").status_code == 401


def test_health_sigue_publico(client):
    assert client.get("/health").status_code == 200
