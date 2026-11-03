import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import jwt

from app.auth.settings import AuthSettings


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def authenticate_api_key(api_key: str, settings: AuthSettings) -> str | None:
    """Devuelve el id del cliente dueño de la API key, o None si no es válida."""
    candidate = hash_api_key(api_key)
    client_id = None
    # Se recorren todas las entradas para no revelar por tiempo cuál coincidió.
    for known_client, known_hash in settings.api_keys.items():
        if hmac.compare_digest(candidate, known_hash):
            client_id = known_client
    return client_id


def create_access_token(client_id: str, settings: AuthSettings) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": client_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), settings.jwt_algorithm)


def decode_access_token(token: str, settings: AuthSettings) -> str:
    """Valida firma y expiración; lanza jwt.InvalidTokenError si el token no sirve."""
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "exp"]},
    )
    return claims["sub"]
