"""`rest_separate_current_weekly_views_v1`: compare current and weekly views (A07).

Differences are descriptive notices. They never make retrieval partial or a starter invalid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sleeper_mcp.contract import VIEWS_POLICY
from sleeper_mcp.json_values import Json, as_list
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.schema import Placement, Slot, ViewComparison, ViewDifference
from sleeper_mcp.lineup_context.slots import VACANCY

Membership = Literal["present", "absent", "unknown"]


@dataclass
class ViewsResult:
    comparison: ViewComparison
    issues: list[IssueSpec] = field(default_factory=lambda: [])


def compare_views(
    current_ref: str,
    weekly_ref: str | None,
    members: list[str] | None,
    membership_complete: bool,
    placements: dict[str, Placement] | None,
    current_starters: Json,
    weekly_slots: list[Slot] | None,
    weekly_starters: Json,
) -> ViewsResult:
    refs = [current_ref] + ([weekly_ref] if weekly_ref else [])
    if weekly_ref is None or weekly_slots is None:
        return ViewsResult(
            ViewComparison(
                policy_id=VIEWS_POLICY,
                state="weekly_unavailable",
                current_source_ref=current_ref,
                weekly_source_ref=weekly_ref,
                differences=[],
                membership_states={},
                atomic_snapshot=False,
                source_refs=refs,
            )
        )
    member_set = set(members or [])
    states: dict[str, Membership] = {}
    differences: list[ViewDifference] = []
    weekly_ids: list[str] = []
    for slot in weekly_slots:
        if slot.state != "occupied" or slot.player_id is None:
            continue
        pid = slot.player_id
        weekly_ids.append(pid)
        membership: Membership = (
            "present"
            if pid in member_set
            else "absent"
            if members is not None and membership_complete
            else "unknown"
        )
        states[pid] = membership
        placement = (placements or {}).get(pid) if membership == "present" else None
        if membership == "absent":
            differences.append(
                ViewDifference(
                    kind="weekly_player_not_in_current_roster",
                    difference_type="weekly_player_absent_from_current_membership",
                    player_id=pid,
                    weekly_index=slot.index,
                    current_index=None,
                    current_placement=None,
                    indexes=[slot.index],
                )
            )
        elif placement in ("bench", "reserve", "taxi"):
            differences.append(
                ViewDifference(
                    kind="current_weekly_assignment_difference",
                    difference_type="current_placement_differs_from_weekly_assignment",
                    player_id=pid,
                    weekly_index=slot.index,
                    current_index=None,
                    current_placement=placement,
                    indexes=[slot.index],
                )
            )
    current_list = as_list(current_starters) or []
    for index, pid in enumerate(current_list):
        if isinstance(pid, str) and pid != VACANCY and pid not in weekly_ids:
            differences.append(
                ViewDifference(
                    kind="current_weekly_assignment_difference",
                    difference_type="current_starter_not_observed_as_weekly_starter",
                    player_id=pid,
                    weekly_index=None,
                    current_index=index,
                    current_placement=(placements or {}).get(pid),
                    indexes=[index],
                )
            )
    weekly_list = as_list(weekly_starters) or []
    if current_list != weekly_list:
        length = max(len(current_list), len(weekly_list))
        differing = [
            i
            for i in range(length)
            if i >= len(current_list) or i >= len(weekly_list) or current_list[i] != weekly_list[i]
        ]
        differences.append(
            ViewDifference(
                kind="current_weekly_assignment_difference",
                difference_type="ordered_starter_arrays_differ",
                player_id=None,
                weekly_index=None,
                current_index=None,
                current_placement=None,
                indexes=differing,
            )
        )

    issues: list[IssueSpec] = []
    absent = [d.player_id for d in differences if d.kind == "weekly_player_not_in_current_roster"]
    if absent:
        issues.append(
            IssueSpec(
                reason="weekly_player_not_in_current_roster",
                severity="notice",
                explanation="Some weekly starters are not in the complete current roster. This is "
                "an observed relationship, not an invalid starter or a current option.",
                affected=[("player", p) for p in absent if p],
                next_action=None,
                retryable=None,
            )
        )
    other = [d for d in differences if d.kind == "current_weekly_assignment_difference"]
    if other:
        issues.append(
            IssueSpec(
                reason="current_weekly_assignment_difference",
                severity="notice",
                explanation="Current roster placements or starters differ from the weekly "
                "assignments. Both views are kept as observed.",
                affected=[("player", d.player_id) for d in other if d.player_id]
                + [("section", "context.view_comparison")],
                next_action=None,
                retryable=None,
            )
        )
    return ViewsResult(
        ViewComparison(
            policy_id=VIEWS_POLICY,
            state="compared",
            current_source_ref=current_ref,
            weekly_source_ref=weekly_ref,
            differences=differences,
            membership_states=states,
            atomic_snapshot=False,
            source_refs=refs,
        ),
        issues,
    )
