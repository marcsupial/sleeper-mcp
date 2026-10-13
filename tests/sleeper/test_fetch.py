from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import replace

import anyio
import httpx
import pytest
import respx

from sleeper_mcp.contract import FETCH_CONTROLS, FetchControls
from sleeper_mcp.sleeper import routes
from sleeper_mcp.sleeper.fetch import (
    CallBudget,
    Clock,
    Fetcher,
    ProcessLimiter,
    SystemClock,
    parse_retry_after,
)
from sleeper_mcp.sleeper.routes import Route, RouteRequest

from ..helpers import BASE_URL, FakeClock

pytestmark = pytest.mark.anyio

USER_URL = f"{BASE_URL}/user/test-owner-1"


def make(
    clock: Clock | None = None, controls: FetchControls = FETCH_CONTROLS
) -> tuple[Fetcher, CallBudget]:
    clock = clock or FakeClock()
    fetcher = Fetcher(
        Fetcher.build_client(), BASE_URL, ProcessLimiter(clock, controls), clock, controls
    )
    return fetcher, CallBudget(clock, controls)


@respx.mock
async def test_success_records_headers_hash_and_times() -> None:
    body = b'{"user_id":"test-owner-1"}'
    route = respx.get(USER_URL).mock(
        return_value=httpx.Response(
            200,
            content=body,
            headers={"Cache-Control": "public, s-maxage=600", "Age": "12", "ETag": '"abc"'},
        )
    )
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.ok
    assert observation.body == {"user_id": "test-owner-1"}
    assert observation.http_status == 200
    assert observation.body_sha256 == hashlib.sha256(body).hexdigest()
    assert observation.body_bytes == len(body)
    assert observation.headers is not None
    assert observation.headers["cache-control"] == "public, s-maxage=600"
    assert observation.headers["age"] == "12"
    assert observation.headers["x-time"] is None
    assert observation.request_started_at is not None
    assert observation.body_completed_at is not None
    assert len(observation.attempts) == 1
    request = route.calls.last.request
    assert request.headers["user-agent"].startswith("sleeper-mcp/")
    assert "if-none-match" not in request.headers
    assert "if-modified-since" not in request.headers
    assert "authorization" not in request.headers


@respx.mock
async def test_successful_json_null_is_parsed_not_failed() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(200, content=b"null"))
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.ok
    assert observation.body is None


@pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
@respx.mock
async def test_eligible_statuses_get_one_retry(status: int) -> None:
    route = respx.get(USER_URL).mock(
        side_effect=[httpx.Response(status), httpx.Response(200, content=b"{}")]
    )
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.ok
    assert route.call_count == 2
    assert [a.http_status for a in observation.attempts] == [status, 200]


@respx.mock
async def test_second_eligible_failure_is_final() -> None:
    route = respx.get(USER_URL).mock(return_value=httpx.Response(503))
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert not observation.ok
    assert observation.failure is not None
    assert observation.failure.cause == "http_status"
    assert observation.failure.retryable is True
    assert route.call_count == 2


@respx.mock
async def test_only_one_core_retry_per_call() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(503))
    league_route = respx.get(f"{BASE_URL}/league/test-league-redraft").mock(
        return_value=httpx.Response(503)
    )
    fetcher, budget = make()
    await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    await fetcher.fetch(routes.league("test-league-redraft"), budget, "src-league")
    assert league_route.call_count == 1
    assert budget.attempts == 3


@pytest.mark.parametrize(
    ("status", "body", "cause"),
    [
        (404, b"", "http_status"),
        (400, b"", "http_status"),
        (200, b"{", "invalid_json"),
        (200, b"", "empty_body"),
        (200, b'{"a":1,"a":2}', "duplicate_key"),
        (200, b"[NaN]", "non_json_value"),
    ],
)
@respx.mock
async def test_permanent_failures_are_not_retried(status: int, body: bytes, cause: str) -> None:
    route = respx.get(USER_URL).mock(return_value=httpx.Response(status, content=body))
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.failure is not None
    assert observation.failure.cause == cause
    assert observation.failure.retryable is False
    assert route.call_count == 1
    if status == 200:
        assert observation.parse == "failed"
        assert observation.body_sha256 == hashlib.sha256(body).hexdigest()
        assert observation.body_bytes == len(body)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@respx.mock
