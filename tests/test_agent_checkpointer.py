import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver

import app.agent.checkpointer as checkpointer_module
from app.agent.checkpointer import (
    CHECKPOINT_SCHEMA,
    CheckpointerConfigurationError,
    postgres_checkpointer,
    setup_postgres_checkpointer,
)
from app.agent.graph import PendingCheckpointError, build_agent_graph, invoke_agent
from app.agent.state import AgentState, RetrievedDocument
from app.integrations.gemini import GeminiResult
from app.knowledge.settings import KnowledgeSettings


class FakeGemini:
    def __init__(self, *responses: str) -> None:
        self._responses = iter(responses)

    def generate(self, _: str) -> GeminiResult:
        return GeminiResult(text=next(self._responses), raw_payload={"provider": "fake"})


def _state(*, retrieval_requested: bool = False) -> AgentState:
    return {
        "query": "¿Cómo funciona JWT?",
        "retrieval_requested": retrieval_requested,
        "route": None,
        "documents": [],
        "messages": [],
        "answer": None,
        "error": None,
    }


def _settings(**updates: object) -> KnowledgeSettings:
    return KnowledgeSettings(
        _env_file=None,
        database_url="postgresql://user:password@localhost/agente_inteligente_bd",
        **updates,
    )


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def test_persistencia_asocia_estado_al_thread_id_y_se_recupera_al_reconstruir_grafo():
    checkpointer = InMemorySaver()
    thread_id = "conversation-1"

    result = invoke_agent(
        _state(),
        gemini_client=FakeGemini("reasoning", "Respuesta persistida."),
        checkpointer=checkpointer,
        thread_id=thread_id,
        recursion_limit=3,
    )
    rebuilt_graph = build_agent_graph(checkpointer=checkpointer)

    snapshot = rebuilt_graph.get_state(_config(thread_id))
    resumed = rebuilt_graph.invoke(None, _config(thread_id))

    assert result["answer"] == "Respuesta persistida."
    assert snapshot.values["answer"] == "Respuesta persistida."
    assert snapshot.next == ()
    assert resumed["answer"] == "Respuesta persistida."


def test_threads_diferentes_mantienen_sus_checkpoints_aislados():
    checkpointer = InMemorySaver()
    invoke_agent(
        _state(),
        gemini_client=FakeGemini("reasoning", "Respuesta A."),
        checkpointer=checkpointer,
        thread_id="conversation-a",
        recursion_limit=3,
    )
    invoke_agent(
        _state(retrieval_requested=True),
        retriever=lambda _: [],
        gemini_client=FakeGemini("rag", "Respuesta B."),
        checkpointer=checkpointer,
        thread_id="conversation-b",
        recursion_limit=4,
    )
    graph = build_agent_graph(checkpointer=checkpointer)

    assert graph.get_state(_config("conversation-a")).values["answer"] == "Respuesta A."
    assert graph.get_state(_config("conversation-b")).values["answer"] == "Respuesta B."


def test_checkpointer_no_altera_rutas_reasoning_ni_rag():
    checkpointer = InMemorySaver()
    document: RetrievedDocument = {"id": "auth-1", "content": "JWT firma tokens.", "score": 0.9}

    direct = invoke_agent(
        _state(),
        gemini_client=FakeGemini("reasoning", "Respuesta directa."),
        checkpointer=checkpointer,
        thread_id="direct",
        recursion_limit=3,
    )
    rag = invoke_agent(
        _state(retrieval_requested=True),
        retriever=lambda _: [document],
        gemini_client=FakeGemini("rag", "Respuesta RAG."),
        checkpointer=checkpointer,
        thread_id="rag",
        recursion_limit=4,
    )

    assert direct["route"] == "reasoning"
    assert rag["route"] == "rag"
    assert rag["documents"] == [document]


def test_thread_id_requiere_checkpointer():
    with pytest.raises(ValueError, match="requiere un checkpointer"):
        invoke_agent(_state(), thread_id="without-saver", recursion_limit=3)


def test_hilo_nuevo_descarta_memoria_historica_recibida_en_la_entrada():
    state = _state()
    state.update(
        {
            "messages": [{"role": "assistant", "content": "No conservar", "raw_payload": {}}],
            "conversation_summary": "No conservar",
            "summary_cursor": 8,
            "summary_failure_cursor": 8,
        }
    )

    result = invoke_agent(
        state,
        gemini_client=FakeGemini("reasoning", "Respuesta nueva."),
        checkpointer=InMemorySaver(),
        thread_id="new-thread",
        recursion_limit=3,
    )

    assert [message["content"] for message in result["messages"]] == [
        "¿Cómo funciona JWT?",
        "reasoning",
        "Respuesta nueva.",
    ]
    assert "No conservar" not in str(result)


