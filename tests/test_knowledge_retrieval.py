from contextlib import contextmanager
from math import isfinite
from types import SimpleNamespace

import psycopg
import pytest
from google.genai import errors as genai_errors

from app.agent.nodes.retrieval import rag_retrieval_node
from app.agent.state import AgentState
from app.knowledge.ingestion import IngestionConfigurationError
from app.knowledge.retrieval import (
    PostgresHybridRetriever,
    PostgresVectorRetriever,
    RetrievalCandidate,
    _normalize,
    build_configured_retriever,
    deterministic_rerank,
    reciprocal_rank_fusion,
)
from app.knowledge.settings import KnowledgeSettings


def _settings(**updates) -> KnowledgeSettings:
    return KnowledgeSettings(
        _env_file=None,
        gemini_api_key="gemini-secret",
        database_url="postgresql://user:password@localhost/agente_inteligente_bd",
        **updates,
    )


def _state() -> AgentState:
    return {
        "query": "¿Cuál es el código de ficha?",
        "retrieval_requested": True,
        "route": "rag",
        "documents": [],
        "messages": [],
        "answer": None,
        "error": None,
    }


class FakeCursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.statement = ""
        self.parameters: tuple[object, ...] = ()
        self._rows = (
            rows
            if rows is not None
            else [("documento", "documento:7", 2, "cod_ficha: F-001", 0.91)]
        )

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[object, ...]) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


class RecordingTelemetry:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    @contextmanager
    def observation(self, *_: object, **__: object):
        yield object()

    def update(self, _: object, metadata: dict[str, object]) -> None:
        self.updates.append(metadata)


def _fake_embedding_sdk() -> SimpleNamespace:
    response = SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0] + [0.0] * 766)])
    return SimpleNamespace(models=SimpleNamespace(embed_content=lambda **_: response))


def test_recuperador_consulta_coseno_y_convierte_documentos(monkeypatch):
    cursor = FakeCursor()
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    monkeypatch.setattr("app.knowledge.retrieval.psycopg.connect", lambda _: FakeConnection(cursor))

    documents = PostgresVectorRetriever(_settings(retrieval_top_k=3))("ficha del predio")

    assert "embedding <=> %s::vector" in cursor.statement
    assert "WHERE modelo_embedding = %s" in cursor.statement
    assert "LIMIT %s" in cursor.statement
    assert cursor.parameters[1] == "gemini-embedding-001"
    assert cursor.parameters[3] == 3
    assert documents == [{"id": "documento:7:2", "content": "cod_ficha: F-001", "score": 0.91}]


def test_recuperador_rechaza_respuesta_de_embedding_con_dimension_invalida(monkeypatch):
    response = SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0] * 767)])
    sdk = SimpleNamespace(models=SimpleNamespace(embed_content=lambda **_: response))
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: sdk)

    retriever = PostgresVectorRetriever(_settings())

    with pytest.raises(RuntimeError, match="767 dimensiones"):
        retriever("consulta")


def test_fallo_de_conexion_se_degrada_en_nodo_rag(monkeypatch):
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    monkeypatch.setattr(
        "app.knowledge.retrieval.psycopg.connect",
        lambda _: (_ for _ in ()).throw(RuntimeError("base no disponible")),
    )

    update = rag_retrieval_node(_state(), PostgresVectorRetriever(_settings()))

    assert update == {"documents": [], "error": "retrieval_unavailable"}


def test_recuperacion_valida_sin_resultados_no_agrega_error(monkeypatch):
    cursor = FakeCursor(rows=[])
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    monkeypatch.setattr("app.knowledge.retrieval.psycopg.connect", lambda _: FakeConnection(cursor))

    update = rag_retrieval_node(_state(), PostgresVectorRetriever(_settings()))

    assert update == {"documents": [], "error": None}


def test_sin_configuracion_el_recuperador_configurado_reporta_no_disponible():
    settings = KnowledgeSettings(_env_file=None)

    update = rag_retrieval_node(_state(), build_configured_retriever(settings))

    assert update == {"documents": [], "error": "retrieval_unavailable"}


def test_recuperador_hibrido_consulta_fts_parametrizado_y_filtra_modelo(monkeypatch):
    cursors = [
        FakeCursor(rows=[("documento", "documento:7", 0, "semántico", 0.4)]),
        FakeCursor(rows=[("documento", "documento:7", 0, "léxico", 0.8)]),
    ]
    semantic_cursor, lexical_cursor = cursors
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    monkeypatch.setattr(
        "app.knowledge.retrieval.psycopg.connect",
        lambda _: FakeConnection(cursors.pop(0)),
    )

    documents = PostgresHybridRetriever(_settings(retrieval_mode="hybrid"))("ficha ' OR 1=1")

    assert documents[0]["id"] == "documento:7:0"
    assert "websearch_to_tsquery('spanish', %s)" in lexical_cursor.statement
    assert "WHERE modelo_embedding = %s" in semantic_cursor.statement
    assert "WHERE modelo_embedding = %s" in lexical_cursor.statement


