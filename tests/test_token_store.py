from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import psycopg
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.integrations import oauth
from app.integrations.clients import Provider
from app.integrations.oauth import InMemoryTokenStore, TokenHub, TokenSet
from app.integrations.settings import IntegrationsSettings
from app.integrations.token_store import (
    PgTokenStore,
    TokenCipher,
    TokenStoreError,
    build_pg_token_store,
)
from app.tools.base import ToolError

KEY = Fernet.generate_key().decode()
ACCESS, REFRESH = "access-secret-value", "refresh-secret-value"


class FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, store: "FakeDb"):
        self._db = store
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.closed = True

    def execute(self, sql, params=None):
        self._db.calls.append((" ".join(sql.split()), params))
        if self._db.error:
            raise self._db.error
        if "FROM oauth_tokens" in sql:
            return FakeResult(self._db.rows.get(params[0]))
        if "INSERT INTO oauth_tokens" in sql:
            provider, access, refresh, expires = params
            self._db.rows[provider] = (access, refresh, expires)
        return FakeResult(None)


class FakeDb:
    def __init__(self):
        self.rows, self.calls, self.error, self.options = {}, [], None, []

    def connect(self, dsn, **kwargs):
        self.options.append(kwargs.get("options"))
        return FakeConnection(self)


def _store(db: FakeDb, key: str = KEY, schema: str = "inmobiliaria_pruebas") -> PgTokenStore:
    return PgTokenStore("postgresql://fake", TokenCipher(key), schema=schema, connect=db.connect)


def test_cipher_round_trip_and_ciphertext_hides_the_value():
    cipher = TokenCipher(KEY)

    encrypted = cipher.encrypt(ACCESS)

    assert ACCESS not in encrypted
    assert cipher.decrypt(encrypted) == ACCESS


def test_cipher_rejects_an_invalid_key_without_echoing_it():
    with pytest.raises(TokenStoreError) as error:
        TokenCipher("not-a-fernet-key")

    assert "not-a-fernet-key" not in str(error.value)


def test_cipher_with_the_wrong_key_fails_with_a_generic_message():
    encrypted = TokenCipher(KEY).encrypt(ACCESS)
    other = TokenCipher(Fernet.generate_key().decode())

    with pytest.raises(TokenStoreError) as error:
        other.decrypt(encrypted)

    assert ACCESS not in str(error.value) and encrypted not in str(error.value)


@pytest.mark.anyio
async def test_set_stores_only_encrypted_values_and_get_decrypts_them():
    db = FakeDb()
    store = _store(db)
    expires_at = datetime(2026, 11, 13, 12, 0, tzinfo=UTC).timestamp()

    await store.set(Provider.X, TokenSet(ACCESS, REFRESH, expires_at))

    _, params = db.calls[-1]
    assert ACCESS not in params and REFRESH not in params
    assert all(ACCESS not in str(p) and REFRESH not in str(p) for p in params)
    db.rows["x"] = (params[1], params[2], datetime.fromtimestamp(expires_at, UTC))
    loaded = await store.get(Provider.X)
    assert (loaded.access_token, loaded.refresh_token, loaded.expires_at) == (
        ACCESS,
        REFRESH,
        expires_at,
    )


@pytest.mark.anyio
async def test_get_returns_none_when_there_is_no_row():
    assert await _store(FakeDb()).get(Provider.X) is None


@pytest.mark.anyio
async def test_token_without_refresh_or_expiry_is_supported():
    db = FakeDb()
    store = _store(db)

    await store.set(Provider.META, TokenSet(ACCESS))
    _, params = db.calls[-1]
    db.rows["meta"] = (params[1], params[2], params[3])

    loaded = await store.get(Provider.META)
    assert (loaded.refresh_token, loaded.expires_at) == (None, None)


@pytest.mark.anyio
async def test_database_errors_are_wrapped_without_details():
    db = FakeDb()
    db.error = psycopg.OperationalError("password authentication failed for user secretuser")
    store = _store(db)

    with pytest.raises(TokenStoreError) as error:
        await store.get(Provider.X)

    assert "secretuser" not in str(error.value)
    assert error.value.__cause__ is None


def test_schema_name_is_validated_and_used_in_the_search_path():
    db = FakeDb()
    _store(db, schema="inmobiliaria_pruebas")._open()
    assert db.options == ["-c search_path=inmobiliaria_pruebas,public"]

    with pytest.raises(TokenStoreError):
        _store(db, schema="x; DROP TABLE oauth_tokens")


@pytest.mark.anyio
async def test_lock_takes_and_releases_an_advisory_lock():
    db = FakeDb()
    store = _store(db)

    async with store.lock(Provider.X):
        assert any("pg_advisory_lock" in sql for sql, _ in db.calls)
        assert not any("pg_advisory_unlock" in sql for sql, _ in db.calls)

    assert any("pg_advisory_unlock" in sql for sql, _ in db.calls)


@pytest.mark.anyio
async def test_lock_failure_is_reported_as_a_store_error():
    db = FakeDb()
    db.error = psycopg.errors.LockNotAvailable("timeout")

    with pytest.raises(TokenStoreError):
        async with _store(db).lock(Provider.X):
            pass


