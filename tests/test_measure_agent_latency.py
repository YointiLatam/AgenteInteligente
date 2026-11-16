from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from scripts.measure_agent_latency import (
    MeasurementConfiguration,
    MeasurementConfigurationError,
    MeasurementSample,
    _environment,
    is_loopback_postgres_url,
    main,
    measurement_report,
    percentile,
    run_measurement,
    validate_configuration,
    validate_execution_environment,
)


def test_percentiles_interpolan_sobre_muestras_ordenadas():
    assert percentile([100, 0, 200, 300], 0.50) == 150
    assert percentile([100, 0, 200, 300], 0.95) == 285
    assert percentile([], 0.95) is None


@pytest.mark.parametrize(
    "database_url,expected",
    [
        ("postgresql://user:secret@localhost:5432/agente", True),
        ("postgresql://user:secret@127.0.0.1:5432/agente", True),
        ("postgresql://user:secret@db:5432/agente", False),
        ("postgresql://user:secret@project.neon.tech/agente", False),
        ("postgresql://user:secret@localhost:5432/agente?host=remote", False),
        ("postgresql://user:secret@localhost:5432/agente#remote", False),
        ("postgresql://user:secret@localhost,remote:5432/agente", False),
        ("postgresql://user:secret@localhost:99999/agente", False),
    ],
)
def test_solo_admite_postgres_loopback(database_url: str, expected: bool):
    assert is_loopback_postgres_url(database_url) is expected


def test_rechaza_objetivo_no_local_y_gemini_no_configurado():
    with pytest.raises(MeasurementConfigurationError):
        validate_execution_environment("postgresql://user:secret@host/agente", True)
    with pytest.raises(MeasurementConfigurationError):
        validate_execution_environment("postgresql://user:secret@localhost/agente", False)


@pytest.mark.parametrize(
    "configuration",
    [
        MeasurementConfiguration(concurrency=0),
        MeasurementConfiguration(concurrency=11),
        MeasurementConfiguration(requests_per_worker=6),
        MeasurementConfiguration(prompt="   "),
    ],
)
def test_rechaza_configuraciones_de_carga_peligrosas(configuration: MeasurementConfiguration):
    with pytest.raises(MeasurementConfigurationError):
        validate_configuration(configuration)


def test_reporte_calcula_latencia_solo_con_respuestas_completas():
    report = measurement_report(
        MeasurementConfiguration(prompt="secreto que no debe aparecer"),
        [
            MeasurementSample("completed", 100),
            MeasurementSample("completed", 300),
            MeasurementSample("error", 1_000, "RuntimeError"),
            MeasurementSample("pending_approval", 2_000),
        ],
        elapsed_seconds=2,
    )

    serialized = str(report)
    assert report["request_count"] == 4
    assert report["completed"] == 2
    assert report["pending_approval"] == 1
    assert report["errors"] == 1
    assert report["error_categories"] == ["RuntimeError"]
    assert report["latency_ms"] == {"p50": 200, "p95": 290, "p99": 298, "mean": 200}
    assert report["throughput_requests_per_second"] == 2
    assert report["throughput_scope"] == "all_terminal_requests_per_wall_clock_second"
    assert report["p95_target_met"] is False
    assert "secreto que no debe aparecer" not in serialized


def test_reporte_sin_respuestas_completas_no_publica_latencias_ni_objetivo():
    report = measurement_report(
        MeasurementConfiguration(),
        [
            MeasurementSample("error", 100, "RuntimeError"),
            MeasurementSample("pending_approval", 200),
        ],
        elapsed_seconds=1,
    )

    assert report["latency_ms"] == {"p50": None, "p95": None, "p99": None, "mean": None}
    assert report["p95_target_met"] is False
    assert report["errors"] == 1
    assert report["pending_approval"] == 1


def test_entorno_usa_la_configuracion_database_url_compartida_por_wiring(
    monkeypatch: pytest.MonkeyPatch,
):
    from app.agent import checkpointer
    from app.conversations import repository
    from app.knowledge.settings import KnowledgeSettings, get_knowledge_settings

    local_url = "postgresql://user:secret@localhost:5432/agente"
    assert repository.get_knowledge_settings is get_knowledge_settings
    assert checkpointer.get_knowledge_settings is get_knowledge_settings
    monkeypatch.setattr(
        "app.knowledge.settings.get_knowledge_settings",
        lambda: KnowledgeSettings(database_url=SecretStr(local_url)),
    )
    monkeypatch.setattr(
        "app.integrations.settings.get_integrations_settings",
        lambda: SimpleNamespace(gemini_api_key=SecretStr("configured")),
    )

    database_url, gemini_configured = _environment()

    assert database_url == local_url
    assert gemini_configured is True
    validate_execution_environment(database_url, gemini_configured)


def test_modo_validacion_no_abre_el_servicio(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        "scripts.measure_agent_latency.run_measurement",
        lambda *_: pytest.fail("No debe ejecutarse el servicio en modo validación"),
    )

    assert main([]) == 0
    assert "validation_only" in capsys.readouterr().out


def test_archivo_se_ejecuta_directamente_en_modo_seguro(tmp_path: Path):
    script = Path(__file__).parents[1] / "scripts" / "measure_agent_latency.py"

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "validation_only" in result.stdout


def test_medicion_registra_error_sin_incluir_el_prompt():
    @contextmanager
    def failing_service():
        raise RuntimeError("contenido sensible")
        yield

    report = run_measurement(
        failing_service,
        MeasurementConfiguration(prompt="contenido sensible", concurrency=1),
    )

    assert report["errors"] == 1
    assert "contenido sensible" not in str(report)
