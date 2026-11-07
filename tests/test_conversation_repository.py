from uuid import uuid4

from app.conversations.repository import ConversationRepository


class _Result:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def fetchone(self) -> dict | None:
        return self._row


class ConflictConnection:
    def __init__(self, existing: dict) -> None:
        self.existing = existing
        self.statements: list[str] = []

    def execute(self, statement: str, _params: tuple) -> _Result:
        self.statements.append(statement)
        if "INSERT INTO" in statement:
            return _Result(None)
        return _Result(self.existing)


def test_reutilizar_hilo_existente_no_actualiza_updated_at():
    existing = {
        "id": uuid4(),
        "channel": "web_widget",
        "external_conversation_id": "visitor-1",
        "langgraph_thread_id": "existing-thread",
        "status": "active",
    }
    connection = ConflictConnection(existing)
    repository = ConversationRepository(connection)  # type: ignore[arg-type]

    thread = repository.get_or_create_thread("web_widget", "visitor-1")

    assert thread.id == existing["id"]
    assert len(connection.statements) == 2
    assert "ON CONFLICT (channel, external_conversation_id) DO NOTHING" in connection.statements[0]
    assert "updated_at" not in connection.statements[0]
    assert "SELECT id, channel" in connection.statements[1]


class PauseConnection:
    def __init__(self, paused: dict) -> None:
        self.paused = paused
        self.statement = ""
        self.params: tuple = ()

    def execute(self, statement: str, params: tuple) -> _Result:
        self.statement = statement
        self.params = params
        return _Result(self.paused)


def test_pausar_hilo_es_una_transicion_condicionada_e_idempotente():
    thread_id = uuid4()
    paused = {
        "id": thread_id,
        "channel": "web_widget",
        "external_conversation_id": "visitor-1",
        "langgraph_thread_id": "thread-1",
        "status": "paused_hil",
    }
    connection = PauseConnection(paused)
    repository = ConversationRepository(connection)  # type: ignore[arg-type]

    thread = repository.mark_thread_paused_hil(thread_id)

    assert thread.status == "paused_hil"
    assert "status IN ('active', 'paused_hil')" in connection.statement
    assert "WHEN status = 'paused_hil' THEN updated_at" in connection.statement
    assert connection.params == (thread_id,)
