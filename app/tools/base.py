import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from app.integrations.clients import Provider, ProviderNotConfigured, build_client
from app.integrations.settings import IntegrationsSettings, get_integrations_settings
from app.tools.audit import AuditEntry, AuditSink, NullAuditSink, ToolStatus
from app.tools.retry import RetryPolicy, SupportsRandom, backoff_delay, parse_rate_limit

logger = logging.getLogger(__name__)

# Claves del JSON Schema de Pydantic que el Function Calling de Gemini no acepta.
_UNSUPPORTED_KEYS = {"title", "default", "additionalProperties"}


class ToolError(Exception):
    """Error esperado de una herramienta; su mensaje es seguro de mostrar al agente."""

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.retry_after = retry_after  # seconds the upstream asked us to wait, if known


_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
_NEVER_RETRY_CODES = frozenset({"invalid_arguments", "not_configured"})
# Failures where the request provably never reached the server.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


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
    timeout: ClassVar[httpx.Timeout | None] = None  # None: el predeterminado del proveedor
    # None means "not critical": critical tools (publish/send) are not safe to repeat once the
    # request may have reached the server.
    retry_safe: ClassVar[bool | None] = None
    retry_policy: ClassVar[RetryPolicy] = RetryPolicy()

    def __init__(
        self,
        audit: AuditSink | None = None,
        settings: IntegrationsSettings | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: SupportsRandom | None = None,
    ) -> None:
        self._audit = audit or NullAuditSink()
        self._settings = settings
        self._sleep = sleep
        self._rng = rng or random.Random()

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
            settings = self._settings_or_default()
            if self.timeout is None:
                return build_client(self.provider, settings)
            return build_client(self.provider, settings, self.timeout)
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
        retries: list[dict] = []
        attempt = 0
        while True:
            attempt += 1
            try:
                data = await self.execute(args)
            except ToolError as error:
                wait = self._error_wait(error, attempt)
                if wait is None:
                    return self._failed(error, raw_args, thread_id, started, approved_by, retries)
                await self._wait_and_log(retries, attempt, f"tool_error_{error.code}", wait)
                continue
            except httpx.HTTPError as exc:
                logger.warning("Falló la API externa en %s: %s", self.name, exc)
                reason, wait = self._http_wait(exc, attempt)
                if wait is None:
                    error = self._upstream_error(exc, wait_limit=reason)
                    return self._failed(error, raw_args, thread_id, started, approved_by, retries)
                await self._wait_and_log(retries, attempt, reason, wait)
                continue
            except Exception:
                logger.exception("Error inesperado en la herramienta %s", self.name)
                error = ToolError("internal_error", "Error interno de la herramienta")
                return self._failed(error, raw_args, thread_id, started, approved_by, retries)
            break

        output = {**data, "retries": retries} if retries else data
        self._record(ToolStatus.EXECUTED, raw_args, output, started, thread_id, approved_by)
        return ToolResult(status=ToolStatus.EXECUTED, data=data)

    def _is_retry_safe(self) -> bool:
        return (not self.critical) if self.retry_safe is None else self.retry_safe

    def _error_wait(self, error: ToolError, attempt: int) -> float | None:
        """Seconds to wait before retrying a ToolError, or None to give up."""
        if (
            not error.retryable
            or error.code in _NEVER_RETRY_CODES
            or not self._is_retry_safe()
            or attempt >= self.retry_policy.max_attempts
        ):
            return None
        return backoff_delay(attempt, self.retry_policy, self._rng)

    def _http_wait(self, exc: httpx.HTTPError, attempt: int) -> tuple[str, float | None]:
        """Return (reason, wait). wait is None when the call must not (or cannot) be retried.

        When a header-provided wait exceeds the policy cap, the reason is "wait_too_long".
        """
        policy = self.retry_policy
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            reason = f"http_{status}"
            if status == 429:  # rejected before processing: safe for every tool
                info = parse_rate_limit(exc.response.headers, self.provider)
                if info.wait_seconds is not None and info.wait_seconds > policy.max_wait:
                    return "wait_too_long", None
                if attempt >= policy.max_attempts:
                    return reason, None
                if info.wait_seconds is not None:
                    return reason, info.wait_seconds
                return reason, backoff_delay(attempt, policy, self._rng)
            retryable = status in _RETRYABLE_STATUS or status >= 500
            if status not in _RETRYABLE_STATUS:
                retryable = retryable and self._is_retry_safe()  # other 5xx: safe tools only
            elif not self._is_retry_safe():
                retryable = False  # the server may have processed it
        elif isinstance(exc, httpx.TimeoutException | httpx.ConnectError):
            reason = "connect_error" if isinstance(exc, _NOT_SENT) else "timeout"
            retryable = isinstance(exc, _NOT_SENT) or self._is_retry_safe()
        else:
            return "not_retryable", None
        if not retryable or attempt >= policy.max_attempts:
            return reason, None
        return reason, backoff_delay(attempt, policy, self._rng)

    async def _wait_and_log(
        self, retries: list[dict], attempt: int, reason: str, wait: float
    ) -> None:
        retries.append({"attempt": attempt, "reason": reason, "wait_seconds": round(wait, 3)})
        logger.info("Reintentando %s (intento %d, %s) en %.2fs", self.name, attempt, reason, wait)
        await self._sleep(wait)

    def _upstream_error(self, exc: httpx.HTTPError, wait_limit: str) -> ToolError:
        if wait_limit == "wait_too_long" and isinstance(exc, httpx.HTTPStatusError):
            info = parse_rate_limit(exc.response.headers, self.provider)
            seconds = info.wait_seconds or 0.0
            return ToolError(
                "upstream_error",
                f"La API externa limitó las solicitudes; reintenta en {round(seconds)} s",
                True,
                retry_after=seconds,
            )
        retryable = not isinstance(exc, httpx.HTTPStatusError) or (
            exc.response.status_code in (408, 429) or exc.response.status_code >= 500
        )
        return ToolError("upstream_error", "Falló la llamada a la API externa", retryable)

    def _failed(
        self,
        error: ToolError,
        raw_args: dict,
        thread_id: UUID | None,
        started: float | None = None,
        approved_by: UUID | None = None,
        retries: list[dict] | None = None,
    ) -> ToolResult:
        info = ToolErrorInfo(code=error.code, message=error.message, retryable=error.retryable)
        output: dict = {"error": info.model_dump()}
        if error.retry_after is not None:
            output["retry_after_seconds"] = round(error.retry_after, 3)
        if retries:
            output["retries"] = retries
        self._record(
            ToolStatus.FAILED,
            raw_args,
            output,
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
