import json
import random
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest
from pydantic import BaseModel

from app.integrations.clients import Provider
from app.tools.audit import InMemoryAuditSink, ToolStatus
from app.tools.base import BaseTool, ToolError
from app.tools.retry import RetryPolicy, backoff_delay, parse_rate_limit

pytestmark = pytest.mark.anyio


class _Input(BaseModel):
    text: str = "x"


class FlakyTool(BaseTool):
    """Raises each item of `script` in order, then succeeds."""

    name = "flaky"
    description = "flaky"
    input_model = _Input
    provider = Provider.X

    def __init__(self, script, **kwargs):
        super().__init__(**kwargs)
        self.script = list(script)
        self.calls = 0

    async def execute(self, args):
        self.calls += 1
        if self.script:
            raise self.script.pop(0)
        return {"ok": True}


class CriticalFlaky(FlakyTool):
    name = "critical_flaky"
    critical = True


def _status_error(status, headers=None):
    request = httpx.Request("GET", "https://api.test")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("x", request=request, response=response)


def _make(cls, script, **kwargs):
    sink, sleeps = InMemoryAuditSink(), []

    async def sleep(seconds):
        sleeps.append(seconds)

    return cls(script, audit=sink, sleep=sleep, rng=random.Random(1), **kwargs), sink, sleeps


# --- pure helpers ---------------------------------------------------------------------


@pytest.mark.parametrize("attempt", [1, 2, 3, 4, 10])
def test_backoff_stays_within_bounds_and_grows(attempt):
    policy = RetryPolicy(base_delay=1.0, max_delay=8.0, jitter=0.5)
    ceiling = min(8.0, 2 ** (attempt - 1))

    for seed in range(20):
        delay = backoff_delay(attempt, policy, random.Random(seed))
        assert ceiling * 0.5 <= delay <= ceiling


def test_backoff_without_jitter_is_deterministic():
    policy = RetryPolicy(base_delay=1.0, max_delay=100.0, jitter=0.0)

    assert [backoff_delay(n, policy, random.Random(0)) for n in (1, 2, 3)] == [1.0, 2.0, 4.0]


def test_retry_after_seconds():
    info = parse_rate_limit({"Retry-After": "12"}, Provider.META)

    assert (info.limited, info.wait_seconds, info.source) == (True, 12.0, "retry_after")


def test_retry_after_http_date():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    later = datetime(2026, 1, 1, 12, 0, 30, tzinfo=UTC)

    headers = {"retry-after": format_datetime(later, usegmt=True)}

    info = parse_rate_limit(headers, None, now.timestamp())

    assert info.wait_seconds == pytest.approx(30.0)


def test_retry_after_garbage_is_ignored():
    assert parse_rate_limit({"retry-after": "soon"}, None).wait_seconds is None


def test_x_reset_header_gives_wait_until_epoch():
    headers = {"x-rate-limit-remaining": "0", "x-rate-limit-reset": "1060"}

    info = parse_rate_limit(headers, Provider.X, now=1000.0)

    assert (info.limited, info.wait_seconds, info.source) == (True, 60.0, "x_rate_limit")


def test_x_with_remaining_quota_is_not_limited():
    headers = {"x-rate-limit-remaining": "5", "x-rate-limit-reset": "1060"}

    assert not parse_rate_limit(headers, Provider.X, now=1000.0).limited


def test_meta_usage_at_100_percent_is_limited_without_wait():
    headers = {"X-App-Usage": json.dumps({"call_count": 100, "total_time": 40})}

    info = parse_rate_limit(headers, Provider.META)

    assert (info.limited, info.wait_seconds) == (True, None)


def test_meta_usage_below_100_is_not_limited():
    headers = {"X-App-Usage": json.dumps({"call_count": 40, "total_time": 99})}

    assert not parse_rate_limit(headers, Provider.META).limited


def test_whatsapp_business_usage_reads_regain_time_in_minutes():
    entry = {"type": "messaging", "call_count": 100, "estimated_time_to_regain_access": 2}
    usage = {"123": [entry]}
    headers = {"X-Business-Use-Case-Usage": json.dumps(usage)}

    info = parse_rate_limit(headers, Provider.WHATSAPP)

    assert (info.limited, info.wait_seconds) == (True, 120.0)


def test_malformed_usage_header_is_ignored():
    assert not parse_rate_limit({"X-App-Usage": "{not json"}, Provider.META).limited


# --- BaseTool integration -------------------------------------------------------------


