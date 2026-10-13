from __future__ import annotations

from typing import Any

import pytest

from sleeper_mcp.lineup_context.weekly import evaluate_weekly
from sleeper_mcp.sleeper.observation import Failure, SourceObservation
from sleeper_mcp.sleeper.routes import Route

L = "test-league-redraft"
CONFIG = ["QB", "RB", "FLEX"]


def obs(body: Any) -> SourceObservation:
    o = SourceObservation(id="src-matchups", route=Route.MATCHUPS, url="u", coordinate={})
    o.parse = "parsed"
    o.body = body
    return o


def evaluate(body: Any, configuration: list[str] | None = CONFIG):
    return evaluate_weekly(obs(body), L, 7, 6, configuration, "observed_selected_configuration")


def reasons(result: Any) -> list[str]:
    return [i.reason for i in result.issues]


def test_selected_row_maps_assignments() -> None:
    result = evaluate(
        [
            {"roster_id": 7, "starters": ["A", "0", "C"], "matchup_id": None, "points": 0},
            {"roster_id": 8, "starters": []},
        ]
    )
    assert result.issues == []
    assert result.coverage.assignments == "complete"
    assert result.data.slots is not None
    assert [s.state for s in result.data.slots] == ["occupied", "empty", "occupied"]
    assert result.data.assignments is not None
    assert [a.raw for a in result.data.assignments] == ["A", "0", "C"]


@pytest.mark.parametrize("body", [[], [{"roster_id": 8, "starters": ["A", "B", "C"]}]])
def test_no_selected_row_is_lineup_unavailable(body: Any) -> None:
    result = evaluate(body)
    assert reasons(result) == ["lineup_unavailable"]
    assert result.data.row_state == "no_selected_row"
    assert result.data.slots is None


@pytest.mark.parametrize(
    ("row", "state"),
    [
        ({"roster_id": 7}, "omitted"),
        ({"roster_id": 7, "starters": None}, "null"),
        ({"roster_id": 7, "starters": []}, "array"),
    ],
)
def test_omitted_null_or_empty_starters(row: dict[str, Any], state: str) -> None:
    result = evaluate([row])
    assert reasons(result) == ["lineup_unavailable"]
    assert result.data.starters_state == state
    assert result.data.slots is None


@pytest.mark.parametrize("starters", ["A", ["A", "B"], ["A", "B", "C", "D"], ["A", 1, "C"]])
def test_wrong_type_or_length_is_invalid(starters: Any) -> None:
    result = evaluate([{"roster_id": 7, "starters": starters}])
    assert reasons(result) == ["invalid_source_data", "lineup_unavailable"]
    assert result.data.slots is None


@pytest.mark.parametrize(
    "body",
    [
        {"roster_id": 7},
        [{"roster_id": 7, "starters": ["A", "B", "C"]}, {"roster_id": 7, "starters": ["A"]}],
        [{"roster_id": "7"}],
        [{"roster_id": 7, "league_id": "test-league-other"}],
        ["row"],
    ],
)
def test_invalid_bodies_and_duplicate_rows(body: Any) -> None:
    result = evaluate(body)
    assert reasons(result) == ["invalid_source_data", "lineup_unavailable"]
    assert result.data.row_state == "invalid_body"


def test_duplicate_assignment_is_partial_with_every_index() -> None:
    result = evaluate([{"roster_id": 7, "starters": ["A", "A", "C"]}])
    assert reasons(result) == ["invalid_source_data"]
    assert result.coverage.assignments == "partial"
    assert result.data.duplicate_ids == ["A"]


def test_missing_configuration_keeps_raw_assignments() -> None:
    result = evaluate([{"roster_id": 7, "starters": ["A", "B", "C"]}], configuration=None)
    assert result.data.slots is None
    assert result.data.assignments is not None
    assert result.coverage.mapping == "unavailable"


def test_failed_retrieval_is_source_failure_not_empty() -> None:
    failed = SourceObservation(id="src-matchups", route=Route.MATCHUPS, url="u", coordinate={})
    failed.failure = Failure("http_status", "HTTP 503", True)
    result = evaluate_weekly(failed, L, 7, 6, CONFIG, None)
    assert reasons(result) == ["source_failure", "lineup_unavailable"]
    assert result.issues[0].retryable is True
    assert result.data.row_state == "source_failure"
