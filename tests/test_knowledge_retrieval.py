from types import SimpleNamespace

import pytest

from app.agent.nodes.retrieval import rag_retrieval_node
from app.agent.state import AgentState
from app.knowledge.retrieval import PostgresVectorRetriever, build_configured_retriever
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
        self._rows = rows if rows is not None else [("documento:7", 2, "cod_ficha: F-001", 0.91)]

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


def _fake_embedding_sdk() -> SimpleNamespace:
    response = SimpleNamespace(
        embeddings=[SimpleNamespace(values=[3.0, 4.0] + [0.0] * 766)]
    )
    return SimpleNamespace(models=SimpleNamespace(embed_content=lambda **_: response))


def test_recuperador_consulta_coseno_y_convierte_documentos(monkeypatch):
    cursor = FakeCursor()
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: _fake_embedding_sdk())
    monkeypatch.setattr(
        "app.knowledge.retrieval.psycopg.connect", lambda _: FakeConnection(cursor)
    )

    documents = PostgresVectorRetriever(_settings(retrieval_top_k=3))("ficha del predio")

    assert "embedding <=> %s::vector" in cursor.statement
    assert "WHERE modelo_embedding = %s" in cursor.statement
    assert "LIMIT %s" in cursor.statement
    assert cursor.parameters[1] == "gemini-embedding-001"
    assert cursor.parameters[3] == 3
    assert documents == [
        {"id": "documento:7:2", "content": "cod_ficha: F-001", "score": 0.91}
    ]


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
    monkeypatch.setattr(
        "app.knowledge.retrieval.psycopg.connect", lambda _: FakeConnection(cursor)
    )

    update = rag_retrieval_node(_state(), PostgresVectorRetriever(_settings()))

    assert update == {"documents": [], "error": None}


def test_sin_configuracion_el_recuperador_configurado_reporta_no_disponible():
    settings = KnowledgeSettings(_env_file=None)

    update = rag_retrieval_node(_state(), build_configured_retriever(settings))

    assert update == {"documents": [], "error": "retrieval_unavailable"}
