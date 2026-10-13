"""Outcome combination (A10) and service-level edge cases through the orchestrator."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
import httpx
import pytest

from sleeper_mcp.contract import FETCH_CONTROLS
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.outcome import combine
from sleeper_mcp.lineup_context.service import Runtime
from sleeper_mcp.scope import NoRegistry
from sleeper_mcp.sleeper.directory import PlayerDirectoryStore
from sleeper_mcp.sleeper.fetch import Fetcher, ProcessLimiter, SystemClock

from .helpers import BASE_URL, FakeClock
from .world import (
    STARTERS,
    World,
    calls,
    league_body,
    make_runtime,
    matchup_row,
    other_row,
    profile,
    reasons,
    roster_row,
    run,
    state_body,
)

pytestmark = pytest.mark.anyio


def spec(severity: str, reason: str = "invalid_source_data") -> IssueSpec:
    return IssueSpec(reason=reason, severity=severity, explanation="x")  # pyright: ignore[reportArgumentType]


@pytest.mark.anyio(False)  # pyright: ignore[reportUntypedFunctionDecorator]
def test_combination_table() -> None:
    assert combine([], False) == "complete"
    assert combine([spec("notice")], False) == "complete"
    assert combine([spec("notice"), spec("limitation")], False) == "partial"
    assert combine([spec("limitation")], True) == "selection_required"
    assert combine([spec("error"), spec("limitation")], True) == "error"


# Scope without network ------------------------------------------------------------------------


async def test_absent_registry_is_unsupported_without_requests(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, registry=NoRegistry("not_configured"))
    result, routes = await run(World(), tmp_path, runtime=runtime)
    assert result.outcome == "error"
    assert reasons(result) == ["unsupported_scope"]
    assert result.issues[0].cause == "registry_absent"
    assert result.resolution_evidence.league.scope.registry == "absent"
    assert calls(routes) == {}


@pytest.mark.parametrize(
    ("league_id", "target"),
    [
        ("test-league-unknown", "not_registered"),
        ("test-league-lookalike-2024", "unsupported_comparison"),
    ],
)
async def test_unregistered_league_is_unsupported_without_requests(
    tmp_path: Path, league_id: str, target: str
) -> None:
    result, routes = await run(World(), tmp_path, {"league_id": league_id})
    assert reasons(result) == ["unsupported_scope"]
    assert result.issues[0].cause == "league_not_registered"
    assert result.resolution_evidence.league.scope.target == target
    assert calls(routes) == {}


# Identity and league ---------------------------------------------------------------------------


@pytest.mark.parametrize("user", [{"user_id": "someone-else"}, ["test-owner-1"], "test-owner-1"])
async def test_user_lookup_mismatch_is_invalid_source_data(tmp_path: Path, user: Any) -> None:
    world = World()
    world.overrides["user"] = httpx.Response(200, json=user)
    result, routes = await run(world, tmp_path)
    assert reasons(result) == ["invalid_source_data"]
    assert result.resolution_evidence.user.status == "invalid_source_data"
    assert "rosters" not in calls(routes)


async def test_league_null_and_mismatch(tmp_path: Path) -> None:
    world = World()
    world.overrides["league"] = httpx.Response(200, content=b"null")
    result, _ = await run(world, tmp_path / "a")
    assert reasons(result) == ["league_unresolved"]
    world.overrides["league"] = httpx.Response(200, json=league_body("test-league-other"))
    result, _ = await run(world, tmp_path / "b")
    assert reasons(result) == ["invalid_source_data"]


async def test_season_mismatch_is_invalid_input(tmp_path: Path) -> None:
    result, routes = await run(World(), tmp_path, {"season": "2025"})
    assert reasons(result) == ["invalid_input"]
    assert result.resolution_evidence.league.status == "invalid_input"
    assert "rosters" not in calls(routes)


async def test_best_ball_one_and_changed_configuration_are_unsupported(tmp_path: Path) -> None:
    world = World()
    world.league["settings"]["best_ball"] = 1
    result, _ = await run(world, tmp_path / "a")
    assert reasons(result) == ["unsupported_scope"]
    world = World()
    world.league = league_body(
        configuration=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX"]
    )
    result, _ = await run(world, tmp_path / "b")
    assert reasons(result) == ["unsupported_scope"]


@pytest.mark.parametrize("positions", ["omit", None])
async def test_missing_configuration_is_partial_slot_mapping(
    tmp_path: Path, positions: Any
) -> None:
    world = World()
    if positions == "omit":
        del world.league["roster_positions"]
    else:
        world.league["roster_positions"] = None
    result, _ = await run(world, tmp_path)
    assert result.outcome == "partial"
    assert "slot_mapping_unavailable" in reasons(result)
    assert result.context is not None
    weekly = result.context.weekly_lineup.data
    assert weekly.slots is None
    assert weekly.assignments is not None
    assert [a.raw for a in weekly.assignments] == STARTERS


def configuration_guard(result: Any) -> Any:
    guards = result.resolution_evidence.league.scope.guards
    return next(g for g in guards if g.name == "starting_configuration_matches")


@pytest.mark.parametrize("positions", [[], {}, "QB", ["QB", None], ["QB", 1, "BN"]])
async def test_malformed_configuration_is_an_error(tmp_path: Path, positions: Any) -> None:
    world = World()
    world.league["roster_positions"] = positions
    result, _ = await run(world, tmp_path)
    assert result.outcome == "error"
    assert reasons(result) == ["invalid_source_data"]
    assert result.context is None
    assert configuration_guard(result).result == "invalid"
    assert result.issues[0].retryable is None


async def test_bench_only_configuration_is_unsupported(tmp_path: Path) -> None:
    world = World()
    world.league["roster_positions"] = ["BN", "BN"]
    result, _ = await run(world, tmp_path)
    assert result.outcome == "error"
    assert reasons(result) == ["unsupported_scope"]
    assert configuration_guard(result).result == "fail"


async def test_previous_league_id_is_never_followed(tmp_path: Path) -> None:
    world = World()
    world.league["previous_league_id"] = "test-league-previous"
    result, routes = await run(world, tmp_path)
    assert result.outcome == "complete"
    assert not any(
        "previous" in str(r.calls.last.request.url) for r in routes.values() if r.call_count
    )


# Week -----------------------------------------------------------------------------------------


async def test_real_divergence_requires_explicit_week_but_keeps_roster(tmp_path: Path) -> None:
    world = World()
    world.league = league_body(leg=5)
    world.state = state_body(week=6, display_week=5)
    result, routes = await run(world, tmp_path)
    assert result.outcome == "partial"
    assert reasons(result) == ["week_resolution_required"]
    assert result.resolved.week is None
    assert result.context is not None
    assert result.context.roster.coverage.placement == "complete"
    assert result.context.weekly_lineup.data.row_state == "not_attempted"
    assert not any(name.startswith("matchups") for name in calls(routes))


async def test_explicit_week_skips_state_and_future_weeks_are_not_selected(
    tmp_path: Path,
) -> None:
    world = World()
    world.matchups[7] = [matchup_row()]
    result, routes = await run(world, tmp_path, {"week": 5})
    assert "state" not in calls(routes)
    assert calls(routes).get("matchups:5") == 1
    assert "matchups:7" not in calls(routes)
    assert result.resolved.week == 5


async def test_archived_target_without_week_never_reads_state(tmp_path: Path) -> None:
    world = World(league_id="test-league-kdef-2023")
    config = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]
    world.league = {
        **league_body("test-league-kdef-2023", config, season="2023"),
        "status": "complete",
    }
    world.rosters = [
        roster_row(2, league_id="test-league-kdef-2023", starters=[*STARTERS, "K1", "BUF"]),
        other_row(3, "test-league-kdef-2023"),
    ]
    world.matchups[5] = [matchup_row(2, starters=[*STARTERS, "K0", "HOU"])]
    world.players["HOU"] = {"team": "HOU", "position": "DEF", "fantasy_positions": ["DEF"]}
    world.players["K0"] = {"player_id": "K0", "position": "K", "fantasy_positions": ["K"]}
    world.players["K1"] = {"player_id": "K1", "position": "K", "fantasy_positions": ["K"]}
    world.players["BUF"] = {"team": "BUF", "position": "DEF", "fantasy_positions": ["DEF"]}
    result, routes = await run(world, tmp_path)
    assert result.outcome == "partial"
    assert reasons(result) == ["week_resolution_required"]
    assert "state" not in calls(routes)

    explicit, routes = await run(world, tmp_path / "b", {"week": 5})
    assert explicit.outcome == "complete"
    assert explicit.context is not None
    weekly = explicit.context.weekly_lineup
    assert weekly.data.mapping_basis == "retained_selected_league_configuration_observed_view"
    assert [s.label for s in weekly.data.slots or []][-2:] == ["K", "DEF"]
    states = explicit.context.view_comparison.membership_states
    assert states["K0"] == "absent" and states["HOU"] == "absent"
    assert "weekly_player_not_in_current_roster" in reasons(explicit)
    assert explicit.context.player_details.data["HOU"].applicability == "unknown"
    assert explicit.context.player_details.data["HOU"].roles == ["weekly_starter", "weekly_member"]


async def test_explicit_current_week_keeps_applicability_unknown(tmp_path: Path) -> None:
    # IMPL-B01: applicability follows how the week was chosen, not which week it is.
    world = World()
    world.players["P1"] = profile("P1", "QB", "SYNTHETIC_TOKEN")
    automatic, _ = await run(world, tmp_path)
    assert automatic.outcome == "partial"
    assert automatic.context is not None
    assert automatic.context.player_details.data["P1"].applicability == "current_observation"

    explicit, routes = await run(world, tmp_path / "b", {"week": 6})
    assert explicit.outcome == "complete"
    assert "state" not in calls(routes)
    assert explicit.context is not None
    details = explicit.context.player_details
    assert details.data["P1"].applicability == "unknown"
    assert "even when it equals the current week" in details.scope.applicability_basis


# Roster boundary (A10) ------------------------------------------------------------------------


@pytest.mark.parametrize("players", ["omit", None, ["P1", 5]])
async def test_unusable_players_keeps_weekly_facts(tmp_path: Path, players: Any) -> None:
    world = World()
    row = roster_row()
    if players == "omit":
        del row["players"]
    else:
        row["players"] = players
    world.rosters = [row, other_row()]
    result, _ = await run(world, tmp_path)
    assert result.outcome == "partial"
    assert reasons(result)[:2] == ["roster_data_unavailable", "invalid_source_data"]
    assert result.context is not None
    assert result.context.roster.data.members is None
    assert result.context.weekly_lineup.coverage.assignments == "complete"
    assert result.context.view_comparison.membership_states["P2"] == "unknown"


async def test_category_id_outside_players_makes_membership_partial(tmp_path: Path) -> None:
    # IMPL-B01: a current starter missing from players means players may be incomplete, so
    # no weekly starter outside players can be called absent.
    world = World()
    world.rosters[0]["players"].remove("P1")
    result, _ = await run(world, tmp_path)
    assert result.outcome == "partial"
    assert result.context is not None
    roster = result.context.roster
    assert roster.coverage.membership == "partial"
    assert roster.coverage.placement == "partial"
    assert {"invalid_source_data", "placement_conflict"} <= set(reasons(result))
    assert result.context.view_comparison.membership_states["P1"] == "unknown"
    assert "weekly_player_not_in_current_roster" not in reasons(result)


async def test_explicit_positive_with_sibling_defect_stays_complete(tmp_path: Path) -> None:
    world = World()
    world.rosters = [roster_row(), {**other_row(), "owner_id": 12345}]
    result, _ = await run(world, tmp_path, {"roster_id": 7})
    assert result.outcome == "complete"
    assert [(i.reason, i.severity) for i in result.issues] == [("invalid_source_data", "notice")]


async def test_notices_never_downgrade(tmp_path: Path) -> None:
    world = World()
    world.headers = {"Cache-Control": "public, s-maxage=60", "Age": "407"}
    result, _ = await run(world, tmp_path, {"week": 5})
    assert result.outcome == "complete"
    assert set(reasons(result)) == {"source_cache_stale", "current_weekly_assignment_difference"}
    assert all(i.severity == "notice" for i in result.issues)


async def test_rosters_source_failure_has_no_fallback(tmp_path: Path) -> None:
    world = World()
    world.overrides["rosters"] = httpx.Response(503)
    result, routes = await run(world, tmp_path, {"roster_id": 7, "week": 6})
    assert result.outcome == "error"
    assert reasons(result) == ["source_failure"]
    assert result.resolved.roster_id is None
    assert calls(routes)["rosters"] == 2
    assert "matchups:6" not in calls(routes)


# Directory and fetch budget -------------------------------------------------------------------


async def test_cold_directory_failure_then_gate_used(tmp_path: Path) -> None:
    clock = FakeClock()
    runtime = make_runtime(tmp_path, clock)
    world = World()
    world.overrides["players"] = httpx.Response(503)
    first, _ = await run(world, tmp_path, runtime=runtime)
    assert first.outcome == "partial"
    assert reasons(first) == ["player_details_unavailable", "source_failure"]
    second, routes = await run(World(), tmp_path, runtime=runtime)
    assert reasons(second) == ["player_details_unavailable"]
    assert second.issues[0].cause == "directory_gate_used"
    assert "players" not in calls(routes)
    assert second.context is not None
    assert second.context.roster.data.placements is not None


async def test_directory_reuse_is_reported_as_local_reuse(tmp_path: Path) -> None:
    clock = FakeClock()
    runtime = make_runtime(tmp_path, clock)
    await run(World(), tmp_path, runtime=runtime)
    clock.advance(3600)
    second, routes = await run(World(), tmp_path, runtime=runtime)
    assert "players" not in calls(routes)
    source = next(s for s in second.sources if s.id == "src-players")
    assert source.origin == "local_reuse"
    assert second.context is not None
    assert second.context.freshness.directory_reuse_age_seconds == pytest.approx(3600)


async def test_call_deadline_mid_roster_is_fatal(tmp_path: Path) -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(5)
        return httpx.Response(200, json=[])

    clock = SystemClock()
    controls = replace(FETCH_CONTROLS, call_deadline_seconds=0.3)
    fetcher = Fetcher(
        Fetcher.build_client(), BASE_URL, ProcessLimiter(clock, controls), clock, controls
    )
    base = make_runtime(tmp_path)
    runtime = Runtime(
        registry=base.registry,
        fetcher=fetcher,
        directory=PlayerDirectoryStore(tmp_path, fetcher, clock, controls),
        clock=clock,
        controls=controls,
    )
    world = World()
    world.overrides["rosters"] = hang
    with anyio.fail_after(2):
        result, _ = await run(world, tmp_path, runtime=runtime)
    assert result.outcome == "error"
    assert reasons(result) == ["source_failure"]
    assert result.issues[0].cause == "deadline_exceeded"
    assert result.context is None


async def test_six_routes_and_one_retry_use_exactly_seven_attempts(tmp_path: Path) -> None:
    world = World()
    responses = iter([httpx.Response(503), httpx.Response(200, json=world.user)])
    world.overrides["user"] = lambda request: next(responses)
    world.overrides["players"] = httpx.Response(503)
    result, routes = await run(world, tmp_path)
    assert calls(routes) == {
        "user": 2,
        "league": 1,
        "rosters": 1,
        "state": 1,
        "matchups:6": 1,
        "players": 1,
    }
    assert result.outcome == "partial"
    assert reasons(result) == ["player_details_unavailable", "source_failure"]
