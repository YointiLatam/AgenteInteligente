"""Drift guard: every `### METHOD /path` heading in guias/api.md and guias/administracion.md
must be a real route."""

import re
from pathlib import Path

from fastapi.routing import APIWebSocketRoute

from app.main import create_app

GUIAS = Path(__file__).resolve().parent.parent / "guias"
GUIDES = [GUIAS / "api.md", GUIAS / "administracion.md"]
HEADING = re.compile(r"^### (GET|POST|PUT|PATCH|DELETE|WS) (/\S*)\s*$", re.MULTILINE)
PARAM = re.compile(r"\{[^}]+\}")


def _normalize(path: str) -> str:
    return PARAM.sub("{}", path)


def _app_routes() -> set[tuple[str, str]]:
    """HTTP routes come from the OpenAPI schema; WebSockets are not part of it, so they are
    read from each included router (their prefix is already part of `route.path`)."""
    app = create_app()
    routes: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        routes.update((method.upper(), _normalize(path)) for method in operations)
    for route in app.routes:
        router = getattr(route, "original_router", None)
        for inner in getattr(router, "routes", []):
            if isinstance(inner, APIWebSocketRoute):
                routes.add(("WS", _normalize(inner.path)))
    return routes


def _documented() -> list[tuple[str, str]]:
    return [
        (m.group(1), _normalize(m.group(2)))
        for guide in GUIDES
        for m in HEADING.finditer(guide.read_text())
    ]


def test_guide_documents_endpoints() -> None:
    assert len(_documented()) >= 17


def test_every_documented_endpoint_exists() -> None:
    routes = _app_routes()
    missing = [item for item in _documented() if item not in routes]
    assert not missing, f"guias document routes that do not exist: {missing}"
