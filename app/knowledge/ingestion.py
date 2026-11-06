"""Pipeline idempotente de chunking y embeddings para documentos locales."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Protocol

import psycopg
from google import genai
from google.genai import types
from psycopg.types.json import Jsonb

from app.knowledge.settings import KnowledgeSettings


class IngestionConfigurationError(RuntimeError):
    """La configuración no permite una ingesta segura."""


class ExistingEmbeddingModelMismatch(RuntimeError):
    """La tabla contiene vectores de otro modelo y no se deben mezclar."""


@dataclass(frozen=True)
class SourceDocument:
    """Texto estructurado disponible para un registro de ``documentos``."""

    id: int
    content: str


@dataclass(frozen=True)
class EmbeddingChunk:
    """Un fragmento vectorizado listo para persistirse."""

    document_id: int
    index: int
    content: str
    vector: list[float]


@dataclass(frozen=True)
class IngestionResult:
    documents: int
    chunks: int
    stored: int


class Embedder(Protocol):
    model: str
    dimensions: int

    def embed(self, text: str) -> list[float]: ...


class KnowledgeRepository(Protocol):
    def read_documents(self) -> list[SourceDocument]: ...

    def existing_models(self) -> set[str]: ...

    def upsert(self, chunk: EmbeddingChunk, model: str) -> None: ...


def chunk_text(text: str, *, size: int, overlap: int) -> list[str]:
    """Parte texto por palabras y conserva solapamiento sin cortar palabras."""
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("chunk_size debe ser positivo y chunk_overlap menor que chunk_size")
    normalized = " ".join(text.split())
    if not normalized:
        return []

    chunks: list[str] = []
    words = normalized.split(" ")
    start = 0
    while start < len(words):
        if len(words[start]) > size:
            step = size - overlap
            word_start = 0
            while word_start < len(words[start]):
                chunks.append(words[start][word_start : word_start + size])
                if word_start + size >= len(words[start]):
                    break
                word_start += step
            start += 1
            continue

        end = start
        chunk_length = 0
        while end < len(words):
            next_length = chunk_length + len(words[end]) + (1 if chunk_length else 0)
            if next_length > size:
                break
            chunk_length = next_length
            end += 1
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break

        next_start = end - 1
        overlap_length = len(words[next_start])
        while next_start > start:
            candidate_length = len(words[next_start - 1]) + 1 + overlap_length
            if candidate_length > overlap:
                break
            next_start -= 1
            overlap_length = candidate_length
        start = next_start if next_start > start else end
    return chunks


class GeminiEmbedder:
    """Adaptador de Gemini que exige vectores normalizados de dimensión conocida."""

    def __init__(self, settings: KnowledgeSettings) -> None:
        if settings.gemini_api_key is None:
            raise IngestionConfigurationError("Falta GEMINI_API_KEY para generar embeddings")
        if settings.embedding_dimensions != 768:
            raise IngestionConfigurationError(
                "La tabla knowledge_embeddings admite vector(768); configure 768 dimensiones"
            )
        self.model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

    def embed(self, text: str) -> list[float]:
        return self._embed(text, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        """Genera un vector de consulta compatible con los documentos ingeridos."""
        return self._embed(text, task_type="RETRIEVAL_QUERY")

    def _embed(self, text: str, *, task_type: str) -> list[float]:
        response = self._client.models.embed_content(
            model=self.model,
            contents=text,
            config=types.EmbedContentConfig(
                output_dimensionality=self.dimensions,
                task_type=task_type,
            ),
        )
        vector = [float(value) for value in response.embeddings[0].values]
        if len(vector) != self.dimensions:
            raise IngestionConfigurationError(
                f"Gemini devolvió {len(vector)} dimensiones; se requieren {self.dimensions}"
            )
        magnitude = sqrt(sum(value * value for value in vector))
        if magnitude == 0:
            raise IngestionConfigurationError("Gemini devolvió un embedding nulo")
        return [value / magnitude for value in vector]


class PostgresKnowledgeRepository:
    """Acceso a la tabla existente sin crear infraestructura."""

    _DOCUMENT_COLUMNS = (
        "cod_contribuyente",
        "cod_ficha",
        "partida_electronica",
        "zona_registral_numero",
        "zona_registral_sede",
        "file_ficha",
        "cod_luz",
        "file_luz",
        "cod_agua",
        "file_agua",
    )

    def __init__(self, database_url: str, schema: str) -> None:
        if schema != "inmobiliaria_pruebas":
            raise IngestionConfigurationError("El pipeline está limitado al esquema verificado")
        self._connection = psycopg.connect(database_url)
        self._schema = schema
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", (f"{schema}.knowledge_embeddings",))
            if cursor.fetchone()[0] is None:
                self._connection.close()
                raise IngestionConfigurationError(
                    "DATABASE_URL no apunta a la base con knowledge_embeddings"
                )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> PostgresKnowledgeRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read_documents(self) -> list[SourceDocument]:
        fields = ", ".join(self._DOCUMENT_COLUMNS)
        query = f"SELECT id, {fields} FROM {self._schema}.documentos ORDER BY id"
        with self._connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
        documents: list[SourceDocument] = []
        for row in rows:
            values = dict(zip(("id", *self._DOCUMENT_COLUMNS), row, strict=True))
            lines = [
                f"{column}: {values[column]}"
                for column in self._DOCUMENT_COLUMNS
                if values[column] not in (None, "")
            ]
            if lines:
                documents.append(SourceDocument(id=values["id"], content="\n".join(lines)))
        return documents

    def existing_models(self) -> set[str]:
        query = f"SELECT DISTINCT modelo_embedding FROM {self._schema}.knowledge_embeddings"
        with self._connection.cursor() as cursor:
            cursor.execute(query)
            return {row[0] for row in cursor.fetchall()}

    def upsert(self, chunk: EmbeddingChunk, model: str) -> None:
        vector = "[" + ",".join(str(value) for value in chunk.vector) + "]"
        query = f"""
            INSERT INTO {self._schema}.knowledge_embeddings
                (documento_id, source_type, source_ref, chunk_index, content_chunk,
                 embedding, modelo_embedding, metadata)
            VALUES (%s, 'documento', %s, %s, %s, %s::vector, %s, %s)
            ON CONFLICT (source_type, source_ref, chunk_index) DO UPDATE
            SET documento_id = EXCLUDED.documento_id,
                content_chunk = EXCLUDED.content_chunk,
                embedding = EXCLUDED.embedding,
                modelo_embedding = EXCLUDED.modelo_embedding,
                metadata = EXCLUDED.metadata
        """
        metadata = Jsonb({"source": "documentos", "chunking": "word_overlap_v1"})
        with self._connection.cursor() as cursor:
            cursor.execute(
                query,
                (
                    chunk.document_id,
                    f"documento:{chunk.document_id}",
                    chunk.index,
                    chunk.content,
                    vector,
                    model,
                    metadata,
                ),
            )
        self._connection.commit()


def ingest_documents(
    repository: KnowledgeRepository, embedder: Embedder, settings: KnowledgeSettings
) -> IngestionResult:
    """Vectoriza documentos y actualiza por clave única, sin generar duplicados."""
    models = repository.existing_models()
    if models and models != {embedder.model}:
        raise ExistingEmbeddingModelMismatch(
            "La tabla contiene embeddings de "
            f"{sorted(models)}; no se mezclarán con {embedder.model}"
        )

    documents = repository.read_documents()
    chunks = 0
    for document in documents:
        for index, content in enumerate(
            chunk_text(document.content, size=settings.chunk_size, overlap=settings.chunk_overlap)
        ):
            repository.upsert(
                EmbeddingChunk(
                    document_id=document.id,
                    index=index,
                    content=content,
                    vector=embedder.embed(content),
                ),
                embedder.model,
            )
            chunks += 1
    return IngestionResult(documents=len(documents), chunks=chunks, stored=chunks)


def run_ingestion(settings: KnowledgeSettings | None = None) -> IngestionResult:
    """Punto de entrada para la carga local explícitamente solicitada."""
    configured = settings or KnowledgeSettings()
    if configured.database_url is None:
        raise IngestionConfigurationError("Falta DATABASE_URL para la ingesta")
    embedder = GeminiEmbedder(configured)
    with PostgresKnowledgeRepository(
        configured.database_url.get_secret_value(), configured.knowledge_schema
    ) as repository:
        return ingest_documents(repository, embedder, configured)