def test_hilo_existente_descarta_memoria_historica_recibida_y_preserva_checkpoint():
    checkpointer = InMemorySaver()
    invoke_agent(
        _state(),
        gemini_client=FakeGemini("reasoning", "Respuesta persistida."),
        checkpointer=checkpointer,
        thread_id="existing-thread",
        recursion_limit=3,
    )
    incoming = _state()
    incoming.update(
        {
            "query": "Siguiente consulta",
            "messages": [{"role": "assistant", "content": "Inyectado", "raw_payload": {}}],
            "conversation_summary": "Inyectado",
            "summary_cursor": 99,
            "summary_failure_cursor": 99,
        }
    )

    result = invoke_agent(
        incoming,
        gemini_client=FakeGemini("reasoning", "Segunda respuesta."),
        checkpointer=checkpointer,
        thread_id="existing-thread",
        recursion_limit=3,
    )

    assert "Inyectado" not in str(result)
    assert [message["content"] for message in result["messages"] if message["role"] == "user"] == [
        "¿Cómo funciona JWT?",
        "Siguiente consulta",
    ]


def test_hilo_con_checkpoint_pendiente_rechaza_un_nuevo_turno():
    checkpointer = InMemorySaver()
    graph = build_agent_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "pending-thread"}, "recursion_limit": 4}
    graph.invoke({**_state(), "new_turn": True}, config, interrupt_before=["router"])

    with pytest.raises(PendingCheckpointError, match="trabajo pendiente"):
        invoke_agent(
            _state(),
            checkpointer=checkpointer,
            thread_id="pending-thread",
            recursion_limit=3,
        )


def test_ejecucion_sin_checkpointer_inicializa_memoria_y_descarta_historial_de_entrada():
    state = _state()
    state.update(
        {
            "messages": [{"role": "assistant", "content": "Ignorar", "raw_payload": {}}],
            "conversation_summary": "Ignorar",
            "summary_cursor": 5,
            "summary_failure_cursor": None,
        }
    )

    result = invoke_agent(
        state,
        gemini_client=FakeGemini("reasoning", "Respuesta local."),
        recursion_limit=3,
    )

    assert "Ignorar" not in str(result)
    assert result["messages"][0]["role"] == "user"


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakePostgresSaver:
    def __init__(self, connection: FakeConnection, *, serde: object) -> None:
        self.connection = connection
        self.serde = serde
        self.setup_called = False

    def setup(self) -> None:
        self.setup_called = True


def test_postgres_saver_usa_esquema_verificado_y_serializacion_estricta(monkeypatch):
    connection = FakeConnection()
    captured: dict[str, object] = {}

    def fake_connect(url: str, **kwargs: object) -> FakeConnection:
        captured["url"] = url
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(checkpointer_module.psycopg, "connect", fake_connect)
    monkeypatch.setattr(checkpointer_module, "PostgresSaver", FakePostgresSaver)

    with postgres_checkpointer(_settings()) as saver:
        assert isinstance(saver, FakePostgresSaver)
        assert saver.serde.pickle_fallback is False
        assert saver.serde._allowed_msgpack_modules is None

    assert captured["autocommit"] is True
    assert captured["row_factory"] is checkpointer_module.dict_row
    assert captured["options"] == f"-c search_path={CHECKPOINT_SCHEMA},public"
    assert connection.closed is True


def test_setup_es_explicito_y_se_ejecuta_solo_al_solicitarse(monkeypatch):
    connection = FakeConnection()
    created: list[FakePostgresSaver] = []
    monkeypatch.setattr(
        checkpointer_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: connection,
    )

    def fake_saver(*args: object, **kwargs: object) -> FakePostgresSaver:
        saver = FakePostgresSaver(*args, **kwargs)
        created.append(saver)
        return saver

    monkeypatch.setattr(checkpointer_module, "PostgresSaver", fake_saver)

    setup_postgres_checkpointer(_settings())

    assert created[0].setup_called is True
    assert connection.closed is True


def test_error_de_conexion_se_reporta_sin_exponer_database_url(monkeypatch):
    monkeypatch.setattr(
        checkpointer_module.psycopg,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(psycopg.OperationalError("unavailable")),
    )

    with pytest.raises(CheckpointerConfigurationError, match="No fue posible conectar") as error:
        with postgres_checkpointer(_settings()):
            pass

    assert "postgresql://" not in str(error.value)
