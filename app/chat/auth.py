import jwt

from app.auth.security import authenticate_api_key, decode_access_token
from app.auth.settings import AuthSettings


def resolve_client(
    settings: AuthSettings,
    *,
    authorization: str | None = None,
    api_key: str | None = None,
    token: str | None = None,
) -> str | None:
    """Identifica al cliente por JWT (Bearer o `token`) o por API key; None si no es válido.

    `token` existe porque los navegadores no pueden enviar cabeceras al abrir un WebSocket.
    """
    bearer = authorization[7:] if (authorization or "").lower().startswith("bearer ") else token
    if bearer:
        try:
            return decode_access_token(bearer, settings)
        except jwt.InvalidTokenError:
            return None
    return authenticate_api_key(api_key, settings) if api_key else None
