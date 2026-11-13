from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ToolStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class AuditEntry:
    """Una ejecución de herramienta, tal como se guarda en `tool_audit_log`."""

    tool_name: str
    status: ToolStatus
    input_parameters: dict
    output_result: dict | None = None
    execution_time_ms: int | None = None
    thread_id: UUID | None = None
    approved_by: UUID | None = None


class AuditSink(Protocol):
    def record(self, entry: AuditEntry) -> None: ...


class NullAuditSink:
    def record(self, entry: AuditEntry) -> None:
        return None


@dataclass
class InMemoryAuditSink:
    """Para pruebas y desarrollo sin base de datos."""

    entries: list[AuditEntry] = field(default_factory=list)

    def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)
