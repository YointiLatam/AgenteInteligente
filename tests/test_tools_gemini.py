import pytest
from pydantic import BaseModel

from app.integrations.clients import Provider
from app.integrations.settings import IntegrationsSettings
from app.tools.base import ToolError
from tests.test_tools_base import EchoTool


def test_declaracion_gemini_sin_campos_no_soportados():
    declaration = EchoTool.declaration()

    assert declaration["name"] == "echo"
    assert declaration["parameters"]["required"] == ["text"]
    assert declaration["parameters"]["properties"]["times"] == {
        "type": "integer",
        "description": "Repeticiones",
    }
    assert "title" not in str(declaration)


def test_declaracion_rechaza_modelos_anidados():
    class Inner(BaseModel):
        x: int

    class Outer(BaseModel):
        inner: Inner

    class Nested(EchoTool):
        input_model = Outer

    with pytest.raises(ValueError, match="anidados"):
        Nested.declaration()


def test_cliente_http_usa_credenciales_del_entorno():
    class MetaTool(EchoTool):
        provider = Provider.META

    settings = IntegrationsSettings(_env_file=None, meta_access_token="secreto")

    client = MetaTool(settings=settings).http_client()

    assert client.headers["Authorization"] == "Bearer secreto"


def test_cliente_http_requiere_proveedor():
    with pytest.raises(RuntimeError):
        EchoTool().http_client()


def test_cliente_http_sin_credenciales_es_un_error_estandar():
    class MetaTool(EchoTool):
        provider = Provider.META

    with pytest.raises(ToolError) as error:
        MetaTool(settings=IntegrationsSettings(_env_file=None)).http_client()

    assert error.value.code == "not_configured"
