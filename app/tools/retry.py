"""Retry policy, backoff and rate-limit header parsing for external API calls.

Pure helpers: no I/O and no sleeping, so they can be tested deterministically.
"""

import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Protocol

from app.integrations.clients import Provider


class SupportsRandom(Protocol):
    def random(self) -> float: ...


@dataclass(frozen=True)
class RetryPolicy:
    """How many times and how long to wait. `max_attempts` counts the first call."""

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.5  # fraction of the delay that is randomized (0 = none, 1 = full)
    max_wait: float = 60.0  # longest header-provided wait we are willing to honor


@dataclass(frozen=True)
class RateLimitInfo:
    limited: bool = False
    wait_seconds: float | None = None
    source: str | None = None


def backoff_delay(attempt: int, policy: RetryPolicy, rng: SupportsRandom) -> float:
    """Exponential delay after the `attempt`-th failure (1-based), with jitter.

    The result lies in `[ceiling * (1 - jitter), ceiling]` where
    `ceiling = min(max_delay, base_delay * 2 ** (attempt - 1))`.
    """
    ceiling = min(policy.max_delay, policy.base_delay * 2 ** max(attempt - 1, 0))
    return ceiling * (1 - policy.jitter) + ceiling * policy.jitter * rng.random()


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _retry_after(value: str, now: float) -> float | None:
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        seconds = moment.timestamp() - now
    return max(seconds, 0.0) if math.isfinite(seconds) else None


def _usage_entries(raw: str) -> list[dict]:
    """Flatten X-App-Usage (a dict) and X-Business-Use-Case-Usage ({id: [dict, ...]})."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, dict):
        return []
    if any(k in parsed for k in ("call_count", "total_time", "total_cputime")):
        return [parsed]
    entries: list[dict] = []
    for value in parsed.values():
        if isinstance(value, list):
            entries.extend(e for e in value if isinstance(e, dict))
        elif isinstance(value, dict):
            entries.append(value)
    return entries


def _meta_info(headers: dict[str, str]) -> RateLimitInfo:
    entries: list[dict] = []
    for name in ("x-business-use-case-usage", "x-app-usage"):
        if name in headers:
            entries.extend(_usage_entries(headers[name]))
    wait = 0.0
    limited = False
    for entry in entries:
        for key in ("call_count", "total_time", "total_cputime"):
            if isinstance(entry.get(key), int | float) and entry[key] >= 100:
                limited = True
        regain = entry.get("estimated_time_to_regain_access")  # minutes
        if isinstance(regain, int | float) and regain > 0:
            limited = True
            wait = max(wait, regain * 60.0)
    return RateLimitInfo(limited, wait or None, "meta_usage" if limited else None)


def _x_info(headers: dict[str, str], now: float) -> RateLimitInfo:
    reset, remaining = headers.get("x-rate-limit-reset"), headers.get("x-rate-limit-remaining")
    if reset is None:
        return RateLimitInfo()
    try:
        exhausted = remaining is None or int(remaining) <= 0
        wait = max(float(reset) - now, 0.0)
    except ValueError:
        return RateLimitInfo()
    if not exhausted:
        return RateLimitInfo()
    return RateLimitInfo(True, wait, "x_rate_limit")


def parse_rate_limit(
    headers: Mapping[str, str], provider: Provider | None, now: float | None = None
) -> RateLimitInfo:
    """Read each API's rate-limit headers. `Retry-After` always wins when present."""
    now = time.time() if now is None else now
    lowered = _lower(headers)
    if "retry-after" in lowered:
        wait = _retry_after(lowered["retry-after"], now)
        if wait is not None:
            return RateLimitInfo(True, wait, "retry_after")
    match provider:
        case Provider.META | Provider.WHATSAPP:
            return _meta_info(lowered)
        case Provider.X:
            return _x_info(lowered, now)
    return RateLimitInfo()
