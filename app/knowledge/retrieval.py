"""Recuperación semántica e híbrida de chunks almacenados en PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite
from time import perf_counter

import psycopg
from google.genai import errors as genai_errors

from app.agent.nodes.retrieval import Retriever
from app.agent.state import RetrievedDocument
from app.knowledge.ingestion import GeminiEmbedder, IngestionConfigurationError
from app.knowledge.settings import KnowledgeSettings, get_knowledge_settings
from app.observability.telemetry import get_telemetry

_RECOVERABLE_DATABASE_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)

@dataclass(frozen=True)
class RetrievalCandidate:
    """Resultado interno con identidad estable y señales de cada recuperador."""

    source_type: str
    source_ref: str
    chunk_index: int
    content: str
    semantic_score: float | None = None
    lexical_score: float | None = None
    semantic_rank: int | None = None
    lexical_rank: int | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.source_type, self.source_ref, self.chunk_index)


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
        self._candidate_k = settings.hybrid_candidate_k
        self._model = settings.embedding_model
        self._embedder = GeminiEmbedder(settings)

    def __call__(self, query: str) -> list[RetrievedDocument]:
        vector = self._embedder.embed_query(query)
        serialized_vector = "[" + ",".join(str(value) for value in vector) + "]"
        statement = f"""
            SELECT source_type, source_ref, chunk_index, content_chunk,
                   1 - (embedding <=> %s::vector) AS score
            FROM {self._schema}.knowledge_embeddings
            WHERE modelo_embedding = %s
            ORDER BY embedding <=> %s::vector,
                     source_type ASC, source_ref ASC, chunk_index ASC
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
            for _, source_ref, chunk_index, content_chunk, score in rows
        ]


