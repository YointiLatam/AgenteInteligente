"""Configuración del pipeline de conocimiento."""

from functools import lru_cache
from math import isfinite
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    retrieval_mode: Literal["semantic", "hybrid"] = "semantic"
    fts_language: Literal["spanish"] = "spanish"
    hybrid_candidate_k: int = Field(default=20, ge=1, le=100)
    hybrid_rrf_k: int = Field(default=60, ge=1, le=10_000)
    hybrid_rerank_enabled: bool = False
    hybrid_rrf_weight: float = Field(default=0.40, ge=0)
    hybrid_semantic_weight: float = Field(default=0.30, ge=0)
    hybrid_lexical_weight: float = Field(default=0.30, ge=0)

    @model_validator(mode="after")
    def validate_hybrid_settings(self) -> "KnowledgeSettings":
        """Reject settings that cannot produce a deterministic hybrid ranking."""
        weights = (
            self.hybrid_rrf_weight,
            self.hybrid_semantic_weight,
            self.hybrid_lexical_weight,
        )
        if not all(isfinite(weight) for weight in weights):
            raise ValueError("Los pesos híbridos deben ser números finitos")
        if sum(weights) <= 0:
            raise ValueError("La suma de los pesos híbridos debe ser positiva")
        if self.hybrid_candidate_k < self.retrieval_top_k:
            raise ValueError("hybrid_candidate_k debe ser mayor o igual que retrieval_top_k")
        return self


@lru_cache
def get_knowledge_settings() -> KnowledgeSettings:
    return KnowledgeSettings()