async def test_redirects_are_not_followed(status: int) -> None:
    target = respx.get(f"{BASE_URL}/elsewhere").mock(return_value=httpx.Response(200))
    respx.get(USER_URL).mock(
        return_value=httpx.Response(status, headers={"Location": f"{BASE_URL}/elsewhere"})
    )
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.failure is not None
    assert observation.failure.cause == "unexpected_redirect"
    assert target.call_count == 0


@respx.mock
async def test_bodyless_304_is_a_failure_not_empty_data() -> None:
    respx.get(USER_URL).mock(return_value=httpx.Response(304))
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.failure is not None
    assert observation.failure.cause == "bodyless_304"
    assert observation.body is None
    assert observation.parse == "not_attempted"


@respx.mock
async def test_connect_error_gets_one_retry() -> None:
    route = respx.get(USER_URL).mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(200, content=b"{}")]
    )
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.ok
    assert route.call_count == 2
    assert observation.attempts[0].outcome == "connect_error"


@respx.mock
async def test_retry_after_is_respected_when_it_fits() -> None:
    clock = FakeClock()
    respx.get(USER_URL).mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "3"}),
            httpx.Response(200, content=b"{}"),
        ]
    )
    fetcher, budget = make(clock)
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.ok
    assert clock.sleeps == [3.0]


@respx.mock
async def test_retry_after_too_long_prevents_the_retry() -> None:
    route = respx.get(USER_URL).mock(
        return_value=httpx.Response(503, headers={"Retry-After": "11"})
    )
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert not observation.ok
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("header", "cooldown"),
    [
        ({"Retry-After": "30"}, 30.0),
        ({}, 60.0),
        ({"Retry-After": "soon"}, 60.0),
        ({"Retry-After": "Tue, 13 Oct 2026 16:00:45 GMT"}, 45.0),
        ({"Retry-After": "3600"}, 3600.0),
    ],
)
@respx.mock
async def test_429_ends_retries_and_sets_process_cooldown(
    header: dict[str, str], cooldown: float
) -> None:
    clock = FakeClock()
    route = respx.get(USER_URL).mock(return_value=httpx.Response(429, headers=header))
    fetcher, budget = make(clock)
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.failure is not None
    assert observation.failure.cause == "rate_limited"
    assert route.call_count == 1
    assert fetcher.limiter.wait_needed() == pytest.approx(cooldown)

    # A later caller in the same process is blocked while the cooldown outlasts its deadline.
    later = CallBudget(clock, FETCH_CONTROLS)
    blocked = await fetcher.fetch(routes.user("test-owner-1"), later, "src-user")
    if cooldown >= FETCH_CONTROLS.call_deadline_seconds:
        assert blocked.failure is not None
        assert blocked.failure.cause == "rate_limited"
        assert route.call_count == 1
    else:
        assert route.call_count == 2
        assert clock.sleeps[-1] == pytest.approx(cooldown)


@respx.mock
async def test_process_window_allows_sixty_attempts_per_minute_across_callers() -> None:
    clock = FakeClock()
    route = respx.get(USER_URL).mock(return_value=httpx.Response(200, content=b"{}"))
    fetcher, _ = make(clock)
    for _ in range(10):
        for _ in range(6):
            budget = CallBudget(clock, FETCH_CONTROLS)
            assert (await fetcher.fetch(routes.user("test-owner-1"), budget, "s")).ok
    assert route.call_count == 60
    assert fetcher.limiter.attempts_in_window == 60
    clock.advance(1)
    blocked = await fetcher.fetch(
        routes.user("test-owner-1"), CallBudget(clock, FETCH_CONTROLS), "s"
    )
    assert blocked.failure is not None
    assert blocked.failure.cause == "rate_limited"
    assert route.call_count == 60
    clock.advance(59)
    allowed = await fetcher.fetch(
        routes.user("test-owner-1"), CallBudget(clock, FETCH_CONTROLS), "s"
    )
    assert allowed.ok


