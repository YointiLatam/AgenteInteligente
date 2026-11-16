"""Punto único de generación textual mediante LiteLLM."""

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from litellm import completion

from app.integrations.settings import IntegrationsSettings
from app.observability.telemetry import get_telemetry

GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_FALLBACK_MODEL = "gemini-3.8-flash"
_SAFETY_FINISH_REASONS = {
    "SAFETY",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
    "IMAGE_SAFETY",
    "MODEL_ARMOR",
    "JAILBREAK",
    "CONTENT_FILTER",
}
_TASK_SETTINGS = {
    "router": "llm_router_model",
    "memory": "llm_memory_model",
    "reasoning": "llm_reasoning_model",
    "judge": "llm_judge_model",
}


class GeminiNotConfigured(RuntimeError):
    """La clave de Gemini no está disponible en la configuración local."""


class GeminiGenerationError(RuntimeError):
    """La generación no produjo un resultado utilizable y conserva su auditoría."""

    def __init__(self, raw_payload: dict[str, Any]) -> None:
        super().__init__("La generación de Gemini no produjo una respuesta utilizable")
        self.raw_payload = raw_payload


@dataclass(frozen=True)
class GeminiResult:
    """Texto y metadatos seguros devueltos por una llamada a Gemini."""

    text: str
    raw_payload: dict[str, Any]


class TextGenerator(Protocol):
    """Contrato pequeño que permite sustituir Gemini por mocks en las pruebas."""

    def generate(self, prompt: str) -> GeminiResult: ...


