import pytest
from fastapi.testclient import TestClient

from app.auth.security import create_access_token
from app.auth.settings import get_auth_settings
from app.integrations.clients import Provider, ProviderNotConfigured, build_client, is_configured
from app.integrations.settings import IntegrationsSettings, get_integrations_settings
from app.main import app


def _settings(**values) -> IntegrationsSettings:
    return IntegrationsSettings(_env_file=None, **values)


def test_sandbox_activo_por_defecto():
    assert _settings().sandbox is True


def test_los_secretos_no_aparecen_en_repr():
    settings = _settings(meta_access_token="token-super-secreto")

    assert "token-super-secreto" not in repr(settings)


def test_cliente_meta_usa_version_configurada_y_bearer():
    settings = _settings(meta_access_token="abc", meta_graph_version="v99.0")

    client = build_client(Provider.META, settings)

    assert str(client.base_url) == "https://graph.facebook.com/v99.0/"
    assert client.headers["Authorization"] == "Bearer abc"


def test_sin_credenciales_se_rechaza_el_cliente():
    with pytest.raises(ProviderNotConfigured):
        build_client(Provider.X, _settings())


def test_whatsapp_requiere_token_y_numero_de_prueba():
    assert not is_configured(Provider.WHATSAPP, _settings(whatsapp_access_token="t"))
    assert is_configured(
        Provider.WHATSAPP, _settings(whatsapp_access_token="t", whatsapp_phone_number_id="123")
    )


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("X_BEARER_TOKEN", "x-secreto")
    get_auth_settings.cache_clear()
    get_integrations_settings.cache_clear()
    yield TestClient(app), create_access_token("demo", get_auth_settings())
    get_auth_settings.cache_clear()
    get_integrations_settings.cache_clear()


def test_status_exige_autenticacion(api):
    client, _ = api

    assert client.get("/integrations/status").status_code == 401


def test_status_informa_sin_filtrar_secretos(api):
    client, token = api

    response = client.get("/integrations/status", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["providers"] == {"meta": False, "whatsapp": False, "x": True}
    assert "x-secreto" not in response.text
