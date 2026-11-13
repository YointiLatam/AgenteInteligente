import logging
import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from app.integrations.clients import Provider, ProviderNotConfigured, build_client
from app.integrations.settings import IntegrationsSettings, get_integrations_settings
from app.tools.audit import AuditEntry, AuditSink, NullAuditSink, ToolStatus

logger = logging.getLogger(__name__)

# Claves del JSON Schema de Pydantic que el Function Calling de Gemini no acepta.
_UNSUPPORTED_KEYS = {"title", "default", "additionalProperties"}


class ToolError(Exception):
    """Error esperado de una herramienta; su mensaje es seguro de mostrar al agente."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


class ToolErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolResult(BaseModel):
    """Formato único de respuesta de toda herramienta."""

    status: ToolStatus
    data: dict | None = None
    error: ToolErrorInfo | None = None


def _gemini_schema(node: Any) -> Any:
    """Adapta el JSON Schema de Pydantic al subconjunto que entiende Gemini."""
    if isinstance(node, list):
        return [_gemini_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node or "$defs" in node:
        raise ValueError("El esquema de entrada no admite modelos anidados ($ref)")
    if "anyOf" in node:  # Optional[X] se emite como anyOf [X, null]
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        if len(options) != 1:
            raise ValueError("El esquema de entrada no admite uniones de tipos")
        node = {**{k: v for k, v in node.items() if k != "anyOf"}, **options[0]}
    return {k: _gemini_schema(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYS}


class BaseTool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]
    critical: ClassVar[bool] = False  # True: exige aprobación humana (HITL) antes de ejecutarse
    provider: ClassVar[Provider | None] = None  # API externa que consume, si usa alguna

    def __init__(
        self, audit: AuditSink | None = None, settings: IntegrationsSettings | None = None
    ) -> None:
        self._audit = audit or NullAuditSink()
        self._settings = settings

    @abstractmethod
    async def execute(self, args: Any) -> dict:
        """Lógica de la herramienta. `args` ya viene validado contra `input_model`."""

    @classmethod
    def declaration(cls) -> dict:
        """Declaración de función para el Function Calling de Gemini."""
        parameters = _gemini_schema(cls.input_model.model_json_schema())
        return {"name": cls.name, "description": cls.description, "parameters": parameters}

    def _settings_or_default(self) -> IntegrationsSettings:
        return self._settings or get_integrations_settings()

    def http_client(self) -> httpx.AsyncClient:
        """Cliente HTTP con credenciales leídas del entorno; nunca llegan como argumento."""
        if self.provider is None:
            raise RuntimeError(f"{self.name} no declara un proveedor externo")
        try:
            return build_client(self.provider, self._settings_or_default())
        except ProviderNotConfigured as exc:
            raise ToolError("not_configured", str(exc)) from exc

    async def run(
        self,
        raw_args: dict,
        *,
        thread_id: UUID | None = None,
        approved: bool = False,
        approved_by: UUID | None = None,
    ) -> ToolResult:
        """Valida, ejecuta y audita. Toda ejecución queda registrada, incluso las fallidas."""
        try:
            args = self.input_model.model_validate(raw_args)
        except ValidationError as exc:
            fields = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            error = ToolError("invalid_arguments", f"Argumentos inválidos: {fields}")
            return self._failed(error, raw_args, thread_id)

        if self.critical and not approved:
            self._record(ToolStatus.PENDING_APPROVAL, raw_args, thread_id=thread_id)
            return ToolResult(status=ToolStatus.PENDING_APPROVAL)

        started = time.perf_counter()
        try:
            data = await self.execute(args)
        except ToolError as error:
            return self._failed(error, raw_args, thread_id, started, approved_by)
        except httpx.HTTPError as exc:
            logger.warning("Falló la API externa en %s: %s", self.name, exc)
            retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code in (408, 429) or exc.response.status_code >= 500
            )
            error = ToolError("upstream_error", "Falló la llamada a la API externa", retryable)
            return self._failed(error, raw_args, thread_id, started, approved_by)
        except Exception:
            logger.exception("Error inesperado en la herramienta %s", self.name)
            error = ToolError("internal_error", "Error interno de la herramienta")
            return self._failed(error, raw_args, thread_id, started, approved_by)

        self._record(ToolStatus.EXECUTED, raw_args, data, started, thread_id, approved_by)
        return ToolResult(status=ToolStatus.EXECUTED, data=data)

    def _failed(
        self,
        error: ToolError,
        raw_args: dict,
        thread_id: UUID | None,
        started: float | None = None,
        approved_by: UUID | None = None,
    ) -> ToolResult:
        info = ToolErrorInfo(code=error.code, message=error.message, retryable=error.retryable)
        self._record(
            ToolStatus.FAILED,
            raw_args,
            {"error": info.model_dump()},
            started,
            thread_id,
            approved_by,
        )
        return ToolResult(status=ToolStatus.FAILED, error=info)

    def _record(
        self,
        status: ToolStatus,
        raw_args: dict,
        output: dict | None = None,
        started: float | None = None,
        thread_id: UUID | None = None,
        approved_by: UUID | None = None,
    ) -> None:
        elapsed = None if started is None else round((time.perf_counter() - started) * 1000)
        entry = AuditEntry(self.name, status, raw_args, output, elapsed, thread_id, approved_by)
        try:
            self._audit.record(entry)
        except Exception:
            # Un fallo de auditoría no debe tumbar la conversación, pero tiene que verse.
            logger.exception("No se pudo auditar la ejecución de %s", self.name)