class GeminiClient:
    """Adaptador LiteLLM; Gemini es la prioridad predeterminada."""

    def __init__(
        self,
        settings: IntegrationsSettings,
        model: str | None = None,
        *,
        completion_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._settings = settings
        self._completion = completion_fn or completion
        self._model = self._normalize_model(model or settings.gemini_primary_model)
        self._fallback_enabled = settings.gemini_fallback_enabled
        self._fallback_max_attempts = settings.gemini_fallback_max_attempts
        self._fallback_input_token_limit = settings.gemini_fallback_input_token_limit
        self._fallback_max_output_tokens = settings.gemini_fallback_max_output_tokens
        self._require_credential(self._model)

    def generate(self, prompt: str) -> GeminiResult:
        return self._generate_model(prompt, self._model)

    def generate_for_task(self, prompt: str, *, task: str) -> GeminiResult:
        return self._generate_model(prompt, self._task_model(task))

    def generate_with_fallback(
        self,
        prompt: str,
        *,
        fallback_eligible: bool,
        estimated_input_tokens: int,
        fallback_attempted: bool = False,
        task: str = "reasoning",
    ) -> GeminiResult:
        primary_model = self._task_model(task)
        attempts: list[dict[str, Any]] = []
        fallback_reason: str | None = None
        try:
            primary = self._generate_model(prompt, primary_model)
            if self._is_explicitly_blocked(primary):
                attempts.append(self._attempt_payload(primary.raw_payload, "blocked"))
                raise GeminiGenerationError(
                    self._audit_payload(
                        attempts,
                        requested_model=primary_model,
                        used_model=primary.raw_payload["model"],
                        used_provider=self._provider(primary_model),
                        fallback_reason="safety_blocked",
                    )
                )
            if self._is_usable(primary):
                attempts.append(self._attempt_payload(primary.raw_payload, "success"))
                return GeminiResult(
                    text=primary.text,
                    raw_payload=self._audit_payload(
                        attempts,
                        requested_model=primary_model,
                        used_model=primary.raw_payload["model"],
                        used_provider=self._provider(primary_model),
                        fallback_reason=None,
                    ),
                )
            attempts.append(self._attempt_payload(primary.raw_payload, "unusable"))
            fallback_reason = (
                "truncated_response"
                if primary.raw_payload.get("finish_reason") == "MAX_TOKENS"
                else "empty_response"
            )
        except GeminiGenerationError:
            raise
        except Exception as error:
            attempts.append(self._error_attempt(primary_model, error))
            if not self._is_recoverable(error):
                raise GeminiGenerationError(
                    self._audit_payload(
                        attempts,
                        requested_model=primary_model,
                        used_model=None,
                        fallback_reason="primary_error_nonrecoverable",
                    )
                ) from error
            fallback_reason = "primary_error_recoverable"

        fallback_model = self._fallback_model_for(primary_model)
        if not self._can_fallback(
            fallback_eligible, fallback_attempted, estimated_input_tokens, fallback_model
        ):
            raise GeminiGenerationError(
                self._audit_payload(
                    attempts,
                    requested_model=primary_model,
                    used_model=None,
                    fallback_reason=fallback_reason,
                )
            )
        assert fallback_model is not None
        try:
            fallback = self._generate_model(
                prompt,
                fallback_model,
                max_output_tokens=self._fallback_max_output_tokens,
                fallback_used=True,
            )
            if self._is_explicitly_blocked(fallback):
                attempts.append(self._attempt_payload(fallback.raw_payload, "blocked"))
                raise GeminiGenerationError(
                    self._audit_payload(
                        attempts,
                        requested_model=primary_model,
                        used_model=fallback.raw_payload["model"],
                        used_provider=self._provider(fallback_model),
                        fallback_reason="safety_blocked",
                    )
                )
            if not self._is_usable(fallback):
                attempts.append(self._attempt_payload(fallback.raw_payload, "unusable"))
                raise GeminiGenerationError(
                    self._audit_payload(
                        attempts,
                        requested_model=primary_model,
                        used_model=None,
                        fallback_reason=fallback_reason,
                    )
                )
            attempts.append(self._attempt_payload(fallback.raw_payload, "success"))
        except GeminiGenerationError:
            raise
        except Exception as error:
            attempts.append(self._error_attempt(fallback_model, error))
            raise GeminiGenerationError(
                self._audit_payload(
                    attempts,
                    requested_model=primary_model,
                    used_model=None,
                    fallback_reason=fallback_reason,
                )
            ) from error
        return GeminiResult(
            text=fallback.text,
            raw_payload=self._audit_payload(
                attempts,
                requested_model=primary_model,
                used_model=fallback.raw_payload["model"],
                used_provider=self._provider(fallback_model),
                fallback_reason=fallback_reason,
            ),
        )

    def _generate_model(
        self,
        prompt: str,
        model: str,
        *,
        max_output_tokens: int | None = None,
        fallback_used: bool = False,
    ) -> GeminiResult:
        provider = self._provider(model)
        api_key = self._require_credential(model)
        telemetry = get_telemetry()
        with telemetry.observation(
            "llm.generate",
            as_type="generation",
            metadata={
                "provider": provider,
                "requested_model": model,
                "fallback_used": fallback_used,
            },
            model=model,
        ) as span:
            started = perf_counter()
            arguments: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "api_key": api_key,
            }
            if max_output_tokens is not None:
                arguments["max_tokens"] = max_output_tokens
            response = self._completion(**arguments)
            latency_ms = round((perf_counter() - started) * 1000)
            raw_payload = self._response_payload(response, model, provider, latency_ms)
            result = GeminiResult(text=self._response_text(response), raw_payload=raw_payload)
            cost_details, cost_metadata = telemetry.gemini_cost(
                raw_payload["model"], raw_payload["input_tokens"], raw_payload["output_tokens"]
            )
            telemetry.update(
                span,
                {
                    "outcome": self._generation_outcome(result),
                    "provider": provider,
                    "fallback_used": fallback_used,
                    "used_model": raw_payload["model"],
                    "input_tokens": raw_payload["input_tokens"],
                    "output_tokens": raw_payload["output_tokens"],
                    "total_tokens": raw_payload["total_tokens"],
                    "latency_ms": latency_ms,
                    "finish_reason": raw_payload["finish_reason"],
                    "prompt_block_reason": raw_payload["prompt_block_reason"],
                    **cost_metadata,
                },
                model=raw_payload["model"],
                usage_details={
                    key: value
                    for key, value in {
                        "input": raw_payload["input_tokens"],
                        "output": raw_payload["output_tokens"],
                        "total": raw_payload["total_tokens"],
                    }.items()
                    if isinstance(value, int)
                },
                cost_details=cost_details,
            )
            return result

    def _task_model(self, task: str) -> str:
        field = _TASK_SETTINGS.get(task)
        configured = getattr(self._settings, field) if field else None
        model = self._normalize_model(configured) if configured else self._model
        self._require_credential(model)
        return model

    def _fallback_model_for(self, primary_model: str) -> str | None:
        configured = self._settings.llm_fallback_model
        candidate = self._normalize_model(configured) if configured else self._normalize_model(
            self._settings.gemini_fallback_model
        )
        if candidate == primary_model:
            return None
        try:
            self._require_credential(candidate)
        except GeminiNotConfigured:
            return None
        return candidate

    def _can_fallback(
        self,
        fallback_eligible: bool,
        fallback_attempted: bool,
        estimated_input_tokens: int,
        fallback_model: str | None,
    ) -> bool:
        return (
            fallback_eligible
            and not fallback_attempted
            and self._fallback_enabled
            and self._fallback_max_attempts == 1
            and estimated_input_tokens <= self._fallback_input_token_limit
            and fallback_model is not None
        )

    def _require_credential(self, model: str) -> str:
        secret = {
            "gemini": self._settings.gemini_api_key,
            "openai": self._settings.openai_api_key,
            "anthropic": self._settings.anthropic_api_key,
        }.get(self._provider(model))
        if secret is None:
            raise GeminiNotConfigured(f"Falta una credencial para {self._provider(model)}")
        return secret.get_secret_value()

    @staticmethod
    def _normalize_model(model: str) -> str:
        return model if "/" in model else f"gemini/{model}"

    @staticmethod
    def _provider(model: str) -> str:
        return model.partition("/")[0]

    @staticmethod
    def _value(value: Any, key: str, default: Any = None) -> Any:
        return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)

    @classmethod
    def _response_text(cls, response: Any) -> str:
        choices = cls._value(response, "choices", []) or []
        if not choices:
            legacy_text = cls._value(response, "text")
            return legacy_text if isinstance(legacy_text, str) else ""
        message = cls._value(choices[0], "message") if choices else None
        content = cls._value(message, "content")
        return content if isinstance(content, str) else ""

    @classmethod
    def _response_payload(
        cls, response: Any, requested_model: str, provider: str, latency_ms: int
    ) -> dict[str, Any]:
        choices = cls._value(response, "choices", []) or []
        choice = choices[0] if choices else None
        usage = cls._value(response, "usage") or cls._value(response, "usage_metadata")
        finish_reason = (
            cls._value(choice, "finish_reason")
            if choice is not None
            else cls._finish_reason(response)
        )
        feedback = cls._value(response, "prompt_feedback")
        block_reason = cls._value(feedback, "block_reason")
        prompt_block_reason = cls._value(block_reason, "value", block_reason)
        response_model = (
            cls._value(response, "model")
            or cls._value(response, "model_version")
            or requested_model
        )
        input_tokens = cls._value(
            usage, "prompt_tokens", cls._value(usage, "prompt_token_count")
        )
        output_tokens = cls._value(
            usage, "completion_tokens", cls._value(usage, "candidates_token_count")
        )
        total_tokens = cls._value(
            usage, "total_tokens", cls._value(usage, "total_token_count")
        )
        return {
            "provider": provider,
            "model": response_model,
            "requested_model": requested_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "finish_reason": str(finish_reason).upper() if finish_reason is not None else None,
            "prompt_block_reason": (
                str(prompt_block_reason) if prompt_block_reason is not None else None
            ),
            "latency_ms": latency_ms,
        }

    @staticmethod
    def _is_usable(result: GeminiResult) -> bool:
        return bool(result.text.strip() and result.raw_payload.get("finish_reason") != "MAX_TOKENS")

    @staticmethod
    def _is_explicitly_blocked(result: GeminiResult) -> bool:
        finish_reason = result.raw_payload.get("finish_reason")
        return (
            isinstance(finish_reason, str) and finish_reason.upper() in _SAFETY_FINISH_REASONS
        ) or result.raw_payload.get("prompt_block_reason") is not None

    @classmethod
    def _generation_outcome(cls, result: GeminiResult) -> str:
        if cls._is_explicitly_blocked(result):
            return "blocked"
        return "success" if cls._is_usable(result) else "unusable"

    @staticmethod
    def _finish_reason(response: Any) -> str | None:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return None
        reason = getattr(candidates[0], "finish_reason", None)
        return getattr(reason, "value", reason)

    @staticmethod
    def _prompt_block_reason(response: Any) -> str | None:
        feedback = getattr(response, "prompt_feedback", None)
        reason = getattr(feedback, "block_reason", None)
        value = getattr(reason, "value", reason)
        if isinstance(value, str):
            return value
        return None

    @staticmethod
    def _is_recoverable(error: Exception) -> bool:
        if isinstance(error, TimeoutError):
            return True
        code = getattr(error, "code", None) or getattr(error, "status_code", None)
        return isinstance(code, int) and code in {408, 429, 500, 502, 503, 504}

    @staticmethod
    def _attempt_payload(payload: dict[str, Any], outcome: str) -> dict[str, Any]:
        return {**payload, "outcome": outcome}

    @classmethod
    def _error_attempt(cls, model: str, error: Exception) -> dict[str, Any]:
        code = getattr(error, "code", None) or getattr(error, "status_code", None)
        return {
            "provider": GeminiClient._provider(model),
            "model": model,
            "requested_model": model,
            "error": cls._error_category(code, error),
            "outcome": "error",
        }

    @staticmethod
    def _error_category(code: object, error: Exception) -> str:
        if code == 429:
            return "rate_limited"
        if isinstance(code, int) and 500 <= code <= 599:
            return "server_error"
        if isinstance(error, TimeoutError) or code == 408:
            return "timeout"
        return "generation_error"

    @staticmethod
    def _audit_payload(
        attempts: list[dict[str, Any]],
        *,
        requested_model: str,
        used_model: str | None,
        fallback_reason: str | None,
        used_provider: str | None = None,
    ) -> dict[str, Any]:
        def total(field: str) -> int | None:
            values = [item[field] for item in attempts if isinstance(item.get(field), int)]
            return sum(values) if values else None

        return {
            "provider": used_provider or GeminiClient._provider(requested_model),
            "model": used_model or requested_model,
            "requested_model": requested_model,
            "used_model": used_model,
            "fallback_used": len(attempts) > 1,
            "fallback_reason": fallback_reason,
            "attempt_count": len(attempts),
            "attempts": attempts,
            "input_tokens": total("input_tokens"),
            "output_tokens": total("output_tokens"),
            "total_tokens": total("total_tokens"),
            "thinking_tokens": total("thinking_tokens"),
            "cached_tokens": total("cached_tokens"),
            "latency_ms": total("latency_ms"),
        }
