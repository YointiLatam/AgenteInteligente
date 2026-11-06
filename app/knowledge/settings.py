"""Configuración del pipeline de conocimiento."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class KnowledgeSettings(BaseSettings):
    """Valores necesarios para vectorizar el contenido local."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: SecretStr | None = None
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    database_url: SecretStr | None = None
    knowledge_schema: str = "inmobiliaria_pruebas"
    chunk_size: int = 800
    chunk_overlap: int = 120
    retrieval_top_k: int = Field(default=4, ge=1, le=10)


@lru_cache
def get_knowledge_settings() -> KnowledgeSettings:
    return KnowledgeSettings()
