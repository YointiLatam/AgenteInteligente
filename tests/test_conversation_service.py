from __future__ import annotations

from collections.abc import Iterable
from threading import Event, Lock, Thread
from uuid import UUID, uuid4, uuid5

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import build_agent_graph
from app.conversations.repository import (
    Channel,
    ConversationPersistenceError,
    ConversationRepository,
    ConversationThread,
    MessageRole,
    StoredMessage,
)
from app.conversations.service import (
    ConversationService,
    TurnIdempotencyConflictError,
    TurnRecoveryRequiredError,
    UserTurnRequest,
)
from app.integrations.gemini import GeminiResult


class FakeGemini:
    def __init__(self, responses: Iterable[GeminiResult]) -> None:
        self._responses = iter(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> GeminiResult:
        self.prompts.append(prompt)
        return next(self._responses)


class BlockingGemini(FakeGemini):
    def __init__(self) -> None:
        super().__init__([_result("reasoning"), _result("Respuesta")])
        self.started = Event()
        self.release = Event()

    def generate(self, prompt: str) -> GeminiResult:
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            self.started.set()
            assert self.release.wait(timeout=5)
        return next(self._responses)


class MemoryRepository:
    def __init__(self, *, fail_first_agent_write: bool = False) -> None:
        self._threads: dict[tuple[Channel, str], ConversationThread] = {}
        self._messages: dict[tuple[UUID, str], StoredMessage] = {}
        self.fail_first_agent_write = fail_first_agent_write

    def get_or_create_thread(
        self, channel: Channel, external_conversation_id: str
    ) -> ConversationThread:
        key = (channel, external_conversation_id)
        if key not in self._threads:
            identifier = uuid4()
            self._threads[key] = ConversationThread(
                id=identifier,
                channel=channel,
                external_conversation_id=external_conversation_id,
                langgraph_thread_id=str(identifier),
                status="active",
            )
        return self._threads[key]

    def get_message(self, thread_id: UUID, idempotency_key: str) -> StoredMessage | None:
        return self._messages.get((thread_id, idempotency_key))

    def store_message(
        self,
        *,
        thread_id: UUID,
        turn_id: UUID,
        role: MessageRole,
        content: str,
        raw_payload: dict,
        idempotency_key: str,
    ) -> StoredMessage:
        if role == "agent" and self.fail_first_agent_write:
            self.fail_first_agent_write = False
            raise ConversationPersistenceError("write failed")
        key = (thread_id, idempotency_key)
        existing = self._messages.get(key)
        if existing is not None:
            return existing
        message = StoredMessage(
            id=uuid4(),
            thread_id=thread_id,
            turn_id=turn_id,
            role=role,
            content=content,
            raw_payload=raw_payload,
            idempotency_key=idempotency_key,
        )
        self._messages[key] = message
        return message

    def list_messages(self, thread_id: UUID) -> list[StoredMessage]:
        return [
            message
            for (stored_thread, _), message in self._messages.items()
            if stored_thread == thread_id
        ]


class AdvisoryConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, _params: tuple) -> object:
        self.statements.append(statement)
        return object()


class SharedConnectionRepository(MemoryRepository):
    """Memoria de prueba que reutiliza el thread_lock real de un solo repositorio."""

    thread_lock = ConversationRepository.thread_lock

    def __init__(self) -> None:
        super().__init__()
        self._connection = AdvisoryConnection()
        self._thread_locks: dict[str, Lock] = {}
        self._thread_locks_guard = Lock()
        self._thread_calls = 0
        self._thread_calls_guard = Lock()
        self.second_thread_resolved = Event()

    def get_or_create_thread(
        self, channel: Channel, external_conversation_id: str
    ) -> ConversationThread:
        thread = super().get_or_create_thread(channel, external_conversation_id)
        with self._thread_calls_guard:
            self._thread_calls += 1
            if self._thread_calls == 2:
                self.second_thread_resolved.set()
        return thread


def _result(text: str, **payload: object) -> GeminiResult:
    return GeminiResult(text=text, raw_payload={"provider": "fake", **payload})


def _service(repository: MemoryRepository, client: FakeGemini) -> ConversationService:
    return ConversationService(repository, InMemorySaver(), gemini_client=client, recursion_limit=3)  # type: ignore[arg-type]


def test_persiste_user_y_agent_sin_evento_router_y_conserva_payload():
    repository = MemoryRepository()
    service = _service(
        repository,
        FakeGemini([_result("reasoning"), _result("Respuesta", input_tokens=4, total_tokens=7)]),
    )

    result = service.process_user_turn(
        UserTurnRequest("web_widget", "visitor-1", "req-1", "Hola", raw_payload={"source": "web"})
    )
    messages = repository.list_messages(result.thread.id)

    assert [(message.role, message.content) for message in messages] == [
        ("user", "Hola"),
        ("agent", "Respuesta"),
    ]
    assert messages[0].raw_payload == {
        "source": "web",
        "_conversation": {"retrieval_requested": False},
    }
    assert messages[1].raw_payload["input_tokens"] == 4
    assert result.replayed is False


def test_reintento_del_mismo_request_no_vuelve_a_ejecutar_el_grafo():
    repository = MemoryRepository()
    client = FakeGemini([_result("reasoning"), _result("Respuesta")])
    service = _service(repository, client)
    request = UserTurnRequest("web_widget", "visitor-1", "req-1", "Hola")

    first = service.process_user_turn(request)
    replay = service.process_user_turn(request)

    assert replay.message.id == first.message.id
    assert replay.replayed is True
    assert len(client.prompts) == 2
    assert len(repository.list_messages(first.thread.id)) == 2


