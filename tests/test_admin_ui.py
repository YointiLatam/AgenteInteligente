"""Static admin panel: assets, security headers, allow-list and static code hygiene."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes.admin_ui import ASSETS, SECURITY_HEADERS, STATIC_DIR
from app.main import create_app

client = TestClient(create_app())

FORBIDDEN_JS = [
    r"\.innerHTML\b",
    r"\.outerHTML\b",
    r"insertAdjacentHTML",
    r"document\.write",
    r"\beval\s*\(",
    r"new\s+Function\s*\(",
    r"localStorage",
    r"sessionStorage",
    r"document\.cookie",
    r"indexedDB",
]


def _assert_security_headers(response) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_index_served_with_security_headers() -> None:
    response = client.get("/admin/ui")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    _assert_security_headers(response)
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp


@pytest.mark.parametrize("name,media_type", sorted(ASSETS.items()))
def test_assets_served_with_media_type(name: str, media_type: str) -> None:
    response = client.get(f"/admin/ui/assets/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    _assert_security_headers(response)


@pytest.mark.parametrize(
    "name",
    [
        "..%2fadmin.py",
        "..%2f..%2f..%2f.env",
        "%2e%2e%2fadmin.js",
        "index.html",
        "unknown.js",
        "admin.js.map",
        "ADMIN.JS",
        "admin.js%00",
    ],
)
def test_assets_reject_traversal_and_unknown(name: str) -> None:
    assert client.get(f"/admin/ui/assets/{name}").status_code == 404


def test_shell_needs_no_auth_and_api_still_does(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "x" * 40)
    monkeypatch.setenv("AUTH_API_KEYS", "{}")
    assert client.get("/admin/ui").status_code == 200
    assert client.get("/admin/me").status_code == 401


def test_html_has_no_inline_script_or_style() -> None:
    html = (STATIC_DIR / "index.html").read_text()
    for match in re.finditer(r"<script\b([^>]*)>", html):
        assert "src=" in match.group(1), "inline <script> is not allowed"
    assert "<style" not in html
    assert not re.search(r"\sstyle\s*=", html)
    assert not re.search(r"\son[a-z]+\s*=", html)
    assert "http://" not in html and "https://" not in html


def test_every_referenced_asset_exists_and_is_allow_listed() -> None:
    html = (STATIC_DIR / "index.html").read_text()
    refs = re.findall(r'(?:src|href)="(/admin/ui/assets/[^"]+)"', html)
    assert refs
    for ref in refs:
        name = ref.rsplit("/", 1)[1]
        assert name in ASSETS and (STATIC_DIR / name).is_file()


def test_every_js_import_resolves_to_an_allowed_asset() -> None:
    for name in (n for n in ASSETS if n.endswith(".js")):
        source = (STATIC_DIR / name).read_text()
        for target in re.findall(r'from\s+"\./([^"]+)"', source):
            assert target in ASSETS and (STATIC_DIR / target).is_file()
    for name in ASSETS:
        assert (STATIC_DIR / name).is_file()


@pytest.mark.parametrize("name", sorted(n for n in ASSETS if n.endswith(".js")))
def test_js_has_no_dangerous_patterns(name: str) -> None:
    source = (STATIC_DIR / name).read_text()
    for pattern in FORBIDDEN_JS:
        assert not re.search(pattern, source), f"{name} uses forbidden pattern {pattern}"


def test_static_dir_only_contains_allow_listed_files() -> None:
    present = {p.name for p in Path(STATIC_DIR).iterdir() if p.is_file()}
    assert present == set(ASSETS) | {"index.html"}
