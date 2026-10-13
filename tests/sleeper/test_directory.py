from __future__ import annotations

import json
import os
from pathlib import Path

import anyio
import httpx
import pytest
import respx

from sleeper_mcp.contract import FETCH_CONTROLS
from sleeper_mcp.sleeper.directory import (
    BODY_FILE,
    GATE_FILE,
    META_FILE,
    CacheDirectives,
    PlayerDirectoryStore,
)
from sleeper_mcp.sleeper.fetch import CallBudget, Fetcher, ProcessLimiter

from ..helpers import BASE_URL, FakeClock

pytestmark = pytest.mark.anyio

PLAYERS_URL = f"{BASE_URL}/players/nfl"
DIRECTORY = {
    "4046": {"player_id": "4046", "full_name": "Patrick Mahomes", "fantasy_positions": ["QB"]},
    "JAX": {"team": "JAX", "position": "DEF", "fantasy_positions": ["DEF"]},
}
HEADERS = {"Cache-Control": "public, s-maxage=600, stale-while-revalidate=300", "Age": "254"}


def body() -> bytes:
    return json.dumps(DIRECTORY).encode()


def store_for(cache_dir: Path, clock: FakeClock) -> PlayerDirectoryStore:
    fetcher = Fetcher(Fetcher.build_client(), BASE_URL, ProcessLimiter(clock), clock)
    return PlayerDirectoryStore(cache_dir, fetcher, clock)


def budget(clock: FakeClock) -> CallBudget:
    return CallBudget(clock, FETCH_CONTROLS)


@respx.mock
async def test_cold_start_refreshes_and_writes_the_gate_first(tmp_path: Path) -> None:
    clock = FakeClock()
    gate_seen: list[bool] = []

    def respond(request: httpx.Request) -> httpx.Response:
        gate_seen.append((tmp_path / GATE_FILE).exists())
        return httpx.Response(200, content=body(), headers=HEADERS)

    route = respx.get(PLAYERS_URL).mock(side_effect=respond)
    store = store_for(tmp_path, clock)
    result = await store.get(budget(clock), "src-players")
    assert result.status == "refreshed"
    assert result.profiles == DIRECTORY
    assert result.observation is not None
    assert result.observation.origin == "this_call"
    assert gate_seen == [True]
    assert route.call_count == 1
    assert (tmp_path / BODY_FILE).read_bytes() == body()
    meta = json.loads((tmp_path / META_FILE).read_text())
    assert meta["format"] == "sleeper_mcp.directory_snapshot.v1"
    assert meta["headers"]["age"] == "254"


@respx.mock
async def test_reuse_under_24_hours_keeps_original_acquisition(tmp_path: Path) -> None:
    clock = FakeClock()
    route = respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, content=body(), headers=HEADERS)
    )
    first = await store_for(tmp_path, clock).get(budget(clock), "src-players")
    assert first.observation is not None
    acquired = first.observation.request_started_at

    clock.advance(86_399)
    # A new store instance stands in for a server restart.
    reused = await store_for(tmp_path, clock).get(budget(clock), "src-players")
    assert reused.status == "reused"
    assert route.call_count == 1
    assert reused.observation is not None
    assert reused.observation.origin == "local_reuse"
    assert reused.observation.request_started_at == acquired
    assert reused.observation.attempts == []
    assert reused.observation.body_sha256 == first.observation.body_sha256
    assert reused.reuse_age_seconds == pytest.approx(86_399)


@respx.mock
async def test_snapshot_expires_at_24_hours_and_the_gate_allows_one_refresh(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    route = respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, content=body(), headers=HEADERS)
    )
    store = store_for(tmp_path, clock)
    await store.get(budget(clock), "s")
    clock.advance(86_400)
    refreshed = await store.get(budget(clock), "s")
    assert refreshed.status == "refreshed"
    assert route.call_count == 2


@respx.mock
async def test_a_failed_attempt_uses_the_gate_across_restarts(tmp_path: Path) -> None:
    clock = FakeClock()
    route = respx.get(PLAYERS_URL).mock(return_value=httpx.Response(503))
    first = await store_for(tmp_path, clock).get(budget(clock), "s")
    assert first.status == "unavailable"
    assert first.cause == "http_status"
    assert route.call_count == 1  # the directory is never retried

    clock.advance(3600)
    second = await store_for(tmp_path, clock).get(budget(clock), "s")
    assert second.status == "unavailable"
    assert second.cause == "directory_gate_used"
    assert second.observation is None
    assert route.call_count == 1

    clock.advance(86_400 - 3600)
    route.mock(return_value=httpx.Response(200, content=body(), headers=HEADERS))
    third = await store_for(tmp_path, clock).get(budget(clock), "s")
    assert third.status == "refreshed"


