"""What one source read observed: attempts, timing, headers, hashes and the parsed body."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sleeper_mcp.json_values import Json
from sleeper_mcp.sleeper.routes import Route

Origin = Literal["this_call", "local_reuse"]

AttemptOutcome = Literal[
    "response",
    "timeout",
    "deadline_exceeded",
    "connect_error",
    "transport_error",
]

FailureCause = Literal[
    "timeout",
    "deadline_exceeded",
    "attempt_budget_exhausted",
    "rate_limited",
    "http_status",
    "unexpected_redirect",
    "bodyless_304",
    "connect_error",
    "transport_error",
    "empty_body",
    "invalid_utf8",
    "invalid_json",
    "non_json_value",
    "duplicate_key",
    "directory_gate_used",
    "cache_unavailable",
]

ParseState = Literal["parsed", "failed", "not_attempted"]

RECORDED_HEADERS = (
    "cache-control",
    "age",
    "etag",
    "date",
    "vary",
    "cf-cache-status",
    "x-time",
    "content-encoding",
)


def utc_iso(moment: datetime) -> str:
    """UTC timestamp ending in Z, with microseconds."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_utc_iso(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


@dataclass
class Attempt:
    index: int
    started_at: datetime
    ended_at: datetime
    outcome: AttemptOutcome
    http_status: int | None = None
    retry_after: str | None = None


@dataclass
class Failure:
    cause: FailureCause
    detail: str
    retryable: bool | None


@dataclass
class SourceObservation:
    id: str
    route: Route
    url: str
    coordinate: dict[str, Json]
    origin: Origin = "this_call"
    attempts: list[Attempt] = field(default_factory=lambda: [])
    request_started_at: datetime | None = None
    body_completed_at: datetime | None = None
    http_status: int | None = None
    headers: dict[str, str | None] | None = None
    body_sha256: str | None = None
    body_bytes: int | None = None
    parse: ParseState = "not_attempted"
    body: Json = None
    failure: Failure | None = None
    raw_body: bytes | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        """True when a 200 response parsed as JSON. The body may still be JSON null."""
        return self.failure is None and self.parse == "parsed"

    @property
    def acquired_at(self) -> datetime | None:
        """Original acquisition time; reuse never resets it."""
        return self.request_started_at
