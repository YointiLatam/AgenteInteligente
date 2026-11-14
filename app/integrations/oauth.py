"""Centralized OAuth2 token management ("Tool Hub") for Meta, WhatsApp and X.

One place returns a valid access token per provider and renews it automatically. It is opt-in:
`build_client(..., hub=...)` uses it; without a hub, clients keep using the static token.

Tokens and client secrets are never logged and never included in error messages.
"""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass, replace
from typing import Protocol

import httpx

from app.integrations.clients import Provider
from app.integrations.settings import IntegrationsSettings, get_integrations_settings
from app.tools.base import ToolError

logger = logging.getLogger(__name__)

X_TOKEN_URL = "https://api.x.com/2/oauth2/token"
DEFAULT_SKEW_SECONDS = 60.0
_REFRESH_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None  # epoch seconds; None means "no known expiry"

    def __repr__(self) -> str:  # never print secrets
        return f"TokenSet(expires_at={self.expires_at!r})"


class TokenStore(Protocol):
    async def get(self, provider: Provider) -> TokenSet | None: ...

    async def set(self, provider: Provider, tokens: TokenSet) -> None: ...


class InMemoryTokenStore:
    """Default store. Rotated tokens are lost on restart (see README)."""

    def __init__(self) -> None:
        self._tokens: dict[Provider, TokenSet] = {}

    async def get(self, provider: Provider) -> TokenSet | None:
        return self._tokens.get(provider)

    async def set(self, provider: Provider, tokens: TokenSet) -> None:
        self._tokens[provider] = tokens


def _secret(value) -> str | None:
    return value.get_secret_value() if value else None


