from __future__ import annotations

from sleeper_mcp.json_values import Json
from sleeper_mcp.lineup_context.schema import Placement
from sleeper_mcp.lineup_context.slots import map_slots
from sleeper_mcp.lineup_context.views import compare_views

CONFIG = ["QB", "RB", "K", "DEF"]
PLACEMENTS: dict[str, Placement] = {
    "A": "starter",
    "B": "starter",
    "C": "bench",
    "K1": "starter",
    "D1": "starter",
}


def compare(weekly: list[str], current: list[str], complete: bool = True):
    slots = map_slots(CONFIG, weekly).slots
    return compare_views(
        "src-rosters",
        "src-matchups",
        list(PLACEMENTS),
        complete,
        PLACEMENTS,
        cast_json(current),
        slots,
        cast_json(weekly),
    )


def cast_json(values: list[str]) -> Json:
    items: list[Json] = list(values)
    return items


def test_identical_views_have_no_differences() -> None:
    result = compare(["A", "B", "K1", "D1"], ["A", "B", "K1", "D1"])
    assert result.comparison.differences == []
    assert result.issues == []
    assert set(result.comparison.membership_states.values()) == {"present"}


def test_off_roster_historical_k_def_starters_are_absent_with_complete_membership() -> None:
    result = compare(["A", "B", "K0", "HOU"], ["A", "B", "K1", "D1"])
    states = result.comparison.membership_states
    assert states["K0"] == "absent" and states["HOU"] == "absent"
    kinds = [(d.kind, d.difference_type) for d in result.comparison.differences]
    assert (
        "weekly_player_not_in_current_roster",
        "weekly_player_absent_from_current_membership",
    ) in kinds
    assert ("current_weekly_assignment_difference", "ordered_starter_arrays_differ") in kinds
    assert {i.severity for i in result.issues} == {"notice"}
    ordered = next(
        d
        for d in result.comparison.differences
        if d.difference_type == "ordered_starter_arrays_differ"
    )
    assert ordered.indexes == [2, 3]


def test_absence_is_unknown_with_incomplete_membership() -> None:
    result = compare(["A", "B", "K0", "D1"], ["A", "B", "K1", "D1"], complete=False)
    assert result.comparison.membership_states["K0"] == "unknown"
    assert all(
        d.kind != "weekly_player_not_in_current_roster" for d in result.comparison.differences
    )


def test_current_bench_player_starting_in_week_is_described() -> None:
    result = compare(["A", "C", "K1", "D1"], ["A", "B", "K1", "D1"])
    types = {d.difference_type for d in result.comparison.differences}
    assert "current_placement_differs_from_weekly_assignment" in types
    assert "current_starter_not_observed_as_weekly_starter" in types


def test_weekly_unavailable() -> None:
    result = compare_views("src-rosters", None, ["A"], True, {"A": "starter"}, ["A"], None, None)
    assert result.comparison.state == "weekly_unavailable"
    assert result.comparison.atomic_snapshot is False
