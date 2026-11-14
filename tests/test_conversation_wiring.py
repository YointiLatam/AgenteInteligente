import importlib
import sys
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, HTTPException

from app.conversations import wiring
from app.conversations.repository import ConversationPersistenceError
from app.conversations.service import ConversationService


def test_import_has_no_side_effects(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("must not connect at import time")

    monkeypatch.setattr("psycopg.connect", boom)
    sys.modules.pop("app.conversations.wiring", None)
    importlib.import_module("app.conversations.wiring")


def test_missing_database_url_raises_service_not_configured(monkeypatch):
    @contextmanager
    def missing():
        raise ConversationPersistenceError("Falta DATABASE_URL")
        yield

    monkeypatch.setattr(wiring, "postgres_conversation_repository", missing)
    with pytest.raises(wiring.ServiceNotConfigured), wiring.open_conversation_service():
        pass


def test_dependency_maps_not_configured_to_503(monkeypatch):
    @contextmanager
    def missing():
        raise wiring.ServiceNotConfigured("x")
        yield

    monkeypatch.setattr(wiring, "open_conversation_service", missing)
    generator = wiring.get_conversation_service()
    with pytest.raises(HTTPException) as info:
        next(generator)
    assert info.value.status_code == 503


def test_builds_service_from_pieces_and_closes_them(monkeypatch):
    closed = []

    @contextmanager
    def fake(name):
        try:
            yield object()
        finally:
            closed.append(name)

    monkeypatch.setattr(wiring, "postgres_conversation_repository", lambda: fake("repo"))
    monkeypatch.setattr(wiring, "postgres_checkpointer", lambda: fake("checkpointer"))
    monkeypatch.setattr(wiring, "GeminiClient", lambda settings: object())
    monkeypatch.setattr(wiring, "get_integrations_settings", lambda: object())
    with wiring.open_conversation_service() as service:
        assert isinstance(service, ConversationService)
        assert closed == []
    assert sorted(closed) == ["checkpointer", "repo"]


def test_dependency_is_overridable():
    app = FastAPI()
    sentinel = object()
    app.dependency_overrides[wiring.get_conversation_service] = lambda: sentinel
    assert app.dependency_overrides[wiring.get_conversation_service]() is sentinel
