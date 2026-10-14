"""Structural scope: unregistered leagues are checked against the live league's own settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sleeper_mcp.lineup_context.schema import Guard, LineupContextV1
from sleeper_mcp.scope import NoRegistry

from .world import (
    REDRAFT,
    STARTERS,
    World,
    calls,
    league_body,
    make_runtime,
    matchup_row,
    other_row,
    reasons,
    roster_row,
    run,
)

pytestmark = pytest.mark.anyio

UNLISTED = "test-league-unlisted"
KDEF = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]


def unlisted_world(configuration: list[str] | None = None, **league: Any) -> World:
    world = World(league_id=UNLISTED)
    world.league = {**league_body(UNLISTED, configuration), **league}
    world.rosters = [roster_row(league_id=UNLISTED), other_row(league_id=UNLISTED)]
    return world


def guard(result: LineupContextV1, name: str) -> Guard:
    return next(g for g in result.resolution_evidence.league.scope.guards if g.name == name)


@pytest.mark.parametrize("registry", ["configured", "absent"])
async def test_unregistered_league_is_served_structurally(tmp_path: Path, registry: str) -> None:
    runtime = make_runtime(
        tmp_path, registry=NoRegistry("not_configured") if registry == "absent" else None
    )
    result, _ = await run(unlisted_world(), tmp_path, runtime=runtime)
    assert result.outcome == "complete"
    scope = result.resolution_evidence.league.scope
    assert scope.registry == registry
    assert scope.target == "structural_match"
    assert scope.starting_configuration == REDRAFT
    assert scope.automatic_week == "agreeing_same_season_four_signals"
    assert scope.week_domain == [1, 18]
    for name in ("sport_supported", "season_type_supported", "starting_slots_supported"):
        assert guard(result, name).result == "pass"
    assert result.context is not None
    weekly = result.context.weekly_lineup.data
    assert weekly.mapping_basis == "observed_selected_configuration"
    assert [s.label for s in weekly.slots or []] == REDRAFT


async def test_registered_league_keeps_its_exact_pin(tmp_path: Path) -> None:
    world = World()
    world.league = league_body(configuration=[*REDRAFT, "FLEX"])
    result, routes = await run(world, tmp_path)
    assert reasons(result) == ["unsupported_scope"]
    assert result.resolution_evidence.league.scope.target == "registered"
    assert guard(result, "starting_configuration_matches").result == "fail"
    assert "rosters" not in calls(routes)


async def test_unknown_starting_slots_are_unsupported(tmp_path: Path) -> None:
    world = unlisted_world([*REDRAFT, "DL", "IDP_FLEX", "DL"])
    result, routes = await run(world, tmp_path)
    assert reasons(result) == ["unsupported_scope"]
    assert guard(result, "starting_slots_supported").result == "fail"
    assert "DL, IDP_FLEX." in result.issues[0].explanation
    assert result.resolution_evidence.league.scope.target is None
    assert "rosters" not in calls(routes)


@pytest.mark.parametrize(
    ("change", "guard_name"),
    [
        ({"season_type": "post"}, "season_type_supported"),
        ({"settings": {"best_ball": 1}}, "best_ball_integer_zero"),
    ],
)
async def test_unsupported_formats_are_unsupported(
    tmp_path: Path, change: dict[str, Any], guard_name: str
) -> None:
    result, _ = await run(unlisted_world(**change), tmp_path)
    assert reasons(result) == ["unsupported_scope"]
    assert guard(result, guard_name).result == "fail"


async def test_missing_configuration_cannot_confirm_structural_support(tmp_path: Path) -> None:
    world = unlisted_world()
    del world.league["roster_positions"]
    result, routes = await run(world, tmp_path)
    assert reasons(result) == ["invalid_source_data"]
    assert guard(result, "starting_slots_supported").result == "invalid"
    assert "rosters" not in calls(routes)


async def test_completed_league_uses_the_archive_rules(tmp_path: Path) -> None:
    world = unlisted_world(KDEF, season="2023", status="complete")
    world.rosters = [
        roster_row(league_id=UNLISTED, starters=[*STARTERS, "K1", "BUF"]),
        other_row(league_id=UNLISTED),
    ]
    world.matchups[5] = [matchup_row(starters=[*STARTERS, "K1", "BUF"])]
    world.players["K1"] = {"player_id": "K1", "position": "K", "fantasy_positions": ["K"]}
    world.players["BUF"] = {"team": "BUF", "position": "DEF", "fantasy_positions": ["DEF"]}

    result, routes = await run(world, tmp_path)
    assert reasons(result) == ["week_resolution_required"]
    assert result.resolution_evidence.league.scope.automatic_week == "explicit_week_only"
    assert "state" not in calls(routes)

    explicit, _ = await run(world, tmp_path / "b", {"week": 5})
    assert explicit.context is not None
    weekly = explicit.context.weekly_lineup.data
    assert weekly.mapping_basis == "retained_selected_league_configuration_observed_view"
    assert [s.label for s in weekly.slots or []][-2:] == ["K", "DEF"]


async def test_explicit_week_outside_the_domain_stops_before_requests(tmp_path: Path) -> None:
    result, routes = await run(unlisted_world(), tmp_path, {"week": 19})
    assert result.outcome == "error"
    assert calls(routes) == {}
