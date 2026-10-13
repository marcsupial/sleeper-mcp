"""HTTP adapter enforcing the A08 fetch controls (D6).

Every wire attempt is counted. Redirects and transport-level retries are disabled; the only
retry is the single eligible core retry decided here.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

import anyio
import httpx

from sleeper_mcp.contract import FETCH_CONTROLS, FetchControls
from sleeper_mcp.json_values import JsonParseError, strict_json_loads
from sleeper_mcp.sleeper.observation import (
    RECORDED_HEADERS,
    Attempt,
    AttemptOutcome,
    Failure,
    FailureCause,
    SourceObservation,
)
from sleeper_mcp.sleeper.routes import CORE_ROUTES, RouteRequest

RETRYABLE_STATUSES = frozenset({408, 500, 502, 503, 504})


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await anyio.sleep(seconds)


def user_agent() -> str:
    try:
        return f"sleeper-mcp/{version('sleeper-mcp')}"
    except PackageNotFoundError:  # pragma: no cover - only when running from a bare checkout
        return "sleeper-mcp/unknown"


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Seconds to wait from a Retry-After header, or None if missing or invalid."""
    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return float(int(text))
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if moment.tzinfo is None:
        return None
    return max(0.0, (moment - now).total_seconds())


class ProcessLimiter:
    """Process-wide sliding window (60 attempts per minute) plus the shared 429 cooldown."""

    def __init__(self, clock: Clock, controls: FetchControls = FETCH_CONTROLS) -> None:
        self._clock = clock
        self._controls = controls
        self._attempts: deque[float] = deque()
        self._cooldown_until: float | None = None

    def _prune(self, now: float) -> None:
        window = self._controls.rate_window_seconds
        while self._attempts and now - self._attempts[0] >= window:
            self._attempts.popleft()

    def wait_needed(self) -> float:
        now = self._clock.monotonic()
        self._prune(now)
        wait = 0.0
        if len(self._attempts) >= self._controls.process_attempts_per_minute:
            wait = self._attempts[0] + self._controls.rate_window_seconds - now
        if self._cooldown_until is not None and self._cooldown_until > now:
            wait = max(wait, self._cooldown_until - now)
        return max(0.0, wait)

    def record(self) -> None:
        self._attempts.append(self._clock.monotonic())

    def start_cooldown(self, seconds: float) -> None:
        until = self._clock.monotonic() + seconds
        if self._cooldown_until is None or until > self._cooldown_until:
            self._cooldown_until = until

    @property
    def attempts_in_window(self) -> int:
        self._prune(self._clock.monotonic())
        return len(self._attempts)


class CallBudget:
    """Per-call deadline, attempt/route/retry budgets and in-flight limit."""

    def __init__(self, clock: Clock, controls: FetchControls = FETCH_CONTROLS) -> None:
        self.clock = clock
        self.controls = controls
        self.started = clock.monotonic()
        self.deadline = self.started + controls.call_deadline_seconds
        self.attempts = 0
        self.retries = 0
        self.routes: set[str] = set()
        self.in_flight = anyio.Semaphore(controls.max_in_flight)
        self.max_observed_in_flight = 0
        self._current_in_flight = 0

    def remaining(self) -> float:
        return self.deadline - self.clock.monotonic()

    def enter_flight(self) -> None:
        self._current_in_flight += 1
        self.max_observed_in_flight = max(self.max_observed_in_flight, self._current_in_flight)

    def leave_flight(self) -> None:
        self._current_in_flight -= 1


class Fetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        limiter: ProcessLimiter,
        clock: Clock,
        controls: FetchControls = FETCH_CONTROLS,
    ) -> None:
        self.client = client
        self.base_url = base_url
        self.limiter = limiter
        self.clock = clock
        self.controls = controls

    @staticmethod
    def build_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            follow_redirects=False,
            transport=httpx.AsyncHTTPTransport(retries=0),
            headers={"User-Agent": user_agent(), "Accept": "application/json"},
        )

    async def fetch(
        self, request: RouteRequest, budget: CallBudget, source_id: str
    ) -> SourceObservation:
        observation = SourceObservation(
            id=source_id,
            route=request.route,
            url=request.url(self.base_url),
            coordinate=dict(request.coordinate),
        )
        if request.route not in budget.routes:
            if len(budget.routes) >= self.controls.route_budget:
                observation.failure = Failure(
                    "attempt_budget_exhausted", "the per-call route budget is used", None
                )
                return observation
            budget.routes.add(request.route)
        core = request.route in CORE_ROUTES
        while True:
            retry_delay = await self._one_attempt(observation, budget, core)
            if retry_delay is None:
                return observation
            await self.clock.sleep(retry_delay)

    async def _one_attempt(
        self, observation: SourceObservation, budget: CallBudget, core: bool
    ) -> float | None:
        """Make one attempt. Returns a delay before an eligible retry, or None when finished."""
        if budget.attempts >= self.controls.attempt_budget:
            observation.failure = Failure(
                "attempt_budget_exhausted", "the per-call attempt budget is used", None
            )
            return None
        remaining = budget.remaining()
        if remaining <= 0:
            observation.failure = Failure(
                "deadline_exceeded", "the call deadline passed before the attempt", None
            )
            return None

        acquired = False
        with anyio.move_on_after(remaining):
            await budget.in_flight.acquire()
            acquired = True
        if not acquired:
            observation.failure = Failure(
                "deadline_exceeded", "the call deadline passed while waiting to send", None
            )
            return None
        try:
            admitted = await self._admit(observation, budget)
            if not admitted:
                return None
            budget.attempts += 1
            budget.enter_flight()
            try:
                attempt, response_body, response = await self._send(observation, budget)
            finally:
                budget.leave_flight()
        finally:
            budget.in_flight.release()

        observation.attempts.append(attempt)
        observation.request_started_at = attempt.started_at
        observation.http_status = attempt.http_status
        if response is None:
            observation.body_completed_at = None
            observation.headers = None
            observation.body_sha256 = None
            observation.body_bytes = None
            observation.parse = "not_attempted"
            cause = _outcome_cause(attempt.outcome)
            observation.failure = Failure(cause, _describe_outcome(attempt.outcome), True)
            if core and self._retry_fits(budget, 0.0) and cause != "deadline_exceeded":
                budget.retries += 1
                return 0.0
            return None

        observation.body_completed_at = attempt.ended_at
        observation.headers = {name: response.headers.get(name) for name in RECORDED_HEADERS}
        observation.body_sha256 = hashlib.sha256(response_body).hexdigest()
        observation.body_bytes = len(response_body)
        status = response.status_code

        if status == 200:
            try:
                observation.body = strict_json_loads(response_body)
            except JsonParseError as exc:
                observation.parse = "failed"
                observation.body = None
                observation.failure = Failure(exc.cause, exc.detail, False)
                return None
            observation.parse = "parsed"
            observation.raw_body = response_body
            observation.failure = None
            return None

        observation.parse = "not_attempted"
        observation.body = None
        if status == 429:
            delay = parse_retry_after(attempt.retry_after, self.clock.now())
            cooldown = (
                delay
                if delay is not None
                else self.controls.http_429_missing_retry_after_cooldown_seconds
            )
            self.limiter.start_cooldown(cooldown)
            observation.failure = Failure(
                "rate_limited", f"HTTP 429; process cooldown {cooldown:g} s", True
            )
            return None
        if status == 304:
            cause = "bodyless_304" if not response_body else "http_status"
            observation.failure = Failure(cause, "HTTP 304 is not a successful read", False)
            return None
        if 300 <= status < 400:
            observation.failure = Failure(
                "unexpected_redirect", f"HTTP {status}; redirects are not followed", False
            )
            return None
        retryable = status in RETRYABLE_STATUSES
        observation.failure = Failure("http_status", f"HTTP {status}", retryable or status >= 500)
        if retryable and core:
            delay = parse_retry_after(attempt.retry_after, self.clock.now()) or 0.0
            if self._retry_fits(budget, delay):
                budget.retries += 1
                return delay
        return None

    def _retry_fits(self, budget: CallBudget, delay: float) -> bool:
        return (
            budget.retries < self.controls.eligible_core_retry_budget
            and budget.attempts < self.controls.attempt_budget
            and delay + self.controls.attempt_deadline_seconds <= budget.remaining()
        )

    async def _admit(self, observation: SourceObservation, budget: CallBudget) -> bool:
        """Wait for the process rate window and any 429 cooldown, within the call deadline."""
        while True:
            wait = self.limiter.wait_needed()
            if wait <= 0:
                self.limiter.record()
                return True
            if wait >= budget.remaining():
                observation.failure = Failure(
                    "rate_limited",
                    "the process rate window or 429 cooldown outlasts the call deadline",
                    True,
                )
                return False
            await self.clock.sleep(wait)

    async def _send(
        self, observation: SourceObservation, budget: CallBudget
    ) -> tuple[Attempt, bytes, httpx.Response | None]:
        controls = self.controls
        remaining = budget.remaining()
        limit = min(controls.attempt_deadline_seconds, remaining)
        clipped = remaining < controls.attempt_deadline_seconds
        phases = httpx.Timeout(
            connect=min(controls.connect_seconds, limit),
            read=min(controls.read_seconds, limit),
            write=min(controls.write_seconds, limit),
            pool=min(controls.pool_seconds, limit),
        )
        index = len(observation.attempts)
        started = self.clock.now()
        outcome: AttemptOutcome = "response"
        body = b""
        response: httpx.Response | None = None
        with anyio.move_on_after(limit) as scope:
            try:
                async with self.client.stream("GET", observation.url, timeout=phases) as streamed:
                    body = await streamed.aread()
                    response = streamed
            except httpx.TimeoutException:
                outcome = "timeout"
            except httpx.ConnectError:
                outcome = "connect_error"
            except httpx.HTTPError:
                outcome = "transport_error"
        if scope.cancelled_caught:
            outcome = "deadline_exceeded" if clipped else "timeout"
            response = None
            body = b""
        ended = self.clock.now()
        if response is None:
            return Attempt(index, started, ended, outcome), b"", None
        return (
            Attempt(
                index,
                started,
                ended,
                "response",
                http_status=response.status_code,
                retry_after=response.headers.get("retry-after"),
            ),
            body,
            response,
        )


def _describe_outcome(outcome: AttemptOutcome) -> str:
    return {
        "timeout": "the attempt timed out",
        "deadline_exceeded": "the call deadline ended the attempt",
        "connect_error": "the connection failed",
        "transport_error": "the transfer failed",
        "response": "a response was received",
    }[outcome]


def _outcome_cause(outcome: AttemptOutcome) -> FailureCause:
    if outcome == "response":  # pragma: no cover - a response is never a transport failure
        raise ValueError("a response is not a failure cause")
    return outcome
