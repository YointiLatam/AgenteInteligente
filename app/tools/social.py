from pydantic_settings import BaseSettings, SettingsConfigDict

from app.tools.base import ToolError


class SocialSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    meta_page_id: str | None = None


def require_page_id() -> str:
    page_id = SocialSettings().meta_page_id
    if not page_id:
        raise ToolError("not_configured", "Falta META_PAGE_ID en el entorno")
    return page_id