def test_mismo_request_id_con_contenido_distinto_devuelve_conflicto():
    repository = MemoryRepository()
    client = FakeGemini([_result("reasoning"), _result("Respuesta")])
    service = _service(repository, client)
    service.process_user_turn(UserTurnRequest("web_widget", "visitor-1", "req-1", "Hola"))

    with pytest.raises(TurnIdempotencyConflictError, match="solicitud incompatible"):
        service.process_user_turn(UserTurnRequest("web_widget", "visitor-1", "req-1", "Adiós"))

    assert len(client.prompts) == 2


def test_solicitudes_concurrentes_del_mismo_turno_ejecutan_un_solo_grafo():
    repository = MemoryRepository()
    client = BlockingGemini()
    service = _service(repository, client)
    request = UserTurnRequest("web_widget", "visitor-1", "req-1", "Hola")
    results = []
    errors = []

    def process() -> None:
        try:
            results.append(service.process_user_turn(request))
        except Exception as error:  # pragma: no cover - solo facilita diagnosticar el hilo.
            errors.append(error)

    first = Thread(target=process)
    second = Thread(target=process)
    first.start()
    assert client.started.wait(timeout=5)
    second.start()
    client.release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not errors
    assert len(results) == 2
    assert results[0].message.id == results[1].message.id
    assert len(client.prompts) == 2


def test_repositorio_compartido_serializa_el_turno_antes_del_grafo():
    repository = SharedConnectionRepository()
    client = BlockingGemini()
    service = _service(repository, client)  # type: ignore[arg-type]
    request = UserTurnRequest("web_widget", "visitor-1", "req-1", "Hola")
    results = []

    first = Thread(target=lambda: results.append(service.process_user_turn(request)))
    second = Thread(target=lambda: results.append(service.process_user_turn(request)))
    first.start()
    assert client.started.wait(timeout=5)
    second.start()
    assert repository.second_thread_resolved.wait(timeout=5)
    assert len(client.prompts) == 1
    client.release.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(results) == 2
    assert len(client.prompts) == 2
    statements = repository._connection.statements
    assert sum("pg_advisory_lock" in statement for statement in statements) == 2
    assert sum("pg_advisory_unlock" in statement for statement in statements) == 2


def test_checkpoint_pendiente_no_se_recupera_como_respuesta_final():
    repository = MemoryRepository()
    saver = InMemorySaver()
    service = _service(repository, FakeGemini([]))
    service._checkpointer = saver
    thread = repository.get_or_create_thread("web_widget", "visitor-1")
    request = UserTurnRequest("web_widget", "visitor-1", "req-1", "Hola")
    turn_id = uuid5(thread.id, request.request_id)
    repository.store_message(
        thread_id=thread.id,
        turn_id=turn_id,
        role="user",
        content=request.content,
        raw_payload=service._user_payload(request),
        idempotency_key="user:req-1",
    )
    graph = build_agent_graph(checkpointer=saver)
    graph.invoke(
        service._state(request.content, False, str(turn_id)),
        {"configurable": {"thread_id": thread.langgraph_thread_id}, "recursion_limit": 4},
        interrupt_before=["router"],
    )

    with pytest.raises(TurnRecoveryRequiredError, match="no puede asociarse"):
        service.process_user_turn(request)


def test_reintento_recupera_checkpoint_si_falla_escritura_de_respuesta():
    repository = MemoryRepository(fail_first_agent_write=True)
    saver = InMemorySaver()
    client = FakeGemini([_result("reasoning"), _result("Respuesta recuperada", total_tokens=8)])
    service = ConversationService(repository, saver, gemini_client=client, recursion_limit=3)  # type: ignore[arg-type]
    request = UserTurnRequest("web_widget", "visitor-1", "req-1", "Hola")

    with pytest.raises(ConversationPersistenceError, match="write failed"):
        service.process_user_turn(request)

    recovered = service.process_user_turn(request)

    assert recovered.message.content == "Respuesta recuperada"
    assert recovered.replayed is True
    assert len(client.prompts) == 2
    assert [
        (item.role, item.content) for item in repository.list_messages(recovered.thread.id)
    ] == [
        ("user", "Hola"),
        ("agent", "Respuesta recuperada"),
    ]


def test_turnos_multiturno_comparten_memoria_y_canales_separan_hilos():
    repository = MemoryRepository()
    client = FakeGemini(
        [
            _result("reasoning"), _result("Te llamas Ana."),
            _result("reasoning"), _result("Sí, Ana."),
            _result("reasoning"), _result("Canal distinto."),
        ]
    )
    service = _service(repository, client)

    first = service.process_user_turn(
        UserTurnRequest("web_widget", "visitor-1", "req-1", "Me llamo Ana")
    )
    second = service.process_user_turn(
        UserTurnRequest("web_widget", "visitor-1", "req-2", "¿Cómo me llamo?")
    )
    other = service.process_user_turn(
        UserTurnRequest("whatsapp", "visitor-1", "req-1", "¿Cómo me llamo?")
    )

    assert first.thread.id == second.thread.id
    assert other.thread.id != first.thread.id
    assert "Me llamo Ana" in client.prompts[3]
    assert len(repository.list_messages(first.thread.id)) == 4
    assert len(repository.list_messages(other.thread.id)) == 2


def test_system_y_human_operator_se_persisten_expresamente():
    repository = MemoryRepository()
    service = _service(repository, FakeGemini([]))

    system = service.record_external_message(
        channel="meta",
        external_conversation_id="case-1",
        request_id="system-1",
        role="system",
        content="Horario de atención actualizado.",
    )
    operator = service.record_external_message(
        channel="meta",
        external_conversation_id="case-1",
        request_id="operator-1",
        role="human_operator",
        content="Un asesor tomará el caso.",
    )

    assert (system.role, operator.role) == ("system", "human_operator")
    assert system.thread_id == operator.thread_id
