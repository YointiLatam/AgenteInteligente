"""Encrypted PostgreSQL store for OAuth tokens.

X rotates the refresh token on every renewal: if the newest one is lost, the integration stops
working until someone seeds a new one. This store keeps the rotated tokens across restarts and
deployments. Tokens are encrypted with Fernet before they reach the database; the key
(`TOKEN_ENCRYPTION_KEY`) is never stored in it. Errors never include token or key material.
"""

import asyncio
import hashlib
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import psycopg
from cryptography.fernet import Fernet, InvalidToken

from app.integrations.clients import Provider
from app.integrations.oauth import TokenSet
from app.integrations.settings import IntegrationsSettings
from app.knowledge.settings import get_knowledge_settings

logger = logging.getLogger(__name__)

_SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_LOCK_TIMEOUT = "15s"


class TokenStoreError(RuntimeError):
    """Generic failure of the token store (the message is safe to log)."""


class TokenCipher:
    """Symmetric encryption for token values (Fernet: AES-128-CBC + HMAC-SHA256)."""

    def __init__(self, key: str | bytes) -> None:
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError):
            raise TokenStoreError(
                "TOKEN_ENCRYPTION_KEY no es válida: debe ser una clave Fernet de 32 bytes en base64"
            ) from None

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            raise TokenStoreError(
                "No se pudo descifrar un token guardado: ¿cambió TOKEN_ENCRYPTION_KEY?"
            ) from None


def _advisory_key(provider: Provider) -> int:
    digest = hashlib.sha256(f"oauth:{provider.value}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class PgTokenStore:
    """`TokenStore` backed by the `oauth_tokens` table (see migrations/20261113_oauth_tokens.sql).

    The service is async but psycopg is synchronous: every call runs in a worker thread with a
    short-lived connection. `lock()` serializes a refresh across processes with an advisory lock
    so two instances never spend the same rotated refresh token.
    """

    _SELECT = """
        SELECT access_token_enc, refresh_token_enc, expires_at
        FROM oauth_tokens WHERE provider = %s
    """
    _UPSERT = """
        INSERT INTO oauth_tokens (provider, access_token_enc, refresh_token_enc, expires_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (provider) DO UPDATE SET
            access_token_enc = EXCLUDED.access_token_enc,
            refresh_token_enc = EXCLUDED.refresh_token_enc,
            expires_at = EXCLUDED.expires_at,
            updated_at = now()
    """

    def __init__(
        self,
        dsn: str,
        cipher: TokenCipher,
        *,
        schema: str = "inmobiliaria_pruebas",
        connect: Callable[..., Any] = psycopg.connect,
    ) -> None:
        if not _SCHEMA_PATTERN.match(schema):
            raise TokenStoreError("Esquema de base de datos no válido para los tokens")
        self._dsn, self._cipher, self._schema, self._connect = dsn, cipher, schema, connect

    def _open(self) -> Any:
        try:
            return self._connect(
                self._dsn, autocommit=True, options=f"-c search_path={self._schema},public"
            )
        except psycopg.Error:
            raise TokenStoreError("No fue posible conectar con el almacén de tokens") from None

    # ---- TokenStore protocol ------------------------------------------------------------

    async def get(self, provider: Provider) -> TokenSet | None:
        return await asyncio.to_thread(self._get_sync, provider)

    async def set(self, provider: Provider, tokens: TokenSet) -> None:
        await asyncio.to_thread(self._set_sync, provider, tokens)

    # ---- cross-process lock -------------------------------------------------------------

    @asynccontextmanager
    async def lock(self, provider: Provider) -> AsyncIterator[None]:
        connection = await asyncio.to_thread(self._acquire, provider)
        try:
            yield
        finally:
            await asyncio.to_thread(self._release, connection, provider)

    # ---- synchronous internals ----------------------------------------------------------

    def _get_sync(self, provider: Provider) -> TokenSet | None:
        try:
            with self._open() as connection:
                row = connection.execute(self._SELECT, (provider.value,)).fetchone()
        except psycopg.Error:
            raise TokenStoreError("No fue posible leer los tokens guardados") from None
        if row is None:
            return None
        access_enc, refresh_enc, expires_at = row
        return TokenSet(
            access_token=self._cipher.decrypt(access_enc),
            refresh_token=self._cipher.decrypt(refresh_enc) if refresh_enc else None,
            expires_at=expires_at.timestamp() if expires_at is not None else None,
        )

    def _set_sync(self, provider: Provider, tokens: TokenSet) -> None:
        params = (
            provider.value,
            self._cipher.encrypt(tokens.access_token),
            self._cipher.encrypt(tokens.refresh_token) if tokens.refresh_token else None,
            datetime.fromtimestamp(tokens.expires_at, UTC)
            if tokens.expires_at is not None
            else None,
        )
        try:
            with self._open() as connection:
                connection.execute(self._UPSERT, params)
        except psycopg.Error:
            raise TokenStoreError("No fue posible guardar los tokens") from None

    def _acquire(self, provider: Provider) -> Any:
        connection = self._open()
        try:
            connection.execute(f"SET lock_timeout = '{_LOCK_TIMEOUT}'")
            connection.execute("SELECT pg_advisory_lock(%s)", (_advisory_key(provider),))
        except psycopg.Error:
            connection.close()
            raise TokenStoreError("No fue posible obtener el bloqueo de renovación") from None
        return connection

    def _release(self, connection: Any, provider: Provider) -> None:
        try:
            connection.execute("SELECT pg_advisory_unlock(%s)", (_advisory_key(provider),))
        except psycopg.Error:
            logger.warning("No se pudo liberar el bloqueo de renovación de %s", provider.value)
        finally:
            connection.close()


def build_pg_token_store(settings: IntegrationsSettings) -> PgTokenStore | None:
    """Return the encrypted store when `TOKEN_ENCRYPTION_KEY` and `DATABASE_URL` are set."""
    if not settings.token_encryption_key:
        return None
    knowledge = get_knowledge_settings()
    if not knowledge.database_url:  # unset or empty
        logger.warning("TOKEN_ENCRYPTION_KEY definida pero falta DATABASE_URL: tokens en memoria")
        return None
    cipher = TokenCipher(settings.token_encryption_key.get_secret_value())
    return PgTokenStore(
        knowledge.database_url.get_secret_value(), cipher, schema=knowledge.knowledge_schema
    )
