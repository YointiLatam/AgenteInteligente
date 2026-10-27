from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import authenticate_api_key, decode_access_token
from app.auth.settings import AuthSettings, get_auth_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer = HTTPBearer(auto_error=False)

Settings = Annotated[AuthSettings, Depends(get_auth_settings)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail)


def client_from_api_key(
    settings: Settings, api_key: Annotated[str | None, Depends(_api_key_header)]
) -> str:
    client_id = authenticate_api_key(api_key, settings) if api_key else None
    if client_id is None:
        raise _unauthorized("API key inválida o ausente")
    return client_id


def client_from_token(
    settings: Settings,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None:
        raise _unauthorized("Falta el token Bearer")
    try:
        return decode_access_token(credentials.credentials, settings)
    except jwt.InvalidTokenError:
        raise _unauthorized("Token inválido o expirado") from None
