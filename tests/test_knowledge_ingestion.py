from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app.knowledge.ingestion import (
    EmbeddingChunk,
    ExistingEmbeddingModelMismatch,
    GeminiEmbedder,
    SourceDocument,
    chunk_text,
    ingest_documents,
)
from app.knowledge.settings import KnowledgeSettings


@dataclass
class MemoryRepository:
    documents: list[SourceDocument]
    rows: dict[tuple[str, int], EmbeddingChunk] = field(default_factory=dict)
    models: set[str] = field(default_factory=set)

    def read_documents(self) -> list[SourceDocument]:
        return self.documents

    def existing_models(self) -> set[str]:
        return self.models

    def upsert(self, chunk: EmbeddingChunk, model: str) -> None:
        self.models.add(model)
        self.rows[(f"documento:{chunk.document_id}", chunk.index)] = chunk


class FakeEmbedder:
    model = "gemini-embedding-001"
    dimensions = 768

    def embed(self, _: str) -> list[float]:
        return [1.0] + [0.0] * 767


def _settings() -> KnowledgeSettings:
    return KnowledgeSettings(_env_file=None, chunk_size=12, chunk_overlap=3)


def test_chunk_text_preserva_palabras_completas_y_solapamiento():
    chunks = chunk_text("uno dos tres cuatro cinco", size=12, overlap=3)

    assert chunks == ["uno dos tres", "tres cuatro", "cuatro cinco"]


def test_chunk_text_vacio_no_devuelve_chunks():
    assert chunk_text(" \n\t ", size=12, overlap=3) == []


def test_chunk_text_divide_palabra_mayor_al_limite_con_avance_controlado():
    chunks = chunk_text("abcdefghij", size=4, overlap=1)

    assert chunks == ["abcd", "defg", "ghij"]


@pytest.mark.parametrize("size, overlap", [(0, 0), (4, -1), (4, 4)])
def test_chunk_text_rechaza_parametros_invalidos(size: int, overlap: int):
    with pytest.raises(ValueError):
        chunk_text("texto", size=size, overlap=overlap)


def test_chunk_text_con_solapamiento_maximo_termina_sin_bucle_infinito():
    chunks = chunk_text("abcdefghij", size=2, overlap=1)

    assert chunks == ["ab", "bc", "cd", "de", "ef", "fg", "gh", "hi", "ij"]


def test_ingesta_vectoriza_y_repite_sin_duplicados():
    repository = MemoryRepository([SourceDocument(id=7, content="uno dos tres cuatro cinco")])

    first = ingest_documents(repository, FakeEmbedder(), _settings())
    second = ingest_documents(repository, FakeEmbedder(), _settings())

    assert first.documents == second.documents == 1
    assert first.chunks == second.chunks == 3
    assert len(repository.rows) == 3
    assert all(len(chunk.vector) == 768 for chunk in repository.rows.values())


def test_ingesta_no_mezcla_modelos_existentes():
    repository = MemoryRepository([], models={"otro-modelo"})

    with pytest.raises(ExistingEmbeddingModelMismatch):
        ingest_documents(repository, FakeEmbedder(), _settings())


def test_gemini_embedder_exige_y_normaliza_768_dimensiones(monkeypatch):
    response = SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0] + [0.0] * 766)])
    sdk = SimpleNamespace(models=SimpleNamespace(embed_content=lambda **_: response))
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: sdk)

    embedder = GeminiEmbedder(KnowledgeSettings(_env_file=None, gemini_api_key="secret"))
    vector = embedder.embed("documento de prueba")

    assert len(vector) == 768
    assert vector[:2] == [0.6, 0.8]


def test_gemini_embedder_usa_retrieval_query_para_consultas(monkeypatch):
    response = SimpleNamespace(embeddings=[SimpleNamespace(values=[3.0, 4.0] + [0.0] * 766)])
    calls: list[dict] = []
    sdk = SimpleNamespace(
        models=SimpleNamespace(embed_content=lambda **kwargs: (calls.append(kwargs), response)[1])
    )
    monkeypatch.setattr("app.knowledge.ingestion.genai.Client", lambda **_: sdk)

    vector = GeminiEmbedder(KnowledgeSettings(_env_file=None, gemini_api_key="secret")).embed_query(
        "consulta de prueba"
    )

    assert len(vector) == 768
    assert vector[:2] == [0.6, 0.8]
    assert calls[0]["model"] == "gemini-embedding-001"
    assert calls[0]["config"].task_type == "RETRIEVAL_QUERY"
