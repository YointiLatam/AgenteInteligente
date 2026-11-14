import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import create_app

ALLOWED = "https://cliente.example.com"
OTHER = "https://malo.example.com"
PATHS = ["/auth/token", "/chat/stream", "/chat/approval"]


def _client(monkeypatch, origins):
    if origins is None:
        monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origins)
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset():
    yield
    get_settings.cache_clear()


def _preflight(client, path, origin):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-api-key,authorization",
        },
    )


@pytest.mark.parametrize("path", PATHS)
def test_preflight_from_allowed_origin(monkeypatch, path):
    client = _client(monkeypatch, f"{ALLOWED}, https://otro.example.com")
    response = _preflight(client, path, ALLOWED)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "x-api-key" in allowed_headers and "authorization" in allowed_headers
    assert response.headers["access-control-max-age"] == "600"
    assert "access-control-allow-credentials" not in response.headers


def test_simple_request_from_allowed_origin_gets_headers(monkeypatch):
    client = _client(monkeypatch, f'["{ALLOWED}"]')
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert response.headers["access-control-allow-origin"] == ALLOWED


@pytest.mark.parametrize("path", PATHS)
def test_disallowed_origin_gets_no_allow_origin(monkeypatch, path):
    client = _client(monkeypatch, ALLOWED)
    response = _preflight(client, path, OTHER)
    assert "access-control-allow-origin" not in response.headers
    simple = client.get("/health", headers={"Origin": OTHER})
    assert "access-control-allow-origin" not in simple.headers


@pytest.mark.parametrize("value", [None, "", "  "])
def test_empty_config_adds_no_cors_headers(monkeypatch, value):
    client = _client(monkeypatch, value)
    response = client.get("/health", headers={"Origin": ALLOWED})
    assert not [h for h in response.headers if h.startswith("access-control")]
    assert (
        "access-control-allow-origin" not in _preflight(client, "/chat/approval", ALLOWED).headers
    )


def test_wildcard_is_rejected(monkeypatch):
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValueError):
        Settings()