def test_rrf_deduplica_por_identidad_compuesta_y_ordena_establemente():
    semantic = [
        RetrievalCandidate("documento", "documento:7", 0, "a", semantic_rank=1),
        RetrievalCandidate("documento", "documento:8", 0, "b", semantic_rank=2),
    ]
    lexical = [
        RetrievalCandidate("documento", "documento:7", 0, "a", lexical_rank=1),
        RetrievalCandidate("otro", "documento:7", 0, "c", lexical_rank=2),
    ]

    fused = reciprocal_rank_fusion(semantic, lexical, rrf_k=60)

    assert [candidate.identity for candidate in fused] == [
        ("documento", "documento:7", 0),
        ("documento", "documento:8", 0),
        ("otro", "documento:7", 0),
    ]


def test_reranking_puede_cambiar_orden_rrf_y_desactivado_no_lo_hace():
    fused = reciprocal_rank_fusion(
        [
            RetrievalCandidate("documento", "documento:1", 0, "a", 0.1, None, 1),
            RetrievalCandidate("documento", "documento:2", 0, "b", 0.9, None, 2),
        ],
        [
            RetrievalCandidate("documento", "documento:1", 0, "a", None, 0.1, None, 1),
            RetrievalCandidate("documento", "documento:2", 0, "b", None, 0.9, None, 2),
        ],
        rrf_k=60,
    )

    reranked = deterministic_rerank(
        fused,
        rrf_weight=0.40,
        semantic_weight=0.30,
        lexical_weight=0.30,
        semantic_available=True,
        lexical_available=True,
    )

    assert [candidate.source_ref for candidate in fused] == ["documento:1", "documento:2"]
    assert [candidate.source_ref for candidate in reranked] == ["documento:2", "documento:1"]


@pytest.mark.parametrize("score", [float("nan"), float("inf"), float("-inf"), None])
def test_reranking_maneja_senales_no_finitas(score):
    candidates = [
        RetrievalCandidate("documento", "documento:1", 0, "a", score, score, 1, 1, 0.1),
        RetrievalCandidate("documento", "documento:2", 0, "b", -1.0, 0.2, 2, 2, 0.09),
    ]

    reranked = deterministic_rerank(
        candidates,
        rrf_weight=0.4,
        semantic_weight=0.3,
        lexical_weight=0.3,
        semantic_available=True,
        lexical_available=True,
    )

    assert len(reranked) == 2


def test_reranking_sin_pesos_de_senales_disponibles_conserva_orden_rrf():
    candidates = [
        RetrievalCandidate("documento", "documento:2", 0, "b", rrf_score=0.1),
        RetrievalCandidate("documento", "documento:1", 0, "a", rrf_score=0.2),
    ]

    reranked = deterministic_rerank(
        candidates,
        rrf_weight=0,
        semantic_weight=1,
        lexical_weight=0,
        semantic_available=False,
        lexical_available=True,
    )

    assert [candidate.source_ref for candidate in reranked] == ["documento:1", "documento:2"]
    assert [candidate.rerank_score for candidate in reranked] == [0.2, 0.1]


def test_normalizacion_de_extremos_finitos_permanece_finita():
    normalized = _normalize([-1e308, 1e308])

    assert normalized == [0.0, 1.0]
    assert all(isfinite(value) for value in normalized)


def test_fallo_parcial_conserva_resultados_de_la_rama_sana(monkeypatch):
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    retriever = PostgresHybridRetriever(_settings(retrieval_mode="hybrid"))
    monkeypatch.setattr(retriever, "_semantic_candidates", lambda _: ([], True))
    monkeypatch.setattr(
        retriever,
        "_lexical_candidates",
        lambda _: (
            [RetrievalCandidate("documento", "documento:7", 0, "léxico", lexical_rank=1)],
            False,
        ),
    )

    assert retriever("consulta") == [
        {"id": "documento:7:0", "content": "léxico", "score": pytest.approx(1 / 61)}
    ]


