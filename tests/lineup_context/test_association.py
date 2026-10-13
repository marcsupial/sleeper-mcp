from __future__ import annotations

from typing import Any, cast

import pytest

from sleeper_mcp.lineup_context.association import evaluate_association

U = "test-owner-1"
L = "test-league-redraft"


def row(rid: int, owner: Any = "someone-else", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"roster_id": rid, "league_id": L, "owner_id": owner, "co_owners": None}
    base.update(extra)
    return base


def _length(rows: object) -> int | None:
    return len(cast(list[object], rows)) if isinstance(rows, list) else None


def evaluate(rows: Any, total: Any = None, explicit: int | None = None, total_present: bool = True):
    if total is None and total_present:
        total = _length(rows)
    return evaluate_association(rows, "src-rosters", L, U, total, total_present, explicit)


def test_owner_match_is_selected_automatically() -> None:
    result = evaluate([row(7, U), row(8)])
    assert result.status == "selected"
    ev = result.evidence
    assert ev.selected is not None and ev.selected.roster_id == 7
    assert ev.selected.match == "owner"
    assert ev.selection_basis == "automatic_unique"
    assert ev.uniqueness == "supported"
    assert ev.enumeration.coverage == "complete"


def test_co_owner_match_and_both_count_once() -> None:
    result = evaluate([row(7, "x", co_owners=[U, U]), row(8)])
    assert result.evidence.selected is not None
    assert result.evidence.selected.match == "co_owner"
    both = evaluate([row(7, U, co_owners=[U]), row(8)])
    assert both.evidence.selected is not None
    assert both.evidence.selected.match == "both"
    assert both.evidence.selected.source_fields == ["owner_id", "co_owners"]
    assert len(both.evidence.candidates) == 1


def test_multiple_positives_require_selection() -> None:
    result = evaluate([row(7, "x", co_owners=[U]), row(8, "y", co_owners=[U])])
    assert result.status == "selection_required"
    assert [c.roster_id for c in result.evidence.candidates] == [7, 8]
    assert result.evidence.uniqueness == "multiple"
    issue = result.issues[0]
    assert issue.reason == "roster_selection_required"
    assert issue.next_action == ("select_roster", ["roster_id"], ["roster:7", "roster:8"])


@pytest.mark.parametrize(
    "sibling",
    [
        {"owner_id": None},  # owner null is unknown
        {"owner_id": "y", "co_owners": "omit"},  # omitted co_owners is unknown
        {"owner_id": "y", "co_owners": [1]},  # malformed
    ],
)
def test_one_positive_with_unknown_or_invalid_coverage_requires_selection(
    sibling: dict[str, Any],
) -> None:
    other = row(8, **{k: v for k, v in sibling.items() if v != "omit"})
    if sibling.get("co_owners") == "omit":
        del other["co_owners"]
    result = evaluate([row(7, U), other])
    assert result.status == "selection_required"
    assert result.evidence.uniqueness == "unknown"


@pytest.mark.parametrize(("total", "present"), [(3, True), ("2", True), (None, False)])
def test_one_positive_with_unknown_enumeration_requires_selection(
    total: Any, present: bool
) -> None:
    result = evaluate([row(7, U), row(8)], total=total, total_present=present)
    assert result.status == "selection_required"
    assert result.evidence.enumeration.coverage == "unknown"


def test_zero_positives_covered_is_roster_not_found() -> None:
    result = evaluate([row(7), row(8)])
    assert result.status == "roster_not_found"
    assert result.issues[0].retryable is False


def test_present_null_co_owners_is_known_empty_but_omission_is_unknown() -> None:
    omitted = row(8)
    del omitted["co_owners"]
    assert evaluate([row(7), omitted]).status == "association_unavailable"
    assert evaluate([row(7), row(8)]).status == "roster_not_found"


def test_zero_positives_with_malformed_evidence_is_invalid() -> None:
    assert evaluate([row(7), row(8, co_owners=["x", 2])]).status == "invalid_source_data"
    assert evaluate([row(7), row(8, owner_id=5)]).status == "invalid_source_data"


def test_zero_positives_with_unknown_count_is_unavailable() -> None:
    assert evaluate([row(7), row(8)], total=5).status == "association_unavailable"


@pytest.mark.parametrize(
    "rows",
    [
        {"not": "a list"},
        [row(7, U), row(7)],
        [row(7, U), row(8, league_id="test-league-other")],
        [row(7, U), {"roster_id": "8", "league_id": L}],
        [row(7, U), "row"],
    ],
)
def test_structural_defects_block_selection(rows: Any) -> None:
    result = evaluate(rows, total=2)
    assert result.status == "invalid_source_data"
    assert result.evidence.selected is None
    assert result.evidence.candidates == []
    assert result.evidence.enumeration.structural_defects
    assert result.issues[0].severity == "error"


def test_explicit_positive_survives_sibling_defects_as_a_notice() -> None:
    result = evaluate([row(7, U), row(8, owner_id=5), row(9, owner_id=None)], explicit=7)
    assert result.status == "selected"
    assert result.evidence.selection_basis == "explicit_roster_id"
    assert [(i.reason, i.severity) for i in result.issues] == [("invalid_source_data", "notice")]


def test_malformed_field_on_the_selected_row_is_a_notice() -> None:
    result = evaluate([row(7, U, co_owners=["other", None]), row(8)], explicit=7)
    assert result.status == "selected"
    assert [(i.reason, i.severity) for i in result.issues] == [("invalid_source_data", "notice")]
    assert ("roster", "7") in result.issues[0].affected


def test_explicit_positive_with_unknown_enumeration_is_selected() -> None:
    result = evaluate([row(7, U), row(8)], total=9, explicit=7)
    assert result.status == "selected"
    assert result.evidence.uniqueness == "unknown"


def test_explicit_cases() -> None:
    assert evaluate([row(7, U), row(8)], explicit=8).status == "roster_mismatch"
    assert evaluate([row(7, U), row(8, owner_id=None)], explicit=8).status == (
        "association_unavailable"
    )
    assert evaluate([row(7, U), row(8, owner_id=3)], explicit=8).status == "invalid_source_data"
    assert evaluate([row(7, U), row(8)], explicit=99).status == "roster_mismatch"
    assert evaluate([row(7, U), row(8)], total=5, explicit=99).status == "association_unavailable"
    # Roster IDs have no range constraint: 0 is simply absent from the enumeration.
    assert evaluate([row(7, U), row(8)], explicit=0).status == "roster_mismatch"


def test_row_evidence_keeps_raw_field_states() -> None:
    omitted = row(8)
    del omitted["co_owners"]
    result = evaluate([row(7, U), omitted], explicit=7)
    second = result.evidence.row_evidence[1]
    assert second.co_owners.state == "omitted"
    assert second.owner_id.value == "someone-else"
    assert second.interpretation == "unknown"
