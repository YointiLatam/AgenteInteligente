"""Medición explícita y local de ConversationService, sin carga por defecto."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.conversations.service import (  # noqa: E402 - the script supports direct execution
    ConversationService,
    TurnResult,
    UserTurnRequest,
)

MAX_CONCURRENCY = 10
MAX_REQUESTS_PER_WORKER = 5
DEFAULT_PROMPT = "Consulta local autorizada para medir latencia."
Scenario = Literal["conversation_service_no_rag", "conversation_service_rag"]
ServiceOpener = Callable[[], AbstractContextManager[ConversationService]]


class MeasurementConfigurationError(RuntimeError):
    """La ejecución solicitada no cumple las protecciones del instrumento."""


@dataclass(frozen=True)
class MeasurementConfiguration:
    concurrency: int = 1
    requests_per_worker: int = 1
    retrieval_requested: bool = False
    prompt: str = DEFAULT_PROMPT

    @property
    def scenario(self) -> Scenario:
        if self.retrieval_requested:
            return "conversation_service_rag"
        return "conversation_service_no_rag"

    @property
    def request_count(self) -> int:
        return self.concurrency * self.requests_per_worker


@dataclass(frozen=True)
class MeasurementSample:
    status: Literal["completed", "pending_approval", "error"]
    duration_ms: float
    error_category: str | None = None


def is_loopback_postgres_url(database_url: str) -> bool:
    """Accept only an unambiguous local PostgreSQL connection URL."""
    try:
        parsed = urlsplit(database_url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme in {"postgres", "postgresql"}
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.path.startswith("/")
        and len(parsed.path) > 1
        and (port is None or 1 <= port <= 65535)
    )


def validate_configuration(configuration: MeasurementConfiguration) -> None:
    if not 1 <= configuration.concurrency <= MAX_CONCURRENCY:
        raise MeasurementConfigurationError(
            f"concurrency debe estar entre 1 y {MAX_CONCURRENCY}"
        )
    if not 1 <= configuration.requests_per_worker <= MAX_REQUESTS_PER_WORKER:
        raise MeasurementConfigurationError(
            f"requests_per_worker debe estar entre 1 y {MAX_REQUESTS_PER_WORKER}"
        )
    if configuration.request_count > MAX_CONCURRENCY * MAX_REQUESTS_PER_WORKER:
        raise MeasurementConfigurationError("La carga total supera el límite conservador")
    if not configuration.prompt.strip():
        raise MeasurementConfigurationError("prompt no puede estar vacío")


def validate_execution_environment(database_url: str | None, gemini_configured: bool) -> None:
    if not database_url or not is_loopback_postgres_url(database_url):
        raise MeasurementConfigurationError(
            "La medición real requiere DATABASE_URL de PostgreSQL en loopback local"
        )
    if not gemini_configured:
        raise MeasurementConfigurationError("La medición real requiere GEMINI_API_KEY configurada")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    if not 0 <= fraction <= 1:
        raise ValueError("fraction debe estar entre 0 y 1")
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def measurement_report(
    configuration: MeasurementConfiguration,
    samples: list[MeasurementSample],
    *,
    elapsed_seconds: float | None = None,
) -> dict[str, object]:
    completed_durations = [sample.duration_ms for sample in samples if sample.status == "completed"]
    completed = sum(sample.status == "completed" for sample in samples)
    pending = sum(sample.status == "pending_approval" for sample in samples)
    errors = sum(sample.status == "error" for sample in samples)
    p95 = percentile(completed_durations, 0.95)
    return {
        "scenario": configuration.scenario,
        "request_count": len(samples),
        "completed": completed,
        "pending_approval": pending,
        "errors": errors,
        "error_categories": sorted(
            {
                sample.error_category
                for sample in samples
                if sample.status == "error" and sample.error_category is not None
            }
        ),
        "latency_ms": {
            "p50": percentile(completed_durations, 0.50),
            "p95": p95,
            "p99": percentile(completed_durations, 0.99),
            "mean": mean(completed_durations) if completed_durations else None,
        },
        "p95_target_met": completed == len(samples) and p95 is not None and p95 < 2500,
        "throughput_requests_per_second": (
            len(samples) / elapsed_seconds if elapsed_seconds else None
        ),
        "throughput_scope": "all_terminal_requests_per_wall_clock_second",
    }


def _sample(
    service_opener: ServiceOpener,
    configuration: MeasurementConfiguration,
    run_id: str,
    sequence: int,
) -> MeasurementSample:
    started = perf_counter()
    try:
        with service_opener() as service:
            result = service.process_user_turn(
                UserTurnRequest(
                    channel="web_widget",
                    external_conversation_id=f"latency-{run_id}-{sequence}",
                    request_id=f"latency-{run_id}-{sequence}",
                    content=configuration.prompt,
                    retrieval_requested=configuration.retrieval_requested,
                )
            )
        status = "completed" if isinstance(result, TurnResult) else "pending_approval"
        return MeasurementSample(status=status, duration_ms=(perf_counter() - started) * 1000)
    except Exception as error:
        return MeasurementSample(
            status="error",
            duration_ms=(perf_counter() - started) * 1000,
            error_category=type(error).__name__,
        )


def run_measurement(
    service_opener: ServiceOpener, configuration: MeasurementConfiguration
) -> dict[str, object]:
    """Run a bounded batch; the report deliberately excludes user content and IDs."""
    validate_configuration(configuration)
    run_id = uuid4().hex
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=configuration.concurrency) as executor:
        futures = [
            executor.submit(_sample, service_opener, configuration, run_id, sequence)
            for sequence in range(configuration.request_count)
        ]
        samples = [future.result() for future in as_completed(futures)]
    return measurement_report(configuration, samples, elapsed_seconds=perf_counter() - started)


def _environment() -> tuple[str | None, bool]:
    from app.integrations.settings import get_integrations_settings
    from app.knowledge.settings import get_knowledge_settings

    knowledge = get_knowledge_settings()
    integrations = get_integrations_settings()
    database_url = (
        knowledge.database_url.get_secret_value() if knowledge.database_url is not None else None
    )
    return database_url, integrations.gemini_api_key is not None


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Permite tráfico local real.")
    parser.add_argument(
        "--confirm-local-agent-benchmark",
        action="store_true",
        help="Confirma costo Gemini y escrituras de conversaciones/checkpoints locales.",
    )
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--requests-per-worker", type=int, default=1)
    parser.add_argument("--retrieval", action="store_true", help="Solicita la ruta RAG real.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="No se incluye en el reporte.")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    args = parse_arguments(arguments)
    configuration = MeasurementConfiguration(
        concurrency=args.concurrency,
        requests_per_worker=args.requests_per_worker,
        retrieval_requested=args.retrieval,
        prompt=args.prompt,
    )
    validate_configuration(configuration)
    if not args.execute:
        print(json.dumps({"mode": "validation_only", "scenario": configuration.scenario}))
        return 0
    if not args.confirm_local_agent_benchmark:
        raise MeasurementConfigurationError(
            "Usa --confirm-local-agent-benchmark junto con --execute"
        )
    database_url, gemini_configured = _environment()
    validate_execution_environment(database_url, gemini_configured)
    from app.conversations.wiring import open_conversation_service

    print(json.dumps(run_measurement(open_conversation_service, configuration), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
