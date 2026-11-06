"""Recuperacion vectorial de chunks almacenados en PostgreSQL."""

from __future__ import annotations

import psycopg

from app.agent.nodes.retrieval import Retriever
from app.agent.state import RetrievedDocument
from app.knowledge.ingestion import GeminiEmbedder, IngestionConfigurationError
from app.knowledge.settings import KnowledgeSettings, get_knowledge_settings


class PostgresVectorRetriever:
    """Busca chunks por distancia de coseno sin abrir conexiones al inicializarse."""

    def __init__(self, settings: KnowledgeSettings) -> None:
        if settings.database_url is None:
            raise IngestionConfigurationError("Falta DATABASE_URL para la recuperacion")
        if settings.knowledge_schema != "inmobiliaria_pruebas":
            raise IngestionConfigurationError("El recuperador esta limitado al esquema verificado")
        self._database_url = settings.database_url.get_secret_value()
        self._schema = settings.knowledge_schema
        self._top_k = settings.retrieval_top_k
        self._embedder = GeminiEmbedder(settings)

    def __call__(self, query: str) -> list[RetrievedDocument]:
        vector = self._embedder.embed_query(query)
        serialized_vector = "[" + ",".join(str(value) for value in vector) + "]"
        statement = f"""
            SELECT source_ref, chunk_index, content_chunk,
                   1 - (embedding <=> %s::vector) AS score
            FROM {self._schema}.knowledge_embeddings
            WHERE modelo_embedding = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        with psycopg.connect(self._database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    statement,
                    (serialized_vector, self._embedder.model, serialized_vector, self._top_k),
                )
                rows = cursor.fetchall()
        return [
            {
                "id": f"{source_ref}:{chunk_index}",
                "content": content_chunk,
                "score": float(score),
            }
            for source_ref, chunk_index, content_chunk, score in rows
        ]


def build_configured_retriever(
    settings: KnowledgeSettings | None = None,
) -> Retriever:
    """Devuelve el recuperador real solo cuando toda la configuracion esta disponible."""
    configured = settings or get_knowledge_settings()
    if configured.database_url is None or configured.gemini_api_key is None:
        return unavailable_retriever
    try:
        return PostgresVectorRetriever(configured)
    except IngestionConfigurationError:
        return unavailable_retriever


def unavailable_retriever(_: str) -> list[RetrievedDocument]:
    """Propaga la falta de configuracion al manejo controlado del nodo RAG."""
    raise IngestionConfigurationError("La recuperacion no esta configurada")


def configured_retriever(query: str) -> list[RetrievedDocument]:
    """Resuelve configuracion solo al recorrer la rama RAG del grafo."""
    return build_configured_retriever()(query)