class TokenHub:
    def __init__(
        self,
        settings: IntegrationsSettings,
        *,
        store: TokenStore | None = None,
        clock: Callable[[], float] = time.time,
        transport: httpx.AsyncBaseTransport | None = None,
        skew_seconds: float = DEFAULT_SKEW_SECONDS,
    ) -> None:
        self._settings = settings
        self._store: TokenStore = store or InMemoryTokenStore()
        self._clock = clock
        self._transport = transport
        self._skew = skew_seconds
        self._locks: dict[Provider, asyncio.Lock] = {}
        # Renewed tokens that could not be written to the store yet (see `_persist`).
        self._unsaved: dict[Provider, TokenSet] = {}

    # ---- configuration ------------------------------------------------------------------

    def _seed(self, provider: Provider) -> TokenSet | None:
        s = self._settings
        match provider:
            case Provider.META:
                access, refresh, expires = _secret(s.meta_access_token), None, None
            case Provider.WHATSAPP:
                access = _secret(s.whatsapp_access_token) if s.whatsapp_phone_number_id else None
                refresh, expires = None, None
            case Provider.X:
                access = _secret(s.x_bearer_token)
                refresh, expires = _secret(s.x_refresh_token), s.x_access_token_expires_at
        if access:
            return TokenSet(access, refresh, expires)
        if refresh and self._can_refresh_x():
            return TokenSet("", refresh, 0.0)  # no access token yet: refresh on first use
        return None

    def _can_refresh_x(self) -> bool:
        s = self._settings
        return bool(s.x_client_id and s.x_client_secret)

    def _can_refresh(self, provider: Provider, tokens: TokenSet) -> bool:
        if provider is Provider.X:
            return bool(tokens.refresh_token) and self._can_refresh_x()
        return bool(self._settings.meta_app_id and self._settings.meta_app_secret) and bool(
            tokens.access_token
        )

    def is_configured(self, provider: Provider) -> bool:
        return self._seed(provider) is not None

    # ---- public API ---------------------------------------------------------------------

    async def get_token(
        self, provider: Provider, *, force: bool = False, rejected: str | None = None
    ) -> str:
        """Return a valid access token, refreshing it when needed.

        `force` refreshes even when the token looks valid (the server rejected it). `rejected`
        is the token that failed: if another caller already replaced it, no new refresh occurs.
        """
        async with self._lock(provider):
            tokens = await self._current(provider)
            stale = self._is_stale(tokens)
            if force and (rejected is None or rejected == tokens.access_token):
                stale = True
            if stale and self._can_refresh(provider, tokens):
                async with self._store_lock(provider):
                    # Another process may have renewed (and rotated) the tokens while we waited.
                    latest = await self._load(provider) or tokens
                    if latest.access_token != tokens.access_token and not self._is_stale(latest):
                        return latest.access_token
                    tokens = await self._refresh(provider, latest)
            elif not tokens.access_token:
                raise ToolError("not_configured", f"Faltan credenciales para '{provider.value}'")
            return tokens.access_token

    # ---- internals ----------------------------------------------------------------------

    def _lock(self, provider: Provider) -> asyncio.Lock:
        return self._locks.setdefault(provider, asyncio.Lock())

    def _store_lock(self, provider: Provider) -> AbstractAsyncContextManager[None]:
        """Cross-process lock when the store offers one (PostgreSQL); a no-op otherwise."""
        lock = getattr(self._store, "lock", None)
        return lock(provider) if lock is not None else nullcontext()

    async def _current(self, provider: Provider) -> TokenSet:
        tokens = await self._load(provider)
        if tokens is None:
            tokens = self._seed(provider)
            if tokens is None:
                raise ToolError("not_configured", f"Faltan credenciales para '{provider.value}'")
            await self._try_save(provider, tokens, attempts=1)  # the seed comes from the env
        return tokens

    async def _load(self, provider: Provider) -> TokenSet | None:
        unsaved = self._unsaved.get(provider)
        if unsaved is not None:
            if await self._try_save(provider, unsaved):
                del self._unsaved[provider]
            return unsaved
        try:
            return await self._store.get(provider)
        except Exception:
            logger.warning("Token store unavailable while reading %s tokens", provider.value)
            raise ToolError(
                "upstream_error", "No se pudo leer el almacén de tokens", retryable=True
            ) from None

    async def _try_save(self, provider: Provider, tokens: TokenSet, attempts: int = 3) -> bool:
        for _ in range(attempts):
            try:
                await self._store.set(provider, tokens)
                return True
            except Exception:
                continue
        logger.warning("Token store unavailable while saving %s tokens", provider.value)
        return False

    async def _persist(self, provider: Provider, tokens: TokenSet) -> None:
        """Save renewed tokens. If the store is down, keep them in memory and retry later.

        X has already consumed the old refresh token at this point: losing the new one would
        break the integration until someone seeds a fresh token.
        """
        if not await self._try_save(provider, tokens):
            self._unsaved[provider] = tokens

    def _is_stale(self, tokens: TokenSet) -> bool:
        if not tokens.access_token:
            return True
        return tokens.expires_at is not None and tokens.expires_at - self._skew <= self._clock()

    async def _refresh(self, provider: Provider, tokens: TokenSet) -> TokenSet:
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=_REFRESH_TIMEOUT
            ) as client:
                if provider is Provider.X:
                    payload = await self._refresh_x(client, tokens)
                else:
                    payload = await self._refresh_meta(client, tokens)
            new_access = payload["access_token"]
            if not isinstance(new_access, str) or not new_access:
                raise KeyError("access_token")
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Token refresh rejected for %s (HTTP %s)", provider.value, exc.response.status_code
            )
            raise ToolError(
                "upstream_error",
                f"No se pudo renovar el token de '{provider.value}'",
                retryable=exc.response.status_code >= 500,
            ) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            logger.warning("Token refresh failed for %s", provider.value)
            raise ToolError(
                "upstream_error", f"No se pudo renovar el token de '{provider.value}'", True
            ) from None
        expires_in = payload.get("expires_in")
        expires_at = self._clock() + float(expires_in) if expires_in else None
        # X rotates refresh tokens: always keep the newest one, never drop it.
        refresh = payload.get("refresh_token") or tokens.refresh_token
        renewed = replace(
            tokens, access_token=new_access, refresh_token=refresh, expires_at=expires_at
        )
        await self._persist(provider, renewed)
        return renewed

    async def _refresh_x(self, client: httpx.AsyncClient, tokens: TokenSet) -> dict:
        s = self._settings
        auth = httpx.BasicAuth(s.x_client_id or "", _secret(s.x_client_secret) or "")
        response = await client.post(
            X_TOKEN_URL,
            auth=auth,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token or "",
                "client_id": s.x_client_id or "",
            },
        )
        response.raise_for_status()
        return response.json()

    async def _refresh_meta(self, client: httpx.AsyncClient, tokens: TokenSet) -> dict:
        s = self._settings
        response = await client.get(
            f"https://graph.facebook.com/{s.meta_graph_version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": s.meta_app_id or "",
                "client_secret": _secret(s.meta_app_secret) or "",
                "fb_exchange_token": tokens.access_token,
            },
        )
        response.raise_for_status()
        return response.json()


class HubAuth(httpx.Auth):
    """Injects the hub's bearer token; on one 401, forces a refresh and retries once."""

    def __init__(self, hub: TokenHub, provider: Provider) -> None:
        self._hub, self._provider = hub, provider

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        token = await self._hub.get_token(self._provider)
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            fresh = await self._hub.get_token(self._provider, force=True, rejected=token)
            if fresh != token:
                request.headers["Authorization"] = f"Bearer {fresh}"
                yield request


_hub: TokenHub | None = None


def get_token_hub() -> TokenHub:
    """Lazy process-wide hub built from the environment settings."""
    global _hub
    if _hub is None:
        settings = get_integrations_settings()
        _hub = TokenHub(settings, store=_default_store(settings))
    return _hub


def _default_store(settings: IntegrationsSettings) -> TokenStore | None:
    """Encrypted PostgreSQL store when configured; otherwise in memory (rotated tokens are lost)."""
    from app.integrations.token_store import build_pg_token_store

    store = build_pg_token_store(settings)
    if store is None and settings.x_client_id and settings.x_refresh_token:
        logger.warning(
            "Tokens OAuth de X en memoria: el refresh token rotado se pierde al reiniciar. "
            "Define TOKEN_ENCRYPTION_KEY y DATABASE_URL para guardarlos cifrados."
        )
    return store


def set_token_hub(hub: TokenHub | None) -> None:
    """Override (or reset with None) the singleton; meant for tests and custom stores."""
    global _hub
    _hub = hub
