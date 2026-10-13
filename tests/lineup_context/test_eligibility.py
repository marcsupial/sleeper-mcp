from __future__ import annotations

from typing import Any

import pytest

from sleeper_mcp.lineup_context.eligibility import build_details, interpret_positions
from sleeper_mcp.lineup_context.schema import PlayerRole

MEMBER: list[PlayerRole] = ["current_member"]


def details(profiles: Any, refs: dict[str, list[PlayerRole]] | None = None, **kw: Any):
    return build_details(
        refs or {"P1": MEMBER},
        profiles,
        kw.get("applicability", "unknown"),
        kw.get("occupied", []),
        "src-players",
        kw.get("failed", False),
        kw.get("cause"),
    )


@pytest.mark.parametrize(
    ("positions", "state"),
    [
        (["QB"], "complete"),
        (["DB", "WR"], "complete"),
        (["WR", "XX"], "partial"),
        (["WR", "WR"], "partial"),
        ("omit", "unavailable"),
        (None, "unavailable"),
        ([], "unavailable"),
        (["WR", 3], "invalid"),
        (["WR", ""], "invalid"),
        ("WR", "invalid"),
    ],
)
def test_fantasy_position_states(positions: Any, state: str) -> None:
    profile: dict[str, Any] = {} if positions == "omit" else {"fantasy_positions": positions}
    assert interpret_positions(profile).state == state


def test_dual_eligibility_keeps_both_positions_without_primary_fallback() -> None:
    result = details(
        {"12530": {"player_id": "12530", "position": "DB", "fantasy_positions": ["DB", "WR"]}},
        {"12530": ["current_member", "weekly_starter"]},
    )
    entry = result.entries["12530"]
    assert entry.eligibility.recognized == ["DB", "WR"]
    assert entry.key_binding == "directory_key_and_player_id"
    assert result.issues == []


def test_fb_primary_with_rb_eligibility() -> None:
    result = details({"P1": {"position": "FB", "fantasy_positions": ["RB"]}})
    entry = result.entries["P1"]
    assert entry.key_binding == "directory_key"
    assert entry.eligibility.state == "complete"
    assert entry.fields is not None
    position = next(f for f in entry.fields if f.path == "position")
    assert position.value == "FB"


def test_def_team_key_without_optional_fields() -> None:
    result = details(
        {"JAX": {"team": "JAX", "position": "DEF", "fantasy_positions": ["DEF"]}},
        {"JAX": ["current_member", "current_starter"]},
    )
    entry = result.entries["JAX"]
    assert entry.lookup_status == "found"
    assert entry.fields is not None
    states = {f.path: f.state for f in entry.fields}
    assert states["full_name"] == "omitted" and states["status"] == "omitted"
    assert result.issues == []


def test_missing_entry_keeps_the_player_and_marks_details_unavailable() -> None:
    result = details({"P2": {"fantasy_positions": ["QB"]}})
    assert result.entries["P1"].lookup_status == "missing_entry"
    assert [i.reason for i in result.issues] == ["player_details_unavailable"]
    assert result.coverage.lookup == "unavailable"


def test_unavailable_directory_with_failed_attempt() -> None:
    result = details(None, failed=True, cause="timeout")
    assert result.entries["P1"].lookup_status == "directory_unavailable"
    assert [i.reason for i in result.issues] == ["player_details_unavailable", "source_failure"]


def test_unavailable_directory_with_gate_used() -> None:
    result = details(None, cause="directory_gate_used")
    assert [i.reason for i in result.issues] == ["player_details_unavailable"]
    assert result.issues[0].cause == "directory_gate_used"


@pytest.mark.parametrize(
    "profile",
    [
        "not an object",
        {"player_id": "P9", "fantasy_positions": ["QB"]},
        {"player_id": 1, "fantasy_positions": ["QB"]},
    ],
)
def test_invalid_profiles(profile: Any) -> None:
    result = details({"P1": profile})
    assert result.entries["P1"].lookup_status == "invalid_profile"
    assert [i.reason for i in result.issues] == [
        "player_details_unavailable",
        "invalid_source_data",
    ]


@pytest.mark.parametrize("positions", [None, [], ["WR", 1]])
def test_uninterpretable_positions_on_required_players(positions: Any) -> None:
    result = details({"P1": {"fantasy_positions": positions}})
    assert "player_details_unavailable" in [i.reason for i in result.issues]


def test_partial_positions_only_limit_current_week_occupied_slots() -> None:
    profiles = {"P1": {"fantasy_positions": ["WR", "XX"]}}
    historical = details(profiles, {"P1": ["current_member", "weekly_starter"]}, occupied=["P1"])
    assert historical.issues == []
    current = details(
        profiles,
        {"P1": ["current_member", "weekly_starter"]},
        occupied=["P1"],
        applicability="current_observation",
    )
    assert [i.reason for i in current.issues] == ["player_details_unavailable"]
    assert current.entries["P1"].applicability == "current_observation"


def test_weekly_member_and_fragment_roles_do_not_limit() -> None:
    result = details({}, {"W1": ["weekly_member"], "F1": ["raw_only_fragment"]})
    assert result.issues == []
    assert result.entries["W1"].lookup_status == "missing_entry"
