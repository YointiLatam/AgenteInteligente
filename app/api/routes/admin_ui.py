"""Static admin panel (login + HITL inbox + history + audit). The API enforces auth, not this."""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

router = APIRouter(prefix="/admin/ui", tags=["panel web"])

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "admin" / "static"
ASSET_BASE = "/admin/ui/assets"

# Explicit allow-list: only these names are ever served, so there is no path traversal surface.
ASSETS: dict[str, str] = {
    "admin.js": "text/javascript; charset=utf-8",
    "api.js": "text/javascript; charset=utf-8",
    "render.js": "text/javascript; charset=utf-8",
    "state.js": "text/javascript; charset=utf-8",
    "admin.css": "text/css; charset=utf-8",
}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@router.get("")
def index() -> FileResponse:
    headers = {**SECURITY_HEADERS, "Cache-Control": "no-store"}
    return FileResponse(
        STATIC_DIR / "index.html", media_type="text/html; charset=utf-8", headers=headers
    )


@router.get("/assets/{name}", include_in_schema=False)
def asset(name: str, response: Response) -> FileResponse:
    media_type = ASSETS.get(name)
    if media_type is None:
        raise HTTPException(404, "No encontrado")
    # Assets change with deployments and the panel is tiny: always revalidate.
    headers = {**SECURITY_HEADERS, "Cache-Control": "no-cache"}
    return FileResponse(STATIC_DIR / name, media_type=media_type, headers=headers)