async def test_429_then_success_waits_for_retry_after_and_audits_retries():
    tool, sink, sleeps = _make(FlakyTool, [_status_error(429, {"Retry-After": "3"})])

    result = await tool.run({})

    assert result.status == ToolStatus.EXECUTED
    assert result.data == {"ok": True}  # callers do not see the audit-only retries
    assert (tool.calls, sleeps) == (2, [3.0])
    [entry] = sink.entries
    expected = {"attempt": 1, "reason": "http_429", "wait_seconds": 3.0}
    assert entry.output_result["retries"] == [expected]
    assert entry.output_result["ok"] is True


async def test_header_wait_beats_computed_backoff():
    tool, _, sleeps = _make(FlakyTool, [_status_error(429, {"Retry-After": "7"})])

    await tool.run({})

    assert sleeps == [7.0]


async def test_exhausted_retries_fail_with_retry_history():
    tool, sink, sleeps = _make(FlakyTool, [_status_error(503)] * 5)

    result = await tool.run({})

    assert (result.status, result.error.code, result.error.retryable) == (
        ToolStatus.FAILED,
        "upstream_error",
        True,
    )
    assert tool.calls == 3 and len(sleeps) == 2
    [entry] = sink.entries
    assert entry.status == ToolStatus.FAILED
    assert [r["attempt"] for r in entry.output_result["retries"]] == [1, 2]
    assert entry.output_result["error"]["code"] == "upstream_error"


async def test_wait_longer_than_cap_stops_and_reports_wait():
    tool, sink, sleeps = _make(FlakyTool, [_status_error(429, {"Retry-After": "600"})])

    result = await tool.run({})

    assert (result.error.code, result.error.retryable) == ("upstream_error", True)
    assert "600" in result.error.message
    assert (tool.calls, sleeps) == (1, [])
    assert sink.entries[0].output_result["retry_after_seconds"] == 600.0


async def test_timeout_is_retried_for_safe_tool():
    tool, _, sleeps = _make(FlakyTool, [httpx.ReadTimeout("slow")])

    result = await tool.run({})

    assert result.status == ToolStatus.EXECUTED and len(sleeps) == 1


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_non_safe_tool_is_not_retried_on_5xx(status):
    tool, sink, sleeps = _make(CriticalFlaky, [_status_error(status)])

    result = await tool.run({}, approved=True)

    assert result.status == ToolStatus.FAILED
    assert (tool.calls, sleeps) == (1, [])
    assert "retries" not in sink.entries[0].output_result


async def test_non_safe_tool_is_not_retried_on_read_timeout():
    tool, _, _ = _make(CriticalFlaky, [httpx.ReadTimeout("slow")])

    result = await tool.run({}, approved=True)

    assert result.status == ToolStatus.FAILED and tool.calls == 1


async def test_non_safe_tool_is_retried_on_429():
    tool, _, sleeps = _make(CriticalFlaky, [_status_error(429)])

    result = await tool.run({}, approved=True)

    assert result.status == ToolStatus.EXECUTED
    assert tool.calls == 2 and len(sleeps) == 1


async def test_non_safe_tool_is_retried_when_connection_never_opened():
    tool, _, _ = _make(CriticalFlaky, [httpx.ConnectError("refused")])

    result = await tool.run({}, approved=True)

    assert result.status == ToolStatus.EXECUTED and tool.calls == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_other_4xx_are_never_retried(status):
    tool, _, sleeps = _make(FlakyTool, [_status_error(status)])

    result = await tool.run({})

    assert result.status == ToolStatus.FAILED and tool.calls == 1 and sleeps == []


async def test_tool_error_is_only_retried_when_flagged_retryable():
    plain, _, _ = _make(FlakyTool, [ToolError("busy", "ocupado")])
    flagged, _, sleeps = _make(FlakyTool, [ToolError("busy", "ocupado", retryable=True)])

    assert (await plain.run({})).status == ToolStatus.FAILED and plain.calls == 1
    assert (await flagged.run({})).status == ToolStatus.EXECUTED
    assert len(sleeps) == 1


async def test_retryable_tool_error_is_not_retried_on_non_safe_tool():
    tool, _, _ = _make(CriticalFlaky, [ToolError("busy", "ocupado", retryable=True)])

    result = await tool.run({}, approved=True)

    assert result.status == ToolStatus.FAILED and tool.calls == 1


async def test_not_configured_is_never_retried():
    tool, _, _ = _make(FlakyTool, [ToolError("not_configured", "falta", retryable=True)])

    assert (await tool.run({})).status == ToolStatus.FAILED and tool.calls == 1


async def test_retry_policy_override_limits_attempts():
    class OneShot(FlakyTool):
        retry_policy = RetryPolicy(max_attempts=1)

    tool, _, sleeps = _make(OneShot, [_status_error(503)])

    assert (await tool.run({})).status == ToolStatus.FAILED
    assert tool.calls == 1 and sleeps == []
