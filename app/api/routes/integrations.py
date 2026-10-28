from fastapi import APIRouter, Depends

from app.auth.dependencies import client_from_token
from app.integrations.clients import Provider, is_configured
from app.integrations.settings import get_integrations_settings

router = APIRouter(prefix="/integrations", tags=["integraciones"])


@router.get("/status", dependencies=[Depends(client_from_token)])
def status() -> dict:
    """Indica qué proveedores tienen credenciales, sin exponer ningún valor."""
    settings = get_integrations_settings()
    return {
        "sandbox": settings.sandbox,
        "providers": {p.value: is_configured(p, settings) for p in Provider},
    }
