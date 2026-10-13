"""`rest_observed_fantasy_eligibility_v1`: player-detail entries (A09).

Lookup, eligible-position interpretation and target-week applicability stay separate. No
primary-position fallback, and no historical eligibility claim.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from sleeper_mcp.contract import ELIGIBILITY_POLICY, RECOGNIZED_POSITIONS
from sleeper_mcp.json_values import JsonObject, as_list, as_object, field_state, is_id_string
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.raw import raw_field
from sleeper_mcp.lineup_context.schema import (
    PlayerDetailsCoverage,
    PlayerEligibility,
    PlayerEntry,
    PlayerRole,
)

PROFILE_FIELDS = [
    "player_id",
    "full_name",
    "first_name",
    "last_name",
    "position",
    "fantasy_positions",
    "team",
    "status",
    "injury_status",
]
PROFILE_CATALOG = "player_profile"
ROLE_ORDER: list[PlayerRole] = [
    "current_member",
    "current_starter",
    "current_reserve",
    "current_taxi",
    "weekly_starter",
    "weekly_member",
    "raw_only_fragment",
]
# Roles whose details are required for complete context (A10 handoff).
REQUIRED_ROLES: frozenset[PlayerRole] = frozenset({"current_member", "weekly_starter"})


@dataclass
class DetailsResult:
    entries: dict[str, PlayerEntry]
    coverage: PlayerDetailsCoverage
    issues: list[IssueSpec] = field(default_factory=lambda: [])


def interpret_positions(profile: JsonObject) -> PlayerEligibility:
    state = field_state(profile, "fantasy_positions")
    raw = profile.get("fantasy_positions")
    items = as_list(raw)

    def result(
        status: Literal["complete", "partial", "unavailable", "invalid"],
        recognized: list[str] | None = None,
        unfamiliar: list[str] | None = None,
        duplicates: list[str] | None = None,
    ) -> PlayerEligibility:
        return PlayerEligibility(
            policy_id=ELIGIBILITY_POLICY,
            state=status,
            fantasy_positions_state=state,
            recognized=recognized or [],
            unfamiliar=unfamiliar or [],
            duplicates=duplicates or [],
        )

    if items is None:
        return result("unavailable" if state in ("omitted", "null") else "invalid")
    if not items:
        return result("unavailable")
    if not all(is_id_string(item) for item in items):
        return result("invalid")
    tokens = [str(item) for item in items]
    recognized = list(dict.fromkeys(t for t in tokens if t in RECOGNIZED_POSITIONS))
    unfamiliar = list(dict.fromkeys(t for t in tokens if t not in RECOGNIZED_POSITIONS))
    duplicates = sorted(t for t, count in Counter(tokens).items() if count > 1)
    status: Literal["complete", "partial"] = "partial" if unfamiliar or duplicates else "complete"
    return result(status, recognized, unfamiliar, duplicates)


def build_details(
    references: dict[str, list[PlayerRole]],
    profiles: JsonObject | None,
    applicability: Literal["current_observation", "unknown"],
    occupied_weekly_ids: list[str],
    source_ref: str | None,
    directory_failed: bool,
    directory_cause: str | None,
) -> DetailsResult:
    entries: dict[str, PlayerEntry] = {}
    missing: list[str] = []
    invalid: list[str] = []
    for pid in sorted(references):
        roles: list[PlayerRole] = [role for role in ROLE_ORDER if role in references[pid]]
        not_evaluated = PlayerEligibility(
            policy_id=ELIGIBILITY_POLICY,
            state="not_evaluated",
            fantasy_positions_state=None,
            recognized=[],
            unfamiliar=[],
            duplicates=[],
        )
        required = bool(REQUIRED_ROLES & set(roles))
        if profiles is None:
            entries[pid] = PlayerEntry(
                roles=roles,
                lookup_status="directory_unavailable",
                key_binding=None,
                profile_container=None,
                profile_fields_ref=PROFILE_CATALOG,
                fields=None,
                eligibility=not_evaluated,
                applicability=applicability,
            )
            continue
        if pid not in profiles:
            entries[pid] = PlayerEntry(
                roles=roles,
                lookup_status="missing_entry",
                key_binding=None,
                profile_container="omitted",
                profile_fields_ref=PROFILE_CATALOG,
                fields=None,
                eligibility=not_evaluated,
                applicability=applicability,
            )
            if required:
                missing.append(pid)
            continue
        raw = profiles[pid]
        profile = as_object(raw)
        container = field_state(profiles, pid)
        if profile is None:
            entries[pid] = PlayerEntry(
                roles=roles,
                lookup_status="invalid_profile",
                key_binding=None,
                profile_container=container,
                profile_fields_ref=PROFILE_CATALOG,
                fields=None,
                eligibility=not_evaluated,
                applicability=applicability,
            )
            if required:
                invalid.append(pid)
            continue
        fields = [raw_field(profile, key) for key in PROFILE_FIELDS]
        if "player_id" in profile and profile["player_id"] != pid:
            entries[pid] = PlayerEntry(
                roles=roles,
                lookup_status="invalid_profile",
                key_binding="conflict",
                profile_container=container,
                profile_fields_ref=PROFILE_CATALOG,
                fields=fields,
                eligibility=not_evaluated,
                applicability=applicability,
            )
            if required:
                invalid.append(pid)
            continue
        eligibility = interpret_positions(profile)
        entries[pid] = PlayerEntry(
            roles=roles,
            lookup_status="found",
            key_binding="directory_key_and_player_id"
            if "player_id" in profile
            else "directory_key",
            profile_container=container,
            profile_fields_ref=PROFILE_CATALOG,
            fields=fields,
            eligibility=eligibility,
            applicability=applicability,
        )
        if required:
            if eligibility.state == "invalid":
                invalid.append(pid)
            elif eligibility.state == "unavailable" or (
                eligibility.state == "partial"
                and applicability == "current_observation"
                and pid in occupied_weekly_ids
            ):
                missing.append(pid)

    issues: list[IssueSpec] = []
    refs = [source_ref] if source_ref else []
    required_ids = [p for p, e in entries.items() if REQUIRED_ROLES & set(e.roles)]
    if profiles is None:
        if required_ids:
            issues.append(
                IssueSpec(
                    reason="player_details_unavailable",
                    severity="limitation",
                    explanation="The player directory is unavailable, so no profiles or eligible "
                    "positions are returned. Player IDs, placements and assignments are kept.",
                    affected=[("section", "context.player_details")],
                    cause=directory_cause,
                    next_action=("retry", [], refs) if directory_failed else None,
                    retryable=None,
                )
            )
        if directory_failed and source_ref:
            issues.append(
                IssueSpec(
                    reason="source_failure",
                    severity="limitation",
                    explanation="The player directory refresh attempt failed.",
                    affected=[("source", source_ref)],
                    cause=directory_cause,
                    next_action=None,
                    retryable=None,
                )
            )
    else:
        if missing or invalid:
            issues.append(
                IssueSpec(
                    reason="player_details_unavailable",
                    severity="limitation",
                    explanation="Some referenced players have no directory entry, an invalid "
                    "profile, or eligible positions that cannot be fully interpreted. Their IDs, "
                    "placements and assignments are kept; no primary-position fallback is used.",
                    affected=[("player", p) for p in sorted(set(missing + invalid))],
                    next_action=None,
                    retryable=None,
                )
            )
        if invalid:
            issues.append(
                IssueSpec(
                    reason="invalid_source_data",
                    severity="limitation",
                    explanation="Some directory profiles are malformed or bound to a different "
                    "player_id.",
                    affected=[("player", p) for p in invalid] + [("source", r) for r in refs],
                    next_action=("review_evidence", [], refs),
                    retryable=None,
                )
            )

    if not entries:
        coverage = PlayerDetailsCoverage(lookup="complete", eligibility="complete")
    elif profiles is None:
        coverage = PlayerDetailsCoverage(lookup="unavailable", eligibility="unavailable")
    else:
        found = [e for e in entries.values() if e.lookup_status == "found"]
        lookup = "complete" if len(found) == len(entries) else "partial" if found else "unavailable"
        complete = [e for e in found if e.eligibility.state == "complete"]
        eligibility = (
            "complete" if len(complete) == len(entries) else "partial" if found else "unavailable"
        )
        coverage = PlayerDetailsCoverage(lookup=lookup, eligibility=eligibility)
    return DetailsResult(entries, coverage, issues)
