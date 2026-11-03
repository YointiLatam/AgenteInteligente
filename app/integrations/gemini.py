"""Cliente reutilizable para las llamadas de texto a Gemini."""

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from google import genai

from app.integrations.settings import IntegrationsSettings

GEMINI_MODEL = "gemini-3.5-flash"


class GeminiNotConfigured(RuntimeError):
    """La clave de Gemini no está disponible en la configuración local."""


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

    def __init__(self, settings: IntegrationsSettings, model: str = GEMINI_MODEL) -> None:
        if settings.gemini_api_key is None:
            raise GeminiNotConfigured("Falta la credencial de Gemini")
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._model = model

    def generate(self, prompt: str) -> GeminiResult:
        started = perf_counter()
        response = self._client.models.generate_content(model=self._model, contents=prompt)
        latency_ms = round((perf_counter() - started) * 1000)
        usage = response.usage_metadata
        raw_payload = {
            "provider": "gemini",
            "model": self._model,
            "input_tokens": getattr(usage, "prompt_token_count", None),
            "output_tokens": getattr(usage, "candidates_token_count", None),
            "total_tokens": getattr(usage, "total_token_count", None),
            "latency_ms": latency_ms,
        }
        return GeminiResult(text=response.text or "", raw_payload=raw_payload)