@respx.mock
async def test_attempt_and_route_budgets() -> None:
    respx.get(url__regex=rf"{BASE_URL}/r/\d+").mock(return_value=httpx.Response(200, content=b"{}"))
    fetcher, budget = make()
    results = [
        await fetcher.fetch(RouteRequest(Route.USER, f"r/{i}"), budget, f"s{i}") for i in range(1)
    ]
    assert results[0].ok
    # Six distinct routes are allowed; a seventh is refused without a request.
    distinct = [Route.LEAGUE, Route.ROSTERS, Route.MATCHUPS, Route.STATE_NFL, Route.PLAYERS_NFL]
    for index, route in enumerate(distinct, start=1):
        assert (await fetcher.fetch(RouteRequest(route, f"r/{index}"), budget, "s")).ok
    extra = await fetcher.fetch(RouteRequest(Route("user"), "r/9"), budget, "s")
    assert extra.ok  # same route kind as the first, still within six routes
    assert budget.attempts == 7
    over = await fetcher.fetch(RouteRequest(Route.USER, "r/10"), budget, "s")
    assert over.failure is not None
    assert over.failure.cause == "attempt_budget_exhausted"
    assert budget.attempts == 7


async def test_route_budget_refuses_a_seventh_route() -> None:
    fetcher, budget = make()
    budget.routes.update({"a", "b", "c", "d", "e", "f"})
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "s")
    assert observation.failure is not None
    assert observation.failure.cause == "attempt_budget_exhausted"
    assert observation.attempts == []


@respx.mock
async def test_at_most_two_attempts_in_flight_per_call() -> None:
    active = 0
    peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await anyio.sleep(0.02)
        active -= 1
        return httpx.Response(200, content=b"{}")

    respx.get(url__regex=rf"{BASE_URL}/p/\d+").mock(side_effect=slow)
    fetcher, budget = make(SystemClock())
    async with anyio.create_task_group() as group:
        for index, route in enumerate([Route.USER, Route.LEAGUE, Route.ROSTERS, Route.STATE_NFL]):
            group.start_soon(fetcher.fetch, RouteRequest(route, f"p/{index}"), budget, f"s{index}")
    assert peak == 2
    assert budget.max_observed_in_flight == 2


FAST = replace(
    FETCH_CONTROLS,
    attempt_deadline_seconds=0.1,
    connect_seconds=0.1,
    read_seconds=0.1,
    write_seconds=0.1,
    pool_seconds=0.1,
)


@respx.mock
async def test_slow_body_is_stopped_by_the_attempt_deadline() -> None:
    async def trickle() -> AsyncIterator[bytes]:
        for _ in range(20):
            await anyio.sleep(0.02)  # each chunk arrives within the read timeout
            yield b" "

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=trickle())

    route = respx.get(USER_URL).mock(side_effect=respond)
    fetcher, budget = make(FakeClock(), FAST)
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.failure is not None
    assert observation.failure.cause == "timeout"
    assert [a.outcome for a in observation.attempts] == ["timeout", "timeout"]
    assert route.call_count == 2
    assert observation.body is None


@respx.mock
async def test_call_deadline_clips_the_attempt() -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(5)
        return httpx.Response(200, content=b"{}")

    respx.get(USER_URL).mock(side_effect=hang)
    controls = replace(FAST, call_deadline_seconds=0.05)
    fetcher, budget = make(SystemClock(), controls)
    with anyio.fail_after(1):
        observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.failure is not None
    assert observation.failure.cause == "deadline_exceeded"
    assert len(observation.attempts) == 1


async def test_no_attempt_after_the_call_deadline() -> None:
    clock = FakeClock()
    fetcher, budget = make(clock)
    clock.advance(15)
    observation = await fetcher.fetch(routes.user("test-owner-1"), budget, "src-user")
    assert observation.failure is not None
    assert observation.failure.cause == "deadline_exceeded"
    assert observation.attempts == []


@respx.mock
async def test_directory_route_is_never_retried() -> None:
    route = respx.get(f"{BASE_URL}/players/nfl").mock(return_value=httpx.Response(503))
    fetcher, budget = make()
    observation = await fetcher.fetch(routes.players_nfl(), budget, "src-players")
    assert not observation.ok
    assert route.call_count == 1


def test_parse_retry_after() -> None:
    now = FakeClock().now()
    assert parse_retry_after(None, now) is None
    assert parse_retry_after("7", now) == 7.0
    assert parse_retry_after("-1", now) is None
    assert parse_retry_after("Tue, 13 Oct 2026 15:00:00 GMT", now) == 0.0
    assert parse_retry_after("later", now) is None
