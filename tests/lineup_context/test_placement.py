from __future__ import annotations

from typing import Any

import pytest

from sleeper_mcp.lineup_context.placement import evaluate_placement

CONFIG = ["QB", "RB", "FLEX"]


def row(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "players": ["S1", "S2", "S3", "B1", "R1", "T1"],
        "starters": ["S1", "S2", "S3"],
        "reserve": ["R1"],
        "taxi": ["T1"],
    }
    for key, value in changes.items():
        if value == "omit":
            base.pop(key, None)
        else:
            base[key] = value
    return base


def evaluate(**changes: Any):
    return evaluate_placement(row(**changes), CONFIG, "7", "src-rosters")


def reasons(result: Any) -> list[str]:
    return [i.reason for i in result.issues]


def test_complete_partition_derives_the_bench() -> None:
    result = evaluate()
    assert result.placement_coverage == "complete"
    assert result.data.bench_ids == ["B1"]
    assert result.data.placements == {
        "S1": "starter",
        "S2": "starter",
        "S3": "starter",
        "B1": "bench",
        "R1": "reserve",
        "T1": "taxi",
    }
    assert result.issues == []


@pytest.mark.parametrize("value", [None, []])
def test_present_null_or_empty_categories_are_known_empty(value: Any) -> None:
    result = evaluate(players=["S1", "S2", "S3", "B1"], reserve=value, taxi=value)
    assert result.data.bench_ids == ["B1"]
    states = {c.field: c.interpretation for c in result.data.categories}
    assert states["reserve"] == ("known_empty_by_policy" if value is None else "values")


@pytest.mark.parametrize("field", ["reserve", "taxi"])
def test_omitted_category_is_unknown_and_withholds_the_bench(field: str) -> None:
    result = evaluate(**{field: "omit"})
    assert result.data.bench_ids is None
    assert result.data.placements is not None
    assert result.data.placements["B1"] == "unknown"
    assert result.data.placements["S1"] == "starter"
    assert reasons(result) == ["placement_unavailable"]


def test_malformed_category_keeps_valid_positives() -> None:
    result = evaluate(taxi=["T1", 5])
    assert result.data.placements is not None
    assert result.data.placements["T1"] == "taxi"
    assert result.data.bench_ids is None
    assert reasons(result) == ["invalid_source_data", "placement_unavailable"]


@pytest.mark.parametrize("field", ["players", "starters", "reserve", "taxi"])
def test_within_field_duplicates_withhold_bench_but_keep_positives(field: str) -> None:
    values = row()[field]
    result = evaluate(**{field: [*values, values[0]]})
    assert result.data.bench_ids is None
    assert "invalid_source_data" in reasons(result)
    if field == "players":
        assert result.membership_coverage == "partial"
        assert result.data.members == ["S1", "S2", "S3", "B1", "R1", "T1"]


@pytest.mark.parametrize(
    ("changes", "conflicted"),
    [
        ({"reserve": ["R1", "S1"]}, "S1"),
        ({"taxi": ["T1", "R1"]}, "R1"),
        ({"reserve": ["R1", "T1"], "taxi": ["T1"]}, "T1"),
        ({"reserve": ["R1", "S1"], "taxi": ["T1", "S1"]}, "S1"),
    ],
)
def test_overlaps_make_placement_unknown(changes: dict[str, Any], conflicted: str) -> None:
    result = evaluate(**changes)
    assert result.data.placements is not None
    assert result.data.placements[conflicted] == "unknown"
    assert conflicted in result.data.conflicts
    assert "placement_conflict" in reasons(result)
    assert result.data.bench_ids is None


def test_category_ids_outside_players_are_extras() -> None:
    result = evaluate(reserve=["R1", "GHOST"])
    assert result.data.extras == {"reserve": ["GHOST"]}
    assert "GHOST" not in (result.data.members or [])
    assert "placement_conflict" in reasons(result)


@pytest.mark.parametrize("players", ["omit", None, ["S1", 3], "S1"])
def test_unusable_players_is_roster_data_unavailable(players: Any) -> None:
    result = evaluate(players=players)
    assert result.membership_coverage == "unavailable"
    assert result.data.members is None
    assert result.data.bench_ids is None
    assert reasons(result) == ["roster_data_unavailable", "invalid_source_data"]
    if isinstance(players, list):
        assert result.data.candidate_member_ids == ["S1"]


def test_consistently_empty_roster_is_complete() -> None:
    result = evaluate_placement(
        {"players": [], "starters": [], "reserve": None, "taxi": []}, CONFIG, "7", "src"
    )
    assert result.membership_coverage == "complete"
    assert result.placement_coverage == "complete"
    assert result.data.members == []
    assert result.data.bench_ids == []


def test_zero_in_current_starters_is_a_vacancy() -> None:
    result = evaluate(starters=["S1", "0", "S3"], players=["S1", "S3", "B1", "R1", "T1"])
    assert result.placement_coverage == "complete"
    assert result.data.bench_ids == ["B1"]


def test_zero_in_players_prevents_complete_membership() -> None:
    result = evaluate(players=["S1", "S2", "S3", "B1", "R1", "T1", "0"])
    assert result.membership_coverage == "partial"
    assert "0" not in (result.data.members or [])