def test_fallo_recuperable_de_embedding_conserva_resultado_fts(monkeypatch):
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    retriever = PostgresHybridRetriever(_settings(retrieval_mode="hybrid"))
    monkeypatch.setattr(
        retriever._embedder,
        "embed_query",
        lambda _: (_ for _ in ()).throw(genai_errors.ServerError(503, {})),
    )
    monkeypatch.setattr(
        retriever,
        "_lexical_candidates",
        lambda _: (
            [RetrievalCandidate("documento", "documento:7", 0, "léxico", lexical_rank=1)],
            False,
        ),
    )

    assert retriever("consulta") == [
        {"id": "documento:7:0", "content": "léxico", "score": pytest.approx(1 / 61)}
    ]


def test_fallback_de_reranking_conserva_puntuacion_rrf_publica(monkeypatch):
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    retriever = PostgresHybridRetriever(
        _settings(
            retrieval_mode="hybrid",
            hybrid_rerank_enabled=True,
            hybrid_rrf_weight=0,
            hybrid_semantic_weight=1,
            hybrid_lexical_weight=0,
        )
    )
    monkeypatch.setattr(retriever, "_semantic_candidates", lambda _: ([], True))
    monkeypatch.setattr(
        retriever,
        "_lexical_candidates",
        lambda _: (
            [RetrievalCandidate("documento", "documento:7", 0, "léxico", lexical_rank=1)],
            False,
        ),
    )

    assert retriever("consulta") == [
        {"id": "documento:7:0", "content": "léxico", "score": pytest.approx(1 / 61)}
    ]


def test_fallo_parcial_registra_solo_categoria_tecnica_segura(monkeypatch):
    telemetry = RecordingTelemetry()
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    monkeypatch.setattr("app.knowledge.retrieval.get_telemetry", lambda: telemetry)
    retriever = PostgresHybridRetriever(_settings(retrieval_mode="hybrid"))
    monkeypatch.setattr(retriever, "_semantic_candidates", lambda _: ([], True))
    monkeypatch.setattr(
        retriever,
        "_lexical_candidates",
        lambda _: (
            [RetrievalCandidate("documento", "documento:7", 0, "privado", lexical_rank=1)],
            False,
        ),
    )

    retriever("consulta confidencial")

    assert telemetry.updates[-1]["outcome"] == "partial"
    assert telemetry.updates[-1]["error_type"] == "semantic_unavailable"
    assert "consulta confidencial" not in str(telemetry.updates)
    assert "privado" not in str(telemetry.updates)


def test_dos_fallos_hibridos_conservan_retrieval_unavailable(monkeypatch):
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    retriever = PostgresHybridRetriever(_settings(retrieval_mode="hybrid"))
    monkeypatch.setattr(retriever, "_semantic_candidates", lambda _: ([], True))
    monkeypatch.setattr(retriever, "_lexical_candidates", lambda _: ([], True))

    assert rag_retrieval_node(_state(), retriever) == {
        "documents": [],
        "error": "retrieval_unavailable",
    }


def test_fallo_operacional_de_base_permite_degradacion_parcial(monkeypatch):
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    retriever = PostgresHybridRetriever(_settings(retrieval_mode="hybrid"))
    monkeypatch.setattr(
        "app.knowledge.retrieval.psycopg.connect",
        lambda _: (_ for _ in ()).throw(psycopg.OperationalError("no disponible")),
    )

    assert retriever._semantic_candidates("consulta") == ([], True)


def test_error_estructural_de_psycopg_no_se_oculta_como_fallo_parcial(monkeypatch):
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    retriever = PostgresHybridRetriever(_settings(retrieval_mode="hybrid"))
    monkeypatch.setattr(
        "app.knowledge.retrieval.psycopg.connect",
        lambda _: (_ for _ in ()).throw(psycopg.ProgrammingError("columna inexistente")),
    )

    with pytest.raises(IngestionConfigurationError, match="búsqueda textual"):
        retriever._lexical_candidates("consulta")


def test_error_inesperado_no_se_oculta_como_fallo_parcial(monkeypatch):
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    retriever = PostgresHybridRetriever(_settings(retrieval_mode="hybrid"))
    monkeypatch.setattr(
        "app.knowledge.retrieval.psycopg.connect",
        lambda _: (_ for _ in ()).throw(RuntimeError("defecto")),
    )

    with pytest.raises(RuntimeError, match="defecto"):
        retriever._lexical_candidates("consulta")


@pytest.mark.parametrize(
    "updates",
    [
        {"hybrid_rrf_weight": 0, "hybrid_semantic_weight": 0, "hybrid_lexical_weight": 0},
        {"hybrid_rrf_weight": float("nan")},
        {"hybrid_candidate_k": 3, "retrieval_top_k": 4},
    ],
)
def test_configuracion_hibrida_rechaza_pesos_y_candidatos_invalidos(updates):
    with pytest.raises(ValueError):
        _settings(**updates)
