"""Cliente reutilizable para las llamadas de texto a Gemini."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from google import genai
from google.genai import types

from app.integrations.settings import IntegrationsSettings

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
    """Adaptador sin estado para generación de texto mediante el SDK oficial."""

    def __init__(self, settings: IntegrationsSettings, model: str | None = None) -> None:
        if settings.gemini_api_key is None:
            raise GeminiNotConfigured("Falta la credencial de Gemini")
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._model = model or settings.gemini_primary_model
        self._fallback_model = settings.gemini_fallback_model
        self._fallback_enabled = settings.gemini_fallback_enabled
        self._fallback_max_attempts = settings.gemini_fallback_max_attempts
        self._fallback_input_token_limit = settings.gemini_fallback_input_token_limit
        self._fallback_max_output_tokens = settings.gemini_fallback_max_output_tokens

    def generate(self, prompt: str) -> GeminiResult:
        return self._generate_model(prompt, self._model)

    def generate_with_fallback(
        self,
        prompt: str,
        *,
        fallback_eligible: bool,
        estimated_input_tokens: int,
        fallback_attempted: bool = False,
    ) -> GeminiResult:
        attempts: list[dict[str, Any]] = []
        fallback_reason: str | None = None
        try:
            primary = self._generate_model(prompt, self._model)
            if self._is_explicitly_blocked(primary):
                attempts.append(self._attempt_payload(primary.raw_payload, "blocked"))
                raise GeminiGenerationError(
                    self._audit_payload(
                        attempts,
                        requested_model=self._model,
                        used_model=primary.raw_payload["model"],
                        fallback_reason="safety_blocked",
                    )
                )
            if self._is_usable(primary):
                attempts.append(self._attempt_payload(primary.raw_payload, "success"))
                return GeminiResult(
                    text=primary.text,
                    raw_payload=self._audit_payload(
                        attempts,
                        requested_model=self._model,
                        used_model=primary.raw_payload["model"],
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
            attempts.append(self._error_attempt(self._model, error))
            if not self._is_recoverable(error):
                raise GeminiGenerationError(
                    self._audit_payload(
                        attempts,
                        requested_model=self._model,
                        used_model=None,
                        fallback_reason="primary_error_nonrecoverable",
                    )
                ) from error
            fallback_reason = "primary_error_recoverable"

        if not self._can_fallback(
            fallback_eligible, fallback_attempted, estimated_input_tokens
        ):
            raise GeminiGenerationError(
                self._audit_payload(
                    attempts,
                    requested_model=self._model,
                    used_model=None,
                    fallback_reason=fallback_reason,
                )
            )
        try:
            fallback = self._generate_model(
                prompt,
                self._fallback_model,
                max_output_tokens=self._fallback_max_output_tokens,
            )
            if not self._is_usable(fallback):
                attempts.append(self._attempt_payload(fallback.raw_payload, "unusable"))
                raise GeminiGenerationError(
                    self._audit_payload(
                        attempts,
                        requested_model=self._model,
                        used_model=None,
                        fallback_reason=fallback_reason,
                    )
                )
            attempts.append(self._attempt_payload(fallback.raw_payload, "success"))
        except GeminiGenerationError:
            raise
        except Exception as error:
            attempts.append(self._error_attempt(self._fallback_model, error))
            raise GeminiGenerationError(
                self._audit_payload(
                    attempts,
                    requested_model=self._model,
                    used_model=None,
                    fallback_reason=fallback_reason,
                )
            ) from error
        return GeminiResult(
            text=fallback.text,
            raw_payload=self._audit_payload(
                attempts,
                requested_model=self._model,
                used_model=fallback.raw_payload["model"],
                fallback_reason=fallback_reason,
            ),
        )

    def _generate_model(
        self, prompt: str, model: str, *, max_output_tokens: int | None = None
    ) -> GeminiResult:
        started = perf_counter()
        arguments: dict[str, Any] = {"model": model, "contents": prompt}
        if max_output_tokens is not None:
            arguments["config"] = types.GenerateContentConfig(
                max_output_tokens=max_output_tokens
            )
        response = self._client.models.generate_content(**arguments)
        latency_ms = round((perf_counter() - started) * 1000)
        usage = response.usage_metadata
        raw_payload = {
            "provider": "gemini",
            "model": getattr(response, "model_version", None) or model,
            "requested_model": model,
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "total_tokens": getattr(usage, "total_token_count", None),
            "thinking_tokens": getattr(usage, "thoughts_token_count", None),
            "cached_tokens": getattr(usage, "cached_content_token_count", None),
            "finish_reason": self._finish_reason(response),
            "prompt_block_reason": self._prompt_block_reason(response),
            "latency_ms": latency_ms,
        }
        return GeminiResult(text=response.text or "", raw_payload=raw_payload)

    def _can_fallback(
        self, fallback_eligible: bool, fallback_attempted: bool, estimated_input_tokens: int
    ) -> bool:
        return (
            fallback_eligible
            and not fallback_attempted
            and self._fallback_enabled
            and self._fallback_max_attempts == 1
            and estimated_input_tokens <= self._fallback_input_token_limit
        )

    @staticmethod
    def _is_usable(result: GeminiResult) -> bool:
        return bool(
            result.text.strip() and result.raw_payload.get("finish_reason") != "MAX_TOKENS"
        )

    @staticmethod
    def _is_explicitly_blocked(result: GeminiResult) -> bool:
        finish_reason = result.raw_payload.get("finish_reason")
        return (
            isinstance(finish_reason, str)
            and finish_reason.upper() in _SAFETY_FINISH_REASONS
        ) or result.raw_payload.get("prompt_block_reason") is not None

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
        return isinstance(code, int) and code in {408, 500, 502, 503, 504}

    @staticmethod
    def _attempt_payload(payload: dict[str, Any], outcome: str) -> dict[str, Any]:
        return {**payload, "outcome": outcome}

    @classmethod
    def _error_attempt(cls, model: str, error: Exception) -> dict[str, Any]:
        code = getattr(error, "code", None) or getattr(error, "status_code", None)
        return {
            "provider": "gemini",
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
    ) -> dict[str, Any]:
        def total(field: str) -> int | None:
            values = [item[field] for item in attempts if isinstance(item.get(field), int)]
            return sum(values) if values else None

        return {
            "provider": "gemini",
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
