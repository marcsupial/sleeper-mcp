"""`rest_weekly_observed_assignments_v1`: the selected row for the pinned week (A06)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sleeper_mcp.json_values import (
    OMITTED,
    JsonObject,
    as_list,
    as_object,
    field_state,
    is_json_int,
)
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.raw import observation
from sleeper_mcp.lineup_context.schema import (
    Assignment,
    Observation,
    WeeklyCoverage,
    WeeklyData,
)
from sleeper_mcp.lineup_context.slots import SlotMapping, map_slots
from sleeper_mcp.sleeper.observation import SourceObservation

WEEKLY_ROW_FIELDS = ["roster_id", "league_id", "starters", "players", "matchup_id", "points"]


@dataclass
class WeeklyResult:
    data: WeeklyData
    coverage: WeeklyCoverage
    observed: list[Observation]
    row: JsonObject | None
    mapping: SlotMapping | None
    issues: list[IssueSpec] = field(default_factory=lambda: [])


def _unavailable_data(row_state: str, basis: str | None) -> WeeklyData:
    return WeeklyData(
        row_state=row_state,  # pyright: ignore[reportArgumentType]
        starters_state=None,
        assignments=None,
        slots=None,
        mapping_basis=basis,
        duplicate_ids=[],
        scoring_effect="not_established",
        autosub_finality="not_established",
    )


def not_attempted(basis: str | None) -> WeeklyResult:
    return WeeklyResult(
        data=_unavailable_data("not_attempted", basis),
        coverage=WeeklyCoverage(assignments="unavailable", mapping="unavailable"),
        observed=[],
        row=None,
        mapping=None,
    )


def evaluate_weekly(
    obs: SourceObservation,
    league_id: str,
    roster_id: int,
    week: int,
    configuration: list[str] | None,
    mapping_basis: str | None,
) -> WeeklyResult:
    def issue(
        reason: str, explanation: str, cause: str | None = None, retryable: bool | None = None
    ) -> IssueSpec:
        return IssueSpec(
            reason=reason,  # pyright: ignore[reportArgumentType]
            severity="limitation",
            explanation=explanation,
            affected=[("section", "context.weekly_lineup"), ("source", obs.id)],
            cause=cause,
            next_action=("retry", [], [obs.id]) if retryable else ("review_evidence", [], [obs.id]),
            retryable=retryable,
        )

    coordinate = f"league {league_id}, roster {roster_id}, week {week}"
    if not obs.ok:
        failure = obs.failure
        failed = obs.parse != "failed"
        return WeeklyResult(
            data=_unavailable_data("source_failure", mapping_basis),
            coverage=WeeklyCoverage(assignments="unavailable", mapping="unavailable"),
            observed=[],
            row=None,
            mapping=None,
            issues=[
                issue(
                    "source_failure" if failed else "invalid_source_data",
                    f"Weekly lineup data for {coordinate} could not be retrieved or parsed; no "
                    "other week or current starters are substituted.",
                    failure.cause if failure else None,
                    failure.retryable if failure else None,
                ),
                issue("lineup_unavailable", f"No weekly lineup is available for {coordinate}."),
            ],
        )
    body = obs.body
    rows = as_list(body)
    valid = rows is not None and all(
        (r := as_object(item)) is not None
        and is_json_int(r.get("roster_id"))
        and ("league_id" not in r or r["league_id"] == league_id)
        for item in rows
    )
    if valid and rows is not None:
        ids = [as_object(item).get("roster_id") for item in rows]  # pyright: ignore[reportOptionalMemberAccess]
        valid = len(set(ids)) == len(ids)  # pyright: ignore[reportArgumentType]
    container = [observation(obs.id, "body", body, [], None)]
    if not valid or rows is None:
        return WeeklyResult(
            data=_unavailable_data("invalid_body", mapping_basis),
            coverage=WeeklyCoverage(assignments="unavailable", mapping="unavailable"),
            observed=container,
            row=None,
            mapping=None,
            issues=[
                issue(
                    "invalid_source_data",
                    "The weekly response is not a list of rows with "
                    "distinct integer roster IDs for this league.",
                ),
                issue("lineup_unavailable", f"No weekly lineup is available for {coordinate}."),
            ],
        )
    row = next(
        (
            r
            for item in rows
            if (r := as_object(item)) is not None and r.get("roster_id") == roster_id
        ),
        None,
    )
    if row is None:
        return WeeklyResult(
            data=_unavailable_data("no_selected_row", mapping_basis),
            coverage=WeeklyCoverage(assignments="unavailable", mapping="unavailable"),
            observed=container,
            row=None,
            mapping=None,
            issues=[
                issue(
                    "lineup_unavailable",
                    f"The weekly response has no row for {coordinate}. This is not an empty "
                    "lineup, and no other week is substituted.",
                )
            ],
        )
    selector = f"body[roster_id={roster_id}]"
    observed = [observation(obs.id, selector, row, WEEKLY_ROW_FIELDS, row)]
    starters = row.get("starters", OMITTED)
    starters_list = as_list(row.get("starters"))
    assignments = (
        [Assignment(index=i, raw=value) for i, value in enumerate(starters_list)]
        if starters_list is not None
        else None
    )
    mapping = map_slots(configuration, starters, row.get("players", OMITTED))
    issues: list[IssueSpec] = []
    state = field_state(row, "starters")
    if mapping.status == "lineup_unavailable":
        issues.append(
            issue(
                "lineup_unavailable",
                f"The selected row's starters is {'[]' if starters == [] else state}; assignments "
                f"for {coordinate} are unavailable, not an empty lineup.",
            )
        )
    elif mapping.status == "invalid":
        issues.append(
            issue(
                "invalid_source_data",
                "The selected row's starters is not a slot-length array of player ID strings.",
            )
        )
        issues.append(
            issue(
                "lineup_unavailable",
                f"Assignments for {coordinate} cannot be interpreted; raw values are kept.",
            )
        )
    elif mapping.status == "defective":
        issues.append(
            issue(
                "invalid_source_data",
                "The weekly starters repeat a player or contradict the vacancy sentinel; affected "
                "evidence is kept and assignment completeness is not claimed.",
            )
        )
    elif mapping.status == "slot_mapping_unavailable" and starters_list in (None, []):
        issues.append(issue("lineup_unavailable", f"Assignments for {coordinate} are unavailable."))

    if mapping.interpreted:
        assigned = "complete" if mapping.status == "mapped" else "partial"
        coverage = WeeklyCoverage(assignments=assigned, mapping=assigned)
    elif mapping.status == "slot_mapping_unavailable" and starters_list:
        coverage = WeeklyCoverage(assignments="partial", mapping="unavailable")
    else:
        coverage = WeeklyCoverage(
            assignments="unavailable",
            mapping="unknown" if mapping.status == "lineup_unavailable" else "unavailable",
        )
    return WeeklyResult(
        data=WeeklyData(
            row_state="selected_row",
            starters_state=state,
            assignments=assignments,
            slots=mapping.slots,
            mapping_basis=mapping_basis if mapping.slots is not None else None,
            duplicate_ids=mapping.duplicate_ids,
            scoring_effect="not_established",
            autosub_finality="not_established",
        ),
        coverage=coverage,
        observed=observed,
        row=row,
        mapping=mapping,
        issues=issues,
    )
