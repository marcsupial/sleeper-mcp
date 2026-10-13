"""Workflow-brief and A09/A10 scenarios, end to end through the MCP client with mocked HTTP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from mcp.client import Client
from mcp_types import TextContent

from sleeper_mcp.server import create_server

from .helpers import schema_errors
from .world import (
    LEAGUE,
    OTHER,
    USER,
    World,
    league_body,
    make_runtime,
    matchup_row,
    other_row,
    profile,
    roster_row,
)

pytestmark = pytest.mark.anyio


class Outcome:
    def __init__(self, obj: dict[str, Any], is_error: bool, routes: dict[str, respx.Route]):
        self.obj = obj
        self.is_error = is_error
        self.routes = routes

    @property
    def reasons(self) -> list[str]:
        return [i["reason"] for i in self.obj["issues"]]

    @property
    def context(self) -> dict[str, Any]:
        assert self.obj["context"] is not None
        return self.obj["context"]

    def called(self, name: str) -> int:
        return self.routes[name].call_count


async def invoke(world: World, tmp_path: Path, **arguments: Any) -> Outcome:
    server = create_server(make_runtime(tmp_path))
    args = {"user_id": USER, "league_id": world.league_id, **arguments}
    with respx.mock(assert_all_called=False) as router:
        routes = world.mount(router)
        async with Client(server) as client:
            tool = next(t for t in (await client.list_tools()).tools)
            result = await client.call_tool("get_lineup_context", args)
    obj = result.structured_content
    assert obj is not None
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert json.loads(block.text) == obj
    assert tool.output_schema is not None
    assert schema_errors(tool.output_schema, obj) == []
    assert result.is_error == (obj["outcome"] == "error")
    return Outcome(obj, result.is_error, routes)


# Workflow brief ------------------------------------------------------------------------------


async def test_return_a_roster_with_each_players_placement(tmp_path: Path) -> None:
    world = World()
    starters = ["P1", "0", "0", "0", "0", "0", "0", "0"]
    world.rosters = [
        {
            **roster_row(starters=starters),
            "players": ["P1", "P2", "P3", "P4"],
            "reserve": ["P3"],
            "taxi": ["P4"],
        },
        other_row(),
    ]
    world.matchups[6] = [matchup_row(starters=starters)]
    world.players = {pid: profile(pid, "QB") for pid in ("P1", "P2", "P3", "P4")}
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "complete"
    assert out.obj["resolved"]["league_id"] == LEAGUE
    assert out.obj["resolved"]["roster_id"] == 7
    roster = out.context["roster"]["data"]
    assert roster["placements"] == {"P1": "starter", "P2": "bench", "P3": "reserve", "P4": "taxi"}
    assert roster["members"] == ["P1", "P2", "P3", "P4"]


async def test_return_the_current_weeks_starting_lineup(tmp_path: Path) -> None:
    world = World()
    starters = ["P1", "P2", "0", "0", "0", "0", "P3", "0"]
    world.rosters = [roster_row(starters=starters), other_row()]
    world.matchups[6] = [matchup_row(starters=starters)]
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "complete"
    resolved = out.obj["resolved"]
    assert (resolved["league_id"], resolved["roster_id"], resolved["season"], resolved["week"]) == (
        LEAGUE,
        7,
        "2026",
        6,
    )
    assert out.obj["resolution_evidence"]["week"]["basis"] == "provider_observed_week"
    slots = out.context["weekly_lineup"]["data"]["slots"]
    occupied = [(s["label"], s["player_id"]) for s in slots if s["state"] == "occupied"]
    assert occupied == [("QB", "P1"), ("RB", "P2"), ("FLEX", "P3")]
    assert out.called("matchups:6") == 1
    assert out.called("matchups:5") == 0


async def test_report_that_the_user_has_no_roster_in_the_league(tmp_path: Path) -> None:
    world = World()
    world.rosters = [roster_row(owner=OTHER), other_row()]
    out = await invoke(world, tmp_path)
    assert out.is_error
    assert out.reasons == ["roster_not_found"]
    assert out.obj["context"] is None
    assert out.obj["resolved"]["roster_id"] is None
    assert out.called("matchups:6") == 0
    assert out.called("players") == 0


async def test_require_roster_selection_for_multiple_rosters(tmp_path: Path) -> None:
    world = World()
    world.rosters = [
        {**roster_row(7, owner=OTHER), "co_owners": [USER]},
        {**roster_row(8, owner="test-owner-3"), "co_owners": [USER]},
    ]
    world.matchups[6] = [matchup_row(7), matchup_row(8)]
    out = await invoke(world, tmp_path)
    assert not out.is_error
    assert out.obj["outcome"] == "selection_required"
    assert out.obj["context"] is None
    candidates = out.obj["resolution_evidence"]["association"]["candidates"]
    assert [(c["roster_id"], c["match"]) for c in candidates] == [(7, "co_owner"), (8, "co_owner")]
    issue = out.obj["issues"][0]
    assert issue["reason"] == "roster_selection_required"
    assert issue["next_action"]["type"] == "select_roster"
    assert out.called("matchups:6") == 0

    chosen = await invoke(world, tmp_path / "again", roster_id=8)
    assert chosen.obj["outcome"] == "complete"
    assert chosen.obj["resolved"]["roster_id"] == 8


async def test_preserve_an_unfilled_starting_slot(tmp_path: Path) -> None:
    world = World()
    starters = ["P1", "0", "P2", "P4", "P5", "P6", "P3", "P8"]
    world.rosters = [roster_row(starters=starters), other_row()]
    world.matchups[6] = [matchup_row(starters=starters)]
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "complete"
    slots = out.context["weekly_lineup"]["data"]["slots"]
    assert slots[0]["label"] == "QB" and slots[0]["player_id"] == "P1"
    assert slots[1] == {
        "index": 1,
        "label": "RB",
        "player_id": None,
        "state": "empty",
        "raw_assignment": "0",
        "policy_ids": ["rest_configured_starting_slots_v1", "rest_string_zero_vacancy_v1"],
    }
    assert slots[6]["label"] == "FLEX" and slots[6]["player_id"] == "P3"
    assert "0" not in out.context["player_details"]["data"]


async def test_report_unavailable_lineup_data_for_the_resolved_week(tmp_path: Path) -> None:
    world = World()
    world.matchups[6] = [{"roster_id": 8, "starters": ["X1"] + ["0"] * 7}]
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "partial"
    assert "lineup_unavailable" in out.reasons
    assert out.obj["resolved"]["week"] == 6
    weekly = out.context["weekly_lineup"]
    assert weekly["scope"] == {"league_id": LEAGUE, "roster_id": 7, "season": "2026", "week": 6}
    assert weekly["data"]["slots"] is None
    assert weekly["data"]["row_state"] == "no_selected_row"
    assert out.called("matchups:5") == 0


async def test_report_a_source_failure_when_rosters_time_out(tmp_path: Path) -> None:
    world = World()
    world.overrides["rosters"] = httpx.ReadTimeout("timed out")
    out = await invoke(world, tmp_path)
    assert out.is_error
    assert out.reasons == ["source_failure"]
    assert out.obj["issues"][0]["cause"] == "timeout"
    assert out.obj["issues"][0]["retryable"] is True
    assert out.obj["context"] is None
    rosters = next(s for s in out.obj["sources"] if s["id"] == "src-rosters")
    assert [a["outcome"] for a in rosters["attempts"]] == ["timeout", "timeout"]
    assert out.called("players") == 0


async def test_preserve_a_roster_player_whose_details_cannot_be_resolved(tmp_path: Path) -> None:
    world = World()
    starters = ["P1", "0", "0", "0", "0", "0", "0", "0"]
    world.rosters = [
        {**roster_row(starters=starters), "players": ["P1", "P2"], "reserve": None, "taxi": None},
        other_row(),
    ]
    world.matchups[6] = [matchup_row(starters=starters)]
    world.players = {"P1": profile("P1", "QB")}
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "partial"
    assert out.reasons == ["player_details_unavailable"]
    roster = out.context["roster"]
    assert roster["data"]["placements"] == {"P1": "starter", "P2": "bench"}
    assert roster["coverage"] == {"membership": "complete", "placement": "complete"}
    details = out.context["player_details"]
    assert details["data"]["P2"]["lookup_status"] == "missing_entry"
    assert details["coverage"]["lookup"] == "partial"
    assert out.obj["issues"][0]["affected"] == [{"kind": "player", "ref": "P2"}]


async def test_report_a_user_that_cannot_be_resolved(tmp_path: Path) -> None:
    world = World()
    world.user = None
    world.overrides["user"] = httpx.Response(200, content=b"null")
    out = await invoke(world, tmp_path)
    assert out.is_error
    assert "user_unresolved" in out.reasons
    assert out.obj["resolved"]["user_id"] is None
    assert out.called("rosters") == 0


# A09/A10 scenarios ---------------------------------------------------------------------------


async def test_dual_fantasy_eligibility_without_primary_shortcut(tmp_path: Path) -> None:
    world = World()
    starters = ["P1", "P2", "P3", "12530", "P5", "P6", "P7", "P8"]
    world.rosters = [roster_row(starters=starters), other_row()]
    world.matchups[6] = [matchup_row(starters=starters)]
    world.players["12530"] = {
        "player_id": "12530",
        "full_name": "Travis Hunter",
        "position": "DB",
        "fantasy_positions": ["DB", "WR"],
        "team": "JAX",
    }
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "complete"
    slot = out.context["weekly_lineup"]["data"]["slots"][3]
    assert (slot["label"], slot["player_id"], slot["state"]) == ("WR", "12530", "occupied")
    entry = out.context["player_details"]["data"]["12530"]
    fields = {f["path"]: f["value"] for f in entry["fields"]}
    assert fields["position"] == "DB"
    assert fields["fantasy_positions"] == ["DB", "WR"]
    assert entry["eligibility"]["state"] == "complete"
    assert out.context["league"]["data"]["slot_rule_labels"] is not None
    assert out.obj["definitions"]["slot_rules"]["WR"] == ["WR"]


@pytest.mark.parametrize(
    ("profile_value", "extra_reason"),
    [
        ("missing", None),
        ({"player_id": "P1"}, None),
        ({"player_id": "P1", "fantasy_positions": None}, None),
        ({"player_id": "P1", "fantasy_positions": []}, None),
        ({"player_id": "P1", "fantasy_positions": ["QB", 7]}, "invalid_source_data"),
    ],
)
async def test_occupied_player_whose_eligibility_cannot_be_interpreted(
    tmp_path: Path, profile_value: Any, extra_reason: str | None
) -> None:
    world = World()
    if profile_value == "missing":
        del world.players["P1"]
    else:
        world.players["P1"] = profile_value
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "partial"
    assert "player_details_unavailable" in out.reasons
    assert (
        (extra_reason in out.reasons) if extra_reason else "invalid_source_data" not in out.reasons
    )
    slot = out.context["weekly_lineup"]["data"]["slots"][0]
    assert (slot["player_id"], slot["state"]) == ("P1", "occupied")
    assert out.context["roster"]["data"]["placements"]["P1"] == "starter"
    entry = out.context["player_details"]["data"]["P1"]
    assert entry["eligibility"]["recognized"] in ([], ["QB"])


async def test_custom_scoring_is_preserved_exactly(tmp_path: Path) -> None:
    world = World()
    out = await invoke(world, tmp_path)
    scoring = out.context["league"]["data"]["scoring_settings"]
    assert scoring == world.league["scoring_settings"]
    assert scoring["pass_yd"] == 0.03999999910593033
    assert scoring["explicit_zero"] == 0
    assert out.context["league"]["data"]["scoring_state"] == "complete"
    text_value = json.dumps(scoring["pass_yd"])
    assert text_value == "0.03999999910593033"


@pytest.mark.parametrize(("state", "value"), [("omitted", None), ("null", None), ("{}", {})])
async def test_optional_scoring_gaps_keep_structural_facts(
    tmp_path: Path, state: str, value: Any
) -> None:
    world = World()
    if state == "omitted":
        del world.league["scoring_settings"]
    else:
        world.league["scoring_settings"] = value
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "partial"
    assert out.reasons == ["league_settings_unavailable"]
    league = out.context["league"]
    expected_state = {"omitted": "omitted", "null": "null", "{}": "empty_object"}[state]
    assert league["data"]["scoring_state"] == expected_state
    assert league["data"]["scoring_settings"] == value
    assert out.context["roster"]["coverage"]["placement"] == "complete"
    assert out.context["weekly_lineup"]["coverage"]["assignments"] == "complete"


@pytest.mark.parametrize("bad", ["0.5", True])
async def test_malformed_scoring_value(tmp_path: Path, bad: Any) -> None:
    world = World()
    world.league["scoring_settings"]["rec"] = bad
    out = await invoke(world, tmp_path)
    assert out.obj["outcome"] == "partial"
    assert out.reasons == ["league_settings_unavailable", "invalid_source_data"]
    assert out.context["league"]["data"]["scoring_settings"]["rec"] == bad
    assert out.context["league"]["coverage"]["scoring"] == "partial"
    assert out.context["weekly_lineup"]["coverage"]["assignments"] == "complete"


@pytest.mark.parametrize("best_ball", ["omit", None, False, 0.0])
async def test_explicit_week_does_not_bypass_best_ball_guard(
    tmp_path: Path, best_ball: Any
) -> None:
    world = World()
    if best_ball == "omit":
        del world.league["settings"]["best_ball"]
    else:
        world.league["settings"]["best_ball"] = best_ball
    out = await invoke(world, tmp_path, week=6)
    assert out.is_error
    assert out.reasons == ["invalid_source_data"]
    assert out.obj["context"] is None
    assert out.called("rosters") == 0


async def test_initial_league_source_failure_is_an_error(tmp_path: Path) -> None:
    world = World()
    world.overrides["league"] = httpx.Response(500)
    out = await invoke(world, tmp_path)
    assert out.is_error
    assert out.reasons == ["source_failure"]
    assert out.obj["resolved"]["league_id"] is None
    assert out.obj["context"] is None
    assert out.called("league") == 2
    assert out.called("rosters") == 0


async def test_league_body_helper_is_supported() -> None:
    assert league_body()["roster_positions"][-1] == "BN"
