"""Adaptador opcional de Langfuse para trazas OpenTelemetry sanitizadas."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, Protocol

from langfuse import Langfuse

logger = logging.getLogger(__name__)

ObservationType = Literal["span", "generation", "tool", "retriever", "guardrail"]
_OBSERVATION_TYPES: frozenset[str] = frozenset(
    {"span", "generation", "tool", "retriever", "guardrail"}
)
_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "cached_tokens",
        "cost_available",
        "critical",
        "decision",
        "effect",
        "error_type",
        "estimated_cost_usd",
        "execution_status",
        "fallback_used",
        "finish_reason",
        "input_tokens",
        "latency_ms",
        "node",
        "outcome",
        "output_tokens",
        "prompt_block_reason",
        "provider",
        "requested_model",
        "route",
        "thinking_tokens",
        "thread_id",
        "tool_name",
        "tool_status",
        "total_tokens",
        "turn_id",
        "used_model",
    }
)
_USAGE_KEYS: frozenset[str] = frozenset({"input", "output", "total"})
_COST_KEYS: frozenset[str] = frozenset({"input", "output", "total"})


class Span(Protocol):
    def update(self, *, metadata: Mapping[str, object] | None = None, **kwargs: object) -> None: ...


class _NoopSpan:
    def update(self, *, metadata: Mapping[str, object] | None = None, **kwargs: object) -> None:
        return None


@dataclass(frozen=True)
class GeminiCost:
    input_per_million: float
    output_per_million: float


def _metadata(values: Mapping[str, object] | None) -> dict[str, object]:
    """Keep only scalar technical attributes with bounded names and values."""
    if not values:
        return {}
    safe: dict[str, object] = {}
    for key, value in values.items():
        if key not in _METADATA_KEYS:
            continue
        if isinstance(value, str):
            safe[key] = value[:200]
        elif isinstance(value, bool | int | float) or value is None:
            safe[key] = value
    return safe


def _usage(values: Mapping[str, object] | None) -> dict[str, int] | None:
    if not values:
        return None
    safe = {
        key: value
        for key, value in values.items()
        if key in _USAGE_KEYS
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }
    return safe or None


def _cost_details(values: Mapping[str, object] | None) -> dict[str, float] | None:
    if not values:
        return None
    safe = {
        key: float(value)
        for key, value in values.items()
        if key in _COST_KEYS
        and isinstance(value, int | float)
        and not isinstance(value, bool)
        and value >= 0
    }
    return safe or None


def _costs_from_environment(raw: str | None) -> dict[str, GeminiCost]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("OBSERVABILITY_GEMINI_PRICING_JSON no contiene JSON válido")
        return {}
    if not isinstance(parsed, dict):
        return {}
    costs: dict[str, GeminiCost] = {}
    for model, prices in parsed.items():
        if not isinstance(model, str) or not isinstance(prices, dict):
            continue
        input_price, output_price = (
            prices.get("input_per_million"),
            prices.get("output_per_million"),
        )
        if (
            isinstance(input_price, int | float)
            and not isinstance(input_price, bool)
            and input_price > 0
            and isinstance(output_price, int | float)
            and not isinstance(output_price, bool)
            and output_price > 0
        ):
            costs[model] = GeminiCost(float(input_price), float(output_price))
    return costs


class Telemetry:
    """Small, fail-open facade around Langfuse's OpenTelemetry-native SDK."""

    def __init__(
        self,
        client: Langfuse | None = None,
        *,
        enabled: bool = False,
        costs: Mapping[str, GeminiCost] | None = None,
    ) -> None:
        self._client = client
        self._enabled = enabled and client is not None
        self._costs = dict(costs or {})

    @classmethod
    def from_environment(cls) -> Telemetry:
        enabled = os.getenv("OBSERVABILITY_ENABLED", "false").lower() == "true"
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        if not enabled or not public_key or not secret_key:
            return cls()
        try:
            return cls(
                Langfuse(
                    public_key=public_key,
                    secret_key=secret_key,
                    base_url=os.getenv("LANGFUSE_BASE_URL") or None,
                    environment=os.getenv("OBSERVABILITY_ENVIRONMENT") or None,
                    tracing_enabled=True,
                ),
                enabled=True,
                costs=_costs_from_environment(os.getenv("OBSERVABILITY_GEMINI_PRICING_JSON")),
            )
        except Exception:
            logger.exception("No se pudo inicializar Langfuse; la telemetría queda deshabilitada")
            return cls()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @contextmanager
    def observation(
        self,
        name: str,
        *,
        as_type: ObservationType = "span",
        metadata: Mapping[str, object] | None = None,
        trace_seed: str | None = None,
        model: str | None = None,
    ) -> Iterator[Span]:
        if not self._enabled or self._client is None or as_type not in _OBSERVATION_TYPES:
            yield _NoopSpan()
            return
        try:
            trace_context = (
                {"trace_id": self._client.create_trace_id(seed=trace_seed)} if trace_seed else None
            )
            observation = self._client.start_as_current_observation(
                name=name,
                as_type=as_type,
                trace_context=trace_context,
                metadata=_metadata(metadata),
                model=model,
                input=None,
                output=None,
            )
        except Exception:
            logger.debug("La observabilidad falló; se conserva la ejecución", exc_info=True)
            yield _NoopSpan()
            return
        entered = False
        body_failed = False
        try:
            with observation as span:
                entered = True
                try:
                    yield span
                except Exception as error:
                    body_failed = True
                    self._update(span, {"outcome": "error", "error_type": type(error).__name__})
                    try:
                        span.update(level="ERROR", status_message=type(error).__name__)
                    except Exception:
                        logger.debug("No se pudo registrar el error de telemetría", exc_info=True)
                    raise
        except Exception:
            if body_failed:
                raise
            if not entered:
                logger.debug("La observabilidad falló; se conserva la ejecución", exc_info=True)
                yield _NoopSpan()
            else:
                logger.debug("No se pudo cerrar una observación", exc_info=True)

    def update(
        self,
        span: Span,
        metadata: Mapping[str, object] | None = None,
        *,
        model: str | None = None,
        usage_details: Mapping[str, object] | None = None,
        cost_details: Mapping[str, object] | None = None,
    ) -> None:
        kwargs: dict[str, object] = {}
        if isinstance(model, str):
            kwargs["model"] = model[:200]
        usage = _usage(usage_details)
        if usage is not None:
            kwargs["usage_details"] = usage
        costs = _cost_details(cost_details)
        if costs is not None:
            kwargs["cost_details"] = costs
        self._update(span, metadata, **kwargs)

    def _update(
        self, span: Span, metadata: Mapping[str, object] | None = None, **kwargs: object
    ) -> None:
        try:
            span.update(metadata=_metadata(metadata), **kwargs)
        except Exception:
            logger.debug("No se pudo actualizar una observación", exc_info=True)

    def gemini_cost(
        self, model: str | None, input_tokens: object, output_tokens: object
    ) -> tuple[dict[str, float] | None, dict[str, object]]:
        pricing = self._costs.get(model or "")
        if (
            pricing is None
            or not isinstance(input_tokens, int)
            or not isinstance(output_tokens, int)
        ):
            return None, {"cost_available": False}
        input_cost = input_tokens * pricing.input_per_million / 1_000_000
        output_cost = output_tokens * pricing.output_per_million / 1_000_000
        return (
            {"input": input_cost, "output": output_cost, "total": input_cost + output_cost},
            {"cost_available": True, "estimated_cost_usd": input_cost + output_cost},
        )


_telemetry: Telemetry | None = None


def get_telemetry() -> Telemetry:
    global _telemetry
    if _telemetry is None:
        _telemetry = Telemetry.from_environment()
    return _telemetry


def set_telemetry(telemetry: Telemetry | None) -> None:
    """Replace the singleton in tests; ``None`` restores lazy environment configuration."""
    global _telemetry
    _telemetry = telemetry