# ---- hub integration -----------------------------------------------------------------


def _settings(**overrides) -> IntegrationsSettings:
    base = {
        "_env_file": None,
        "x_client_id": "client",
        "x_client_secret": "secret",
        "x_refresh_token": "seed-refresh",
    }
    return IntegrationsSettings(**{**base, **overrides})


@pytest.mark.anyio
async def test_hub_uses_the_tokens_another_process_already_refreshed():
    rotated = TokenSet("fresh-by-other-process", "rotated-refresh", 10_000.0)

    class OtherProcessStore(InMemoryTokenStore):
        @asynccontextmanager
        async def lock(self, provider):
            self._tokens[provider] = rotated  # the other process finished while we waited
            yield

    def refuse(request: httpx.Request) -> httpx.Response:  # a second refresh would burn the token
        raise AssertionError("must not refresh again")

    hub = TokenHub(
        _settings(),
        store=OtherProcessStore(),
        clock=lambda: 1_000.0,
        transport=httpx.MockTransport(refuse),
    )

    assert await hub.get_token(Provider.X) == "fresh-by-other-process"


def test_build_pg_token_store_needs_both_key_and_database(monkeypatch):
    from app.knowledge.settings import KnowledgeSettings

    monkeypatch.setattr(
        "app.integrations.token_store.get_knowledge_settings",
        lambda: KnowledgeSettings(_env_file=None, database_url=None),
    )
    assert build_pg_token_store(_settings()) is None
    assert build_pg_token_store(_settings(token_encryption_key=SecretStr(KEY))) is None

    monkeypatch.setattr(
        "app.integrations.token_store.get_knowledge_settings",
        lambda: KnowledgeSettings(_env_file=None, database_url=SecretStr("postgresql://x")),
    )
    assert build_pg_token_store(_settings()) is None
    assert isinstance(
        build_pg_token_store(_settings(token_encryption_key=SecretStr(KEY))), PgTokenStore
    )


def test_default_store_is_memory_without_encryption_key(monkeypatch, caplog):
    monkeypatch.setattr("app.integrations.token_store.build_pg_token_store", lambda s: None)

    with caplog.at_level("WARNING"):
        assert oauth._default_store(_settings()) is None

    assert "en memoria" in caplog.text
    assert "seed-refresh" not in caplog.text


# ---- regression: the rotated refresh token must never be lost ----------------------------


class FlakyStore(InMemoryTokenStore):
    def __init__(self, failures: int):
        super().__init__()
        self.failures = failures

    async def set(self, provider, tokens):
        if self.failures > 0:
            self.failures -= 1
            raise TokenStoreError("store down")
        await super().set(provider, tokens)


@pytest.mark.anyio
async def test_rotated_refresh_token_survives_a_store_outage():
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        n = len(bodies)
        return httpx.Response(
            200,
            json={"access_token": f"A{n + 1}", "refresh_token": f"R{n + 1}", "expires_in": 3600},
        )

    now = [1_000.0]
    store = FlakyStore(failures=4)  # the seed write and all 3 attempts after the first refresh
    hub = TokenHub(
        _settings(x_refresh_token="R1"),
        store=store,
        clock=lambda: now[0],
        transport=httpx.MockTransport(handler),
    )

    assert await hub.get_token(Provider.X) == "A2"  # refresh #1 consumed R1; the store was down
    assert await hub.get_token(Provider.X) == "A2"  # served from memory, no second refresh
    assert len(bodies) == 1
    saved = await store.get(Provider.X)  # the store recovered: the pending tokens were written
    assert saved is not None and saved.refresh_token == "R2"

    now[0] = 10_000.0  # A2 expired: the next refresh must spend R2, not the spent R1
    assert await hub.get_token(Provider.X) == "A3"
    assert "refresh_token=R2" in bodies[1] and "refresh_token=R1" not in bodies[1]


@pytest.mark.anyio
async def test_unreadable_store_surfaces_as_a_tool_error_without_details():
    class BrokenStore(InMemoryTokenStore):
        async def get(self, provider):
            raise TokenStoreError("password authentication failed for user secretuser")

    hub = TokenHub(_settings(), store=BrokenStore(), clock=lambda: 1_000.0)

    with pytest.raises(ToolError) as error:
        await hub.get_token(Provider.X)

    assert error.value.code == "upstream_error" and "secretuser" not in str(error.value)


def test_empty_database_url_is_treated_as_unset(monkeypatch):
    from app.knowledge.settings import KnowledgeSettings

    monkeypatch.setattr(
        "app.integrations.token_store.get_knowledge_settings",
        lambda: KnowledgeSettings(_env_file=None, database_url=SecretStr("")),
    )

    assert build_pg_token_store(_settings(token_encryption_key=SecretStr(KEY))) is None


@pytest.mark.anyio
async def test_an_expiry_of_zero_is_stored_not_dropped():
    db = FakeDb()

    await _store(db).set(Provider.X, TokenSet(ACCESS, REFRESH, 0.0))

    assert db.calls[-1][1][3] == datetime.fromtimestamp(0.0, UTC)
