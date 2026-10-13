"""`rest_owner_co_owner_null_v1` association and the A02 resolution matrix."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sleeper_mcp.contract import ASSOCIATION_POLICY
from sleeper_mcp.json_values import (
    Json,
    JsonObject,
    as_list,
    as_object,
    is_id_string,
    is_id_string_list,
    is_json_int,
)
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.raw import raw_field, raw_value
from sleeper_mcp.lineup_context.schema import (
    AssociationEvidence,
    Candidate,
    Enumeration,
    RawField,
    RowEvidence,
)

Status = Literal[
    "selected",
    "selection_required",
    "roster_not_found",
    "association_unavailable",
    "roster_mismatch",
    "invalid_source_data",
]


@dataclass
class RowState:
    roster_id: int
    positive: bool
    unknown: bool
    invalid: bool
    match: Literal["owner", "co_owner", "both"] | None


@dataclass
class AssociationResult:
    evidence: AssociationEvidence
    issues: list[IssueSpec]
    selected_row: JsonObject | None

    @property
    def status(self) -> str:
        return self.evidence.status


def _row_state(row: JsonObject, user_id: str) -> RowState:
    owner = row.get("owner_id")
    owner_state = "known" if is_id_string(owner) else "unknown" if owner is None else "invalid"
    if "co_owners" not in row:
        co_state = "unknown"
    else:
        co = row["co_owners"]
        co_state = "known" if co is None or is_id_string_list(co) else "invalid"
    owner_match = owner_state == "known" and owner == user_id
    co_list = as_list(row.get("co_owners"))
    co_match = co_state == "known" and co_list is not None and user_id in co_list
    match: Literal["owner", "co_owner", "both"] | None = (
        "both"
        if owner_match and co_match
        else "owner"
        if owner_match
        else "co_owner"
        if co_match
        else None
    )
    return RowState(
        roster_id=row["roster_id"],  # pyright: ignore[reportArgumentType]
        positive=match is not None,
        unknown=owner_state == "unknown" or co_state == "unknown",
        invalid=owner_state == "invalid" or co_state == "invalid",
        match=match,
    )


def _source_fields(match: str) -> list[str]:
    return {"owner": ["owner_id"], "co_owner": ["co_owners"], "both": ["owner_id", "co_owners"]}[
        match
    ]


def evaluate_association(
    body: Json,
    source_id: str,
    league_id: str,
    user_id: str,
    total_rosters: Json,
    total_rosters_present: bool,
    explicit_roster_id: int | None,
) -> AssociationResult:
    rows = as_list(body)
    total_field = (
        raw_value("total_rosters", total_rosters)
        if total_rosters_present
        else RawField(path="total_rosters", state="omitted", value=None)
    )

    row_evidence: list[RowEvidence] = []
    defects: list[str] = []
    states: dict[int, RowState] = {}
    by_id: dict[int, JsonObject] = {}

    if rows is None:
        defects.append("roster response is not a JSON array")
    else:
        for index, raw_row in enumerate(rows):
            row = as_object(raw_row)
            if row is None:
                defects.append(f"row {index} is not an object")
                continue
            rid = row.get("roster_id")
            interpretation: Literal[
                "positive", "excluded", "unknown", "invalid", "structural_defect"
            ]
            if not is_json_int(rid) or rid < 1:
                defects.append(f"row {index} has a missing or non-integer roster_id")
                interpretation = "structural_defect"
            elif row.get("league_id") != league_id:
                defects.append(f"row {index} names a different or missing league_id")
                interpretation = "structural_defect"
            elif rid in states:
                defects.append(f"roster_id {rid} appears more than once")
                interpretation = "structural_defect"
            else:
                state = _row_state(row, user_id)
                states[rid] = state
                by_id[rid] = row
                interpretation = (
                    "positive"
                    if state.positive
                    else "invalid"
                    if state.invalid
                    else "unknown"
                    if state.unknown
                    else "excluded"
                )
            row_evidence.append(
                RowEvidence(
                    row_index=index,
                    roster_id=raw_field(row, "roster_id"),
                    league_id=raw_field(row, "league_id"),
                    owner_id=raw_field(row, "owner_id"),
                    co_owners=raw_field(row, "co_owners"),
                    interpretation=interpretation,
                )
            )

    row_count = len(rows) if rows is not None else None
    enumeration_complete = (
        rows is not None
        and not defects
        and is_json_int(total_rosters)
        and total_rosters >= 0
        and total_rosters == len(rows)
    )
    enumeration = Enumeration(
        coverage="unavailable" if defects else "complete" if enumeration_complete else "unknown",
        row_count=row_count,
        total_rosters=total_field,
        structural_defects=defects,
    )

    def candidate(rid: int) -> Candidate:
        match = states[rid].match
        assert match is not None
        return Candidate(
            league_id=league_id,
            roster_id=rid,
            match=match,
            source_fields=_source_fields(match),
            source_refs=[source_id],
            label=None,
        )

    positives = sorted(rid for rid, s in states.items() if s.positive)
    candidates = [candidate(rid) for rid in positives] if not defects else []

    def finish(
        status: Status,
        issues: list[IssueSpec],
        selected: int | None = None,
        basis: Literal["automatic_unique", "explicit_roster_id"] | None = None,
        uniqueness: Literal["supported", "multiple", "unknown", "not_applicable"] = "unknown",
    ) -> AssociationResult:
        evidence = AssociationEvidence(
            policy_id=ASSOCIATION_POLICY,
            status=status,
            selection_basis=basis,
            requested_roster_id=explicit_roster_id,
            selected=candidate(selected) if selected is not None else None,
            candidates=candidates,
            enumeration=enumeration,
            uniqueness=uniqueness,
            row_evidence=row_evidence,
            source_refs=[source_id],
        )
        return AssociationResult(evidence, issues, by_id.get(selected) if selected else None)

    def fatal(
        reason: str,
        explanation: str,
        action: tuple[str, list[str], list[str]] | None,
        retryable: bool | None = None,
    ) -> IssueSpec:
        return IssueSpec(
            reason=reason,  # pyright: ignore[reportArgumentType]
            severity="error",
            explanation=explanation,
            affected=[("source", source_id), ("section", "resolution_evidence.association")],
            next_action=action,  # pyright: ignore[reportArgumentType]
            retryable=retryable,
            stage="association",
        )

    if defects:
        return finish(
            "invalid_source_data",
            [
                fatal(
                    "invalid_source_data",
                    "The roster list has structural identity defects, so no roster can be "
                    "selected: " + "; ".join(defects) + ".",
                    ("review_evidence", [], [source_id]),
                )
            ],
            uniqueness="unknown",
        )

    invalid_rows = sorted(rid for rid, s in states.items() if s.invalid)
    unknown_rows = sorted(rid for rid, s in states.items() if s.unknown and not s.invalid)
    all_known = not invalid_rows and not unknown_rows

    def sibling_notice(selected: int) -> list[IssueSpec]:
        others = [rid for rid in invalid_rows if rid != selected]
        if not others:
            return []
        return [
            IssueSpec(
                reason="invalid_source_data",
                severity="notice",
                explanation=(
                    "Other roster rows have malformed owner_id or co_owners values. The explicit "
                    "positive selection stands; uniqueness across rosters is not claimed."
                ),
                affected=[("roster", str(rid)) for rid in others] + [("source", source_id)],
                next_action=None,
                retryable=None,
            )
        ]

    if explicit_roster_id is not None:
        rid = explicit_roster_id
        selected = states.get(rid)
        if selected is not None and selected.positive:
            uniqueness: Literal["supported", "multiple", "unknown", "not_applicable"] = (
                "multiple"
                if len(positives) > 1
                else "supported"
                if enumeration_complete and all_known
                else "unknown"
            )
            return finish("selected", sibling_notice(rid), rid, "explicit_roster_id", uniqueness)
        if selected is not None and selected.invalid:
            return finish(
                "invalid_source_data",
                [
                    fatal(
                        "invalid_source_data",
                        f"Roster {rid} has malformed owner_id or co_owners values, so its "
                        "association cannot be established.",
                        ("review_evidence", [], [source_id]),
                    )
                ],
            )
        if (selected is not None and selected.unknown) or (
            selected is None and not enumeration_complete
        ):
            return finish(
                "association_unavailable",
                [
                    fatal(
                        "association_unavailable",
                        f"Roster {rid} cannot be associated with this user: its owner fields are "
                        "unknown or the roster list may be incomplete. Another roster ID alone "
                        "cannot fix missing evidence.",
                        ("review_evidence", [], [source_id]),
                    )
                ],
            )
        return finish(
            "roster_mismatch",
            [
                fatal(
                    "roster_mismatch",
                    f"Roster {rid} is not associated with this user in the observed roster list "
                    "under the owner/co-owner policy."
                    if selected is not None
                    else f"Roster {rid} is not in the observed roster list.",
                    ("correct_input", ["roster_id"], []),
                    retryable=False,
                )
            ],
        )

    if positives:
        if len(positives) == 1 and enumeration_complete and all_known:
            return finish("selected", [], positives[0], "automatic_unique", "supported")
        multiple = len(positives) > 1
        refs = [f"roster:{rid}" for rid in positives]
        explanation = (
            f"The user is associated with rosters {', '.join(map(str, positives))}; "
            "pass roster_id to choose one."
            if multiple
            else f"The user is associated with roster {positives[0]}, but other rows or the "
            "roster count leave uniqueness unknown; pass roster_id to confirm."
        )
        issues = [
            IssueSpec(
                reason="roster_selection_required",
                severity="limitation",
                explanation=explanation,
                affected=[("roster", str(rid)) for rid in positives],
                next_action=("select_roster", ["roster_id"], refs),
                retryable=False,
            )
        ]
        if invalid_rows:
            issues.append(
                IssueSpec(
                    reason="invalid_source_data",
                    severity="notice",
                    explanation="Some roster rows have malformed owner_id or co_owners values.",
                    affected=[("roster", str(rid)) for rid in invalid_rows]
                    + [("source", source_id)],
                    next_action=None,
                    retryable=None,
                )
            )
        return finish(
            "selection_required", issues, uniqueness="multiple" if multiple else "unknown"
        )

    if invalid_rows:
        return finish(
            "invalid_source_data",
            [
                fatal(
                    "invalid_source_data",
                    "No roster is positively associated, and some rows have malformed owner "
                    "fields, so absence cannot be established.",
                    ("review_evidence", [], [source_id]),
                )
            ],
        )
    if not enumeration_complete or unknown_rows:
        return finish(
            "association_unavailable",
            [
                fatal(
                    "association_unavailable",
                    "No roster is positively associated, but owner fields or the roster count are "
                    "unknown, so absence cannot be established.",
                    ("review_evidence", [], [source_id]),
                )
            ],
        )
    return finish(
        "roster_not_found",
        [
            fatal(
                "roster_not_found",
                "No roster in this observed roster list is owned or co-owned by the user under the "
                "owner/co-owner policy. This describes this observation, not permanent absence.",
                ("correct_input", ["user_id", "league_id"], []),
                retryable=False,
            )
        ],
        uniqueness="not_applicable",
    )
