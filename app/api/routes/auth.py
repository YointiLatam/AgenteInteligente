from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.dependencies import Settings, client_from_api_key, client_from_token
from app.auth.security import create_access_token

router = APIRouter(prefix="/auth", tags=["autenticación"])


@router.post("/token")
def issue_token(client_id: Annotated[str, Depends(client_from_api_key)], settings: Settings):
    """Canjea una API key por un JWT de vida corta."""
    return {
        "access_token": create_access_token(client_id, settings),
        "token_type": "bearer",
        "expires_in": settings.jwt_ttl_minutes * 60,
    }


@router.get("/me")
def me(client_id: Annotated[str, Depends(client_from_token)]) -> dict[str, str]:
    return {"client_id": client_id}