class PostgresHybridRetriever(PostgresVectorRetriever):
    """Combina pgvector y FTS sin cambiar el contrato público de los resultados."""

    def __init__(self, settings: KnowledgeSettings) -> None:
        super().__init__(settings)
        self._settings = settings

    def __call__(self, query: str) -> list[RetrievedDocument]:
        if self._settings.retrieval_mode == "semantic":
            return super().__call__(query)

        telemetry = get_telemetry()
        started = perf_counter()
        with telemetry.observation("knowledge.hybrid_retrieval", as_type="retriever") as span:
            semantic, semantic_failed = self._semantic_candidates(query)
            lexical, lexical_failed = self._lexical_candidates(query)
            if semantic_failed and lexical_failed:
                telemetry.update(
                    span,
                    {"outcome": "error", "error_type": "semantic_and_lexical_unavailable"},
                )
                raise IngestionConfigurationError("La recuperación híbrida no está disponible")

            fused = reciprocal_rank_fusion(
                semantic, lexical, rrf_k=self._settings.hybrid_rrf_k
            )
            if self._settings.hybrid_rerank_enabled:
                fused = deterministic_rerank(
                    fused,
                    rrf_weight=self._settings.hybrid_rrf_weight,
                    semantic_weight=self._settings.hybrid_semantic_weight,
                    lexical_weight=self._settings.hybrid_lexical_weight,
                    semantic_available=not semantic_failed and bool(semantic),
                    lexical_available=not lexical_failed and bool(lexical),
                )
                score_name = "rerank"
            else:
                score_name = "rrf"

            metadata: dict[str, object] = {
                "outcome": "partial" if semantic_failed or lexical_failed else "success",
                "latency_ms": round((perf_counter() - started) * 1000, 3),
            }
            if semantic_failed:
                metadata["error_type"] = "semantic_unavailable"
            elif lexical_failed:
                metadata["error_type"] = "lexical_unavailable"
            telemetry.update(span, metadata)
            return _documents_from_candidates(fused[: self._top_k], score_name)

    def _semantic_candidates(self, query: str) -> tuple[list[RetrievalCandidate], bool]:
        try:
            vector = self._embedder.embed_query(query)
        except genai_errors.APIError as error:
            if _is_recoverable_embedding_error(error):
                return [], True
            raise
        serialized_vector = "[" + ",".join(str(value) for value in vector) + "]"
        statement = f"""
            SELECT source_type, source_ref, chunk_index, content_chunk,
                   1 - (embedding <=> %s::vector) AS score
            FROM {self._schema}.knowledge_embeddings
            WHERE modelo_embedding = %s
            ORDER BY embedding <=> %s::vector,
                     source_type ASC, source_ref ASC, chunk_index ASC
            LIMIT %s
        """
        try:
            with psycopg.connect(self._database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        statement,
                        (serialized_vector, self._model, serialized_vector, self._candidate_k),
                    )
                    rows = cursor.fetchall()
            return _candidates(rows, score_name="semantic"), False
        except _RECOVERABLE_DATABASE_ERRORS:
            return [], True
        except psycopg.Error as error:
            raise IngestionConfigurationError(
                "La búsqueda semántica no es compatible con el esquema"
            ) from error

    def _lexical_candidates(self, query: str) -> tuple[list[RetrievalCandidate], bool]:
        statement = f"""
            WITH parsed_query AS (
                SELECT websearch_to_tsquery('spanish', %s) AS value
            )
            SELECT source_type, source_ref, chunk_index, content_chunk,
                   ts_rank_cd(search_vector, parsed_query.value, 32) AS score
            FROM {self._schema}.knowledge_embeddings
            CROSS JOIN parsed_query
            WHERE modelo_embedding = %s
              AND search_vector @@ parsed_query.value
            ORDER BY score DESC, source_type ASC, source_ref ASC, chunk_index ASC
            LIMIT %s
        """
        try:
            with psycopg.connect(self._database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(statement, (query, self._model, self._candidate_k))
                    rows = cursor.fetchall()
            return _candidates(rows, score_name="lexical"), False
        except _RECOVERABLE_DATABASE_ERRORS:
            return [], True
        except psycopg.Error as error:
            raise IngestionConfigurationError(
                "La búsqueda textual no es compatible con el esquema"
            ) from error


def _candidates(
    rows: list[tuple[object, ...]], *, score_name: str
) -> list[RetrievalCandidate]:
    candidates: list[RetrievalCandidate] = []
    for rank, row in enumerate(rows, start=1):
        source_type, source_ref, chunk_index, content_chunk, score = row
        kwargs = (
            {"semantic_score": _finite_or_none(score), "semantic_rank": rank}
            if score_name == "semantic"
            else {"lexical_score": _finite_or_none(score), "lexical_rank": rank}
        )
        candidates.append(
            RetrievalCandidate(
                source_type=str(source_type),
                source_ref=str(source_ref),
                chunk_index=int(chunk_index),
                content=str(content_chunk),
                **kwargs,
            )
        )
    return candidates


def reciprocal_rank_fusion(
    semantic: list[RetrievalCandidate], lexical: list[RetrievalCandidate], *, rrf_k: int
) -> list[RetrievalCandidate]:
    """Fusiona rangos independientes sin comparar escalas incompatibles."""
    by_identity: dict[tuple[str, str, int], RetrievalCandidate] = {}
    for candidate in [*semantic, *lexical]:
        previous = by_identity.get(candidate.identity)
        if previous is None:
            by_identity[candidate.identity] = candidate
        else:
            by_identity[candidate.identity] = replace(
                previous,
                semantic_score=(
                    candidate.semantic_score
                    if candidate.semantic_score is not None
                    else previous.semantic_score
                ),
                lexical_score=(
                    candidate.lexical_score
                    if candidate.lexical_score is not None
                    else previous.lexical_score
                ),
                semantic_rank=(
                    candidate.semantic_rank
                    if candidate.semantic_rank is not None
                    else previous.semantic_rank
                ),
                lexical_rank=(
                    candidate.lexical_rank
                    if candidate.lexical_rank is not None
                    else previous.lexical_rank
                ),
            )
    fused = []
    for candidate in by_identity.values():
        score = 0.0
        if candidate.semantic_rank is not None:
            score += 1 / (rrf_k + candidate.semantic_rank)
        if candidate.lexical_rank is not None:
            score += 1 / (rrf_k + candidate.lexical_rank)
        fused.append(replace(candidate, rrf_score=score))
    return sorted(fused, key=_rrf_order)


def deterministic_rerank(
    candidates: list[RetrievalCandidate],
    *,
    rrf_weight: float,
    semantic_weight: float,
    lexical_weight: float,
    semantic_available: bool,
    lexical_available: bool,
) -> list[RetrievalCandidate]:
    """Añade señales normalizadas de similitud y proximidad léxica a RRF."""
    weights = (rrf_weight, semantic_weight, lexical_weight)
    if not all(isfinite(weight) and weight >= 0 for weight in weights) or sum(weights) <= 0:
        raise ValueError("Los pesos de reranking deben ser finitos, no negativos y no nulos")
    if not candidates:
        return []
    rrf_values = _normalize([candidate.rrf_score for candidate in candidates])
    semantic_values = _normalize([candidate.semantic_score for candidate in candidates])
    lexical_values = _normalize([candidate.lexical_score for candidate in candidates])
    denominator = rrf_weight + (semantic_weight if semantic_available else 0) + (
        lexical_weight if lexical_available else 0
    )
    if denominator == 0:
        return [
            replace(candidate, rerank_score=candidate.rrf_score)
            for candidate in sorted(candidates, key=_rrf_order)
        ]
    ranked = []
    for index, candidate in enumerate(candidates):
        score = rrf_weight * rrf_values[index]
        if semantic_available:
            score += semantic_weight * semantic_values[index]
        if lexical_available:
            score += lexical_weight * lexical_values[index]
        ranked.append(replace(candidate, rerank_score=score / denominator))
    return sorted(ranked, key=_rerank_order)


def _normalize(values: list[float | None]) -> list[float]:
    finite = [value for value in values if value is not None and isfinite(value)]
    if not finite:
        return [0.0] * len(values)
    lower, upper = min(finite), max(finite)
    if lower == upper:
        return [0.5 if value is not None and isfinite(value) else 0.0 for value in values]
    scale = max(abs(lower), abs(upper))
    scaled_lower = lower / scale
    scaled_range = upper / scale - scaled_lower

    def normalize(value: float | None) -> float:
        if value is None or not isfinite(value):
            return 0.0
        normalized = (value / scale - scaled_lower) / scaled_range
        return min(1.0, max(0.0, normalized)) if isfinite(normalized) else 0.0

    return [
        normalize(value) for value in values
    ]


def _rrf_order(candidate: RetrievalCandidate) -> tuple[float, str, str, int]:
    return (
        -candidate.rrf_score,
        candidate.source_type,
        candidate.source_ref,
        candidate.chunk_index,
    )


def _rerank_order(candidate: RetrievalCandidate) -> tuple[float, float, str, str, int]:
    return (
        -(candidate.rerank_score or 0.0),
        -candidate.rrf_score,
        candidate.source_type,
        candidate.source_ref,
        candidate.chunk_index,
    )


def _documents_from_candidates(
    candidates: list[RetrievalCandidate], score_name: str
) -> list[RetrievedDocument]:
    def selected_score(candidate: RetrievalCandidate) -> float:
        value = {
            "rrf": candidate.rrf_score,
            "rerank": candidate.rerank_score,
        }[score_name]
        return float(value or 0.0)

    return [
        {
            "id": f"{candidate.source_ref}:{candidate.chunk_index}",
            "content": candidate.content,
            "score": selected_score(candidate),
        }
        for candidate in candidates
    ]


def _finite_or_none(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if isfinite(numeric) else None


def _is_recoverable_embedding_error(error: genai_errors.APIError) -> bool:
    """Permite solo indisponibilidad transitoria, nunca credenciales o seguridad."""
    return isinstance(error, genai_errors.ServerError) or error.code in {408, 429}


def build_configured_retriever(
    settings: KnowledgeSettings | None = None,
) -> Retriever:
    """Devuelve el recuperador real solo cuando toda la configuracion esta disponible."""
    configured = settings or get_knowledge_settings()
    if configured.database_url is None or configured.gemini_api_key is None:
        return unavailable_retriever
    try:
        if configured.retrieval_mode == "hybrid":
            return PostgresHybridRetriever(configured)
        return PostgresVectorRetriever(configured)
    except IngestionConfigurationError:
        return unavailable_retriever


def unavailable_retriever(_: str) -> list[RetrievedDocument]:
    """Propaga la falta de configuracion al manejo controlado del nodo RAG."""
    raise IngestionConfigurationError("La recuperacion no esta configurada")


def configured_retriever(query: str) -> list[RetrievedDocument]:
    """Resuelve configuracion solo al recorrer la rama RAG del grafo."""
    return build_configured_retriever()(query)
