"""`rest_roster_placements_v1`: current membership and placements (A04, with A10's boundary)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sleeper_mcp.contract import PLACEMENT_POLICY, SLOT_POLICY, VACANCY_POLICY
from sleeper_mcp.json_values import (
    JsonObject,
    as_list,
    field_state,
    is_id_string,
    is_id_string_list,
)
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.schema import CategoryState, Placement, RosterData
from sleeper_mcp.lineup_context.slots import VACANCY, map_slots, occupied_ids


@dataclass
class PlacementResult:
    data: RosterData
    membership_coverage: str
    placement_coverage: str
    membership_complete: bool
    issues: list[IssueSpec] = field(default_factory=lambda: [])


def _distinct(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for value in values:
        if value in seen and value not in repeated:
            repeated.append(value)
        seen.add(value)
    return repeated


def evaluate_placement(
    row: JsonObject,
    configuration: list[str] | None,
    roster_ref: str,
    source_id: str,
) -> PlacementResult:
    players = row.get("players")
    issues: list[IssueSpec] = []

    if not is_id_string_list(players):
        # A10: association stands; membership is unavailable. Fragments are candidate evidence.
        fragments = _distinct(
            [p for p in (as_list(players) or []) if is_id_string(p) and p != VACANCY]
        )
        state = field_state(row, "players")
        issues.extend(
            [
                IssueSpec(
                    reason="roster_data_unavailable",
                    severity="limitation",
                    explanation=(
                        f"The selected roster's players field is {state} or malformed, so "
                        "membership, bench and absence are unknown. Weekly facts remain."
                    ),
                    affected=[
                        ("field", "roster.players"),
                        ("source", source_id),
                        ("roster", roster_ref),
                    ],
                    next_action=("review_evidence", [], [source_id]),
                    retryable=None,
                ),
                IssueSpec(
                    reason="invalid_source_data",
                    severity="limitation",
                    explanation=f"The selected roster's players field is {state}, not an array "
                    "of player ID strings.",
                    affected=[("field", "roster.players"), ("source", source_id)],
                    next_action=("review_evidence", [], [source_id]),
                    retryable=None,
                ),
            ]
        )
        categories = [_category(row, key, configuration) for key in ("starters", "reserve", "taxi")]
        return PlacementResult(
            data=RosterData(
                members=None,
                candidate_member_ids=fragments,
                placements=None,
                bench_ids=None,
                categories=categories,
                duplicates={},
                conflicts=[],
                extras={},
            ),
            membership_coverage="unavailable",
            placement_coverage="unavailable",
            membership_complete=False,
            issues=issues,
        )

    member_list: list[str] = list(players)
    member_set = set(member_list)
    members = _distinct([p for p in member_list if p != VACANCY])
    membership_complete = len(member_list) == len(member_set) and VACANCY not in member_set
    duplicates: dict[str, list[str]] = {}
    if _duplicates(member_list):
        duplicates["players"] = _duplicates(member_list)

    raw_starters = row.get("starters")
    consistent_empty = (
        member_list == []
        and raw_starters == []
        and all(key in row and row[key] in (None, []) for key in ("reserve", "taxi"))
    )

    starting = occupied_ids(raw_starters)
    mapping = map_slots(configuration, raw_starters, players)
    defect = not membership_complete or mapping.status in ("defective", "invalid")
    unavailable = not mapping.interpreted
    if mapping.duplicate_ids:
        duplicates["starters"] = mapping.duplicate_ids

    lists: dict[str, list[str] | None] = {"starters": starting}
    for key in ("reserve", "taxi"):
        value = row.get(key)
        valid_items = _distinct([p for p in (as_list(value) or []) if is_id_string(p)])
        if key not in row:
            lists[key] = None
            unavailable = True
        elif value is None:
            lists[key] = []
        elif is_id_string_list(value):
            lists[key] = _distinct(list(value))
            if _duplicates(list(value)):
                duplicates[key] = _duplicates(list(value))
                defect = True
        else:
            lists[key] = valid_items if as_list(value) is not None else None
            defect = True
            unavailable = True

    groups = {key: set(values or []) for key, values in lists.items()}
    extras = {
        key: sorted(values - member_set) for key, values in groups.items() if values - member_set
    }
    names = list(groups)
    conflicts: set[str] = set()
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            conflicts |= groups[first] & groups[second]
    if extras or conflicts:
        defect = True

    if consistent_empty:
        defect = unavailable = False

    complete = not defect and not unavailable
    placed: set[str] = set()
    for values in groups.values():
        placed |= values
    bench = sorted(member_set - placed) if complete else None
    placements: dict[str, Placement] = {}
    labels: dict[str, Placement] = {"starters": "starter", "reserve": "reserve", "taxi": "taxi"}
    for pid in members:
        hits = [key for key in names if pid in groups[key]]
        if len(hits) == 1:
            placements[pid] = labels[hits[0]]
        elif not hits and bench is not None:
            placements[pid] = "bench"
        else:
            placements[pid] = "unknown"

    categories = [_category(row, key, configuration) for key in ("starters", "reserve", "taxi")]
    affected_players = sorted(conflicts) + [p for v in extras.values() for p in v]
    if not complete:
        if defect:
            issues.append(
                IssueSpec(
                    reason="invalid_source_data",
                    severity="limitation",
                    explanation="Current roster fields contain duplicates, overlaps, IDs outside "
                    "players or malformed values; raw values are kept.",
                    affected=[("section", "context.roster"), ("source", source_id)],
                    next_action=("review_evidence", [], [source_id]),
                    retryable=None,
                )
            )
        if conflicts or extras:
            issues.append(
                IssueSpec(
                    reason="placement_conflict",
                    severity="limitation",
                    explanation="Placement categories conflict with each other or with players; "
                    "affected players have unknown placement.",
                    affected=[("player", p) for p in affected_players if p in placements]
                    + [("section", "context.roster")],
                    next_action=("review_evidence", [], [source_id]),
                    retryable=None,
                )
            )
        issues.append(
            IssueSpec(
                reason="placement_unavailable",
                severity="limitation",
                explanation="Complete current placement cannot be established, so no bench is "
                "derived; known positive placements are kept.",
                affected=[("section", "context.roster")],
                next_action=("review_evidence", [], [source_id]),
                retryable=None,
            )
        )
    return PlacementResult(
        data=RosterData(
            members=members,
            candidate_member_ids=[],
            placements=placements,
            bench_ids=bench,
            categories=categories,
            duplicates=duplicates,
            conflicts=sorted(conflicts),
            extras=extras,
        ),
        membership_coverage="complete" if membership_complete else "partial",
        placement_coverage="complete" if complete else "partial",
        membership_complete=membership_complete or consistent_empty,
        issues=issues,
    )


def _category(row: JsonObject, key: str, configuration: list[str] | None) -> CategoryState:
    state = field_state(row, key)
    value = row.get(key)
    policies = [PLACEMENT_POLICY]
    if key == "starters":
        policies += [SLOT_POLICY, VACANCY_POLICY]
        mapping = map_slots(
            configuration, value if key in row else row.get(key), row.get("players")
        )
        interpretation = (
            "slot_aligned"
            if mapping.interpreted
            else "unknown"
            if state in ("omitted", "null") or value == []
            else "invalid"
        )
    elif state == "null":
        interpretation = "known_empty_by_policy"
    elif state == "omitted":
        interpretation = "unknown"
    elif is_id_string_list(value):
        interpretation = "values"
    elif state == "array":
        interpretation = "mixed"
    else:
        interpretation = "invalid"
    return CategoryState(
        field=key,  # pyright: ignore[reportArgumentType]
        state=state,
        interpretation=interpretation,  # pyright: ignore[reportArgumentType]
        policy_ids=policies,
    )