@respx.mock
async def test_single_flight_per_process(tmp_path: Path) -> None:
    clock = FakeClock()

    async def slow(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(0.02)
        return httpx.Response(200, content=body(), headers=HEADERS)

    route = respx.get(PLAYERS_URL).mock(side_effect=slow)
    store = store_for(tmp_path, clock)
    results: list[str] = []

    async def get() -> None:
        results.append((await store.get(budget(clock), "s")).status)

    async with anyio.create_task_group() as group:
        group.start_soon(get)
        group.start_soon(get)
    assert route.call_count == 1
    assert sorted(results) == ["refreshed", "reused"]


@respx.mock
async def test_no_store_is_kept_in_memory_only(tmp_path: Path) -> None:
    clock = FakeClock()
    respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, content=body(), headers={"Cache-Control": "no-store"})
    )
    store = store_for(tmp_path, clock)
    result = await store.get(budget(clock), "s")
    assert result.status == "refreshed"
    assert not (tmp_path / BODY_FILE).exists()
    assert (await store.get(budget(clock), "s")).status == "reused"
    # After a restart nothing is on disk and the gate is used.
    restarted = await store_for(tmp_path, clock).get(budget(clock), "s")
    assert restarted.cause == "directory_gate_used"


@respx.mock
async def test_no_cache_requires_validation_which_uses_the_gate(tmp_path: Path) -> None:
    clock = FakeClock()
    route = respx.get(PLAYERS_URL).mock(
        return_value=httpx.Response(200, content=body(), headers={"Cache-Control": "no-cache"})
    )
    store = store_for(tmp_path, clock)
    assert (await store.get(budget(clock), "s")).status == "refreshed"
    again = await store.get(budget(clock), "s")
    assert again.status == "unavailable"
    assert again.cause == "directory_gate_used"
    assert route.call_count == 1


@respx.mock
async def test_corrupt_snapshot_is_ignored(tmp_path: Path) -> None:
    clock = FakeClock()
    respx.get(PLAYERS_URL).mock(return_value=httpx.Response(200, content=body(), headers=HEADERS))
    await store_for(tmp_path, clock).get(budget(clock), "s")
    (tmp_path / BODY_FILE).write_bytes(b'{"tampered": true}')
    clock.advance(10)
    result = await store_for(tmp_path, clock).get(budget(clock), "s")
    assert result.status == "unavailable"
    assert result.cause == "directory_gate_used"


async def test_corrupt_gate_counts_as_an_attempt_at_its_mtime(tmp_path: Path) -> None:
    clock = FakeClock()
    gate = tmp_path / GATE_FILE
    gate.write_text("not json")
    os.utime(gate, (clock.now().timestamp() - 60, clock.now().timestamp() - 60))
    with respx.mock:
        route = respx.get(PLAYERS_URL).mock(return_value=httpx.Response(200, content=body()))
        result = await store_for(tmp_path, clock).get(budget(clock), "s")
    assert result.cause == "directory_gate_used"
    assert route.call_count == 0


async def test_unwritable_cache_directory_means_no_attempt(tmp_path: Path) -> None:
    clock = FakeClock()
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    with respx.mock:
        route = respx.get(PLAYERS_URL).mock(return_value=httpx.Response(200, content=body()))
        result = await store_for(blocker / "cache", clock).get(budget(clock), "s")
    assert result.status == "unavailable"
    assert result.cause == "cache_unavailable"
    assert route.call_count == 0


@respx.mock
async def test_non_object_directory_is_invalid(tmp_path: Path) -> None:
    clock = FakeClock()
    respx.get(PLAYERS_URL).mock(return_value=httpx.Response(200, content=b"[]"))
    result = await store_for(tmp_path, clock).get(budget(clock), "s")
    assert result.status == "unavailable"
    assert result.cause == "invalid_source_data"


def test_cache_directives() -> None:
    parsed = CacheDirectives.parse("public, s-maxage=600, Max-Age=30, must-revalidate")
    assert parsed.max_age == 30
    assert parsed.must_revalidate
    assert not parsed.no_store
    assert CacheDirectives.parse(None) == CacheDirectives(False, False, False, None)
