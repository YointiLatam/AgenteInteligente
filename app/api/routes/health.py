from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["salud"])


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}
