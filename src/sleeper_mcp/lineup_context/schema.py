"""Pydantic models for the `get_lineup_context.v1` object (D5).

Normalized models forbid unexpected keys. Raw provider data is typed `JsonValue`, so custom keys
and exact values survive.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator, model_validator

from sleeper_mcp.json_values import FieldState

SCHEMA_VERSION = "get_lineup_context.v1"
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
REQUEST_FIELDS = ("user_id", "league_id", "roster_id", "sport", "season", "week")

Outcome = Literal["complete", "partial", "selection_required", "error"]
Coverage = Literal["complete", "partial", "unavailable", "unknown"]
Severity = Literal["error", "limitation", "notice"]
Placement = Literal["starter", "reserve", "taxi", "bench", "unknown"]
SlotState = Literal["occupied", "empty", "unknown"]
GuardResult = Literal["pass", "fail", "invalid", "unknown", "not_evaluated"]

ReasonCode = Literal[
    "invalid_input",
    "unsupported_scope",
    "user_unresolved",
    "league_unresolved",
    "source_failure",
    "invalid_source_data",
    "roster_not_found",
    "association_unavailable",
    "roster_mismatch",
    "roster_selection_required",
    "week_resolution_required",
    "lineup_unavailable",
    "roster_data_unavailable",
    "placement_unavailable",
    "placement_conflict",
    "slot_mapping_unavailable",
    "league_settings_unavailable",
    "player_details_unavailable",
    "weekly_player_not_in_current_roster",
    "current_weekly_assignment_difference",
    "source_observation_changed",
    "source_cache_stale",
]

NextActionType = Literal[
    "provide_week", "select_roster", "correct_input", "retry", "review_evidence"
]
AffectedKind = Literal["input", "section", "field", "source", "roster", "player", "slot"]

PlayerRole = Literal[
    "current_member",
    "current_starter",
    "current_reserve",
    "current_taxi",
    "weekly_starter",
    "weekly_member",
    "raw_only_fragment",
]
LookupStatus = Literal["found", "missing_entry", "directory_unavailable", "invalid_profile"]
EligibilityState = Literal["complete", "partial", "unavailable", "invalid", "not_evaluated"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Shared records -------------------------------------------------------------------------------


class RawField(Strict):
    """One inspected raw field: its JSON state and exact value (null when omitted)."""

    path: str
    state: FieldState
    value: JsonValue


class Observation(Strict):
    """A raw observation bound to one source; never merged with another read."""

    source_ref: str
    selector: str
    container: FieldState
    fields: list[RawField]


class Guard(Strict):
    name: str
    expected: JsonValue
    observed: RawField | None
    result: GuardResult


# Header ---------------------------------------------------------------------------------------


class Resolved(Strict):
    user_id: str | None
    league_id: str | None
    roster_id: int | None
    sport: str | None
    season: str | None
    season_type: str | None
    week: int | None


class SourceAttempt(Strict):
    index: int
    started_at: str
    ended_at: str
    outcome: Literal["response", "timeout", "deadline_exceeded", "connect_error", "transport_error"]
    http_status: int | None


class SourceFailure(Strict):
    cause: str
    detail: str


class Source(Strict):
    id: str
    route: str
    url: str
    coordinate: dict[str, JsonValue]
    origin: Literal["this_call", "local_reuse"]
    attempts: list[SourceAttempt]
    request_started_at: str | None
    body_completed_at: str | None
    http_status: int | None
    headers: dict[str, str | None] | None
    body_sha256: str | None
    body_bytes: int | None
    parse: Literal["parsed", "failed", "not_attempted"]
    failure: SourceFailure | None


class AffectedRef(Strict):
    kind: AffectedKind
    ref: str


class NextAction(Strict):
    type: NextActionType
    fields: list[str]
    refs: list[str]


class Issue(Strict):
    id: str
    reason: ReasonCode
    severity: Severity
    cause: str | None
    affected: list[AffectedRef]
    explanation: str
    next_action: NextAction | None
    retryable: bool | None


# Resolution evidence --------------------------------------------------------------------------


class UserData(Strict):
    user_id: str


class UserEvidence(Strict):
    status: Literal[
        "resolved", "user_unresolved", "invalid_source_data", "source_failure", "not_attempted"
    ]
    observed: Observation | None
    data: UserData | None
    source_refs: list[str]


class LeagueScope(Strict):
    registry: Literal["configured", "absent", "refused"]
    extension_id: str | None
    target: (
        Literal["registered", "structural_match", "not_registered", "unsupported_comparison"] | None
    )
    automatic_week: Literal["agreeing_same_season_four_signals", "explicit_week_only"] | None
    week_domain: list[int] | None
    starting_configuration: list[str] | None
    guards: list[Guard]


class DefaultApplied(Strict):
    field: str
    value: JsonValue
    basis: Literal["contract_default"]


class LeagueIdentity(Strict):
    league_id: str
    sport: str
    season: str
    season_type: str
    status: JsonValue


class LeagueEvidence(Strict):
    status: Literal[
        "resolved",
        "league_unresolved",
        "invalid_source_data",
        "source_failure",
        "unsupported_scope",
        "invalid_input",
        "not_attempted",
    ]
    scope: LeagueScope
    defaults: list[DefaultApplied]
    observed: Observation | None
    data: LeagueIdentity | None
    source_refs: list[str]


class Candidate(Strict):
    league_id: str
    roster_id: int
    match: Literal["owner", "co_owner", "both"]
    source_fields: list[str]
    source_refs: list[str]
    label: str | None


class RowEvidence(Strict):
    row_index: int
    roster_id: RawField
    league_id: RawField
    owner_id: RawField
    co_owners: RawField
    interpretation: Literal["positive", "excluded", "unknown", "invalid", "structural_defect"]


class Enumeration(Strict):
    coverage: Literal["complete", "unknown", "unavailable"]
    row_count: int | None
    total_rosters: RawField | None
    structural_defects: list[str]


class AssociationEvidence(Strict):
    policy_id: str
    status: Literal[
        "selected",
        "selection_required",
        "roster_not_found",
        "association_unavailable",
        "roster_mismatch",
        "invalid_source_data",
        "source_failure",
        "invalid_input",
        "not_attempted",
    ]
    selection_basis: Literal["automatic_unique", "explicit_roster_id"] | None
    requested_roster_id: int | None
    selected: Candidate | None
    candidates: list[Candidate]
    enumeration: Enumeration
    uniqueness: Literal["supported", "multiple", "unknown", "not_applicable"]
    row_evidence: list[RowEvidence]
    source_refs: list[str]


class WeekSignal(Strict):
    name: str
    observed: RawField
    source_ref: str


class WeekEvidence(Strict):
    policy_id: str
    status: Literal["resolved", "unresolved", "invalid_input", "not_attempted"]
    basis: Literal["explicit_input", "provider_observed_week"] | None
    automatic_week_mode: Literal["agreeing_same_season_four_signals", "explicit_week_only"] | None
    week_domain: list[int] | None
    signals: list[WeekSignal]
    guards: list[Guard]
    pinned: bool
    source_refs: list[str]


class ResolutionEvidence(Strict):
    user: UserEvidence
    league: LeagueEvidence
    association: AssociationEvidence
    week: WeekEvidence


# Context sections -----------------------------------------------------------------------------


class LeagueCoverage(Strict):
    configuration: Coverage
    scoring: Coverage


class StartingSlot(Strict):
    index: int
    label: str


class LeagueData(Strict):
    league_id: str
    sport: str
    season: str
    season_type: str
    status: JsonValue
    roster_positions: JsonValue
    roster_positions_state: FieldState
    starting_slots: list[StartingSlot] | None
    slot_rule_labels: list[str] | None
    mapping_basis: str | None
    original_week_configuration: Literal["not_established"]
    settings: JsonValue
    scoring_settings: JsonValue
    scoring_state: Literal["omitted", "null", "empty_object", "complete", "invalid"]
    rule_fields: list[RawField]


class LeagueSection(Strict):
    coverage: LeagueCoverage
    observed: list[Observation]
    data: LeagueData
    source_refs: list[str]


class RosterCoverage(Strict):
    membership: Coverage
    placement: Coverage


class CategoryState(Strict):
    field: Literal["starters", "reserve", "taxi"]
    state: FieldState
    interpretation: Literal[
        "values", "known_empty_by_policy", "slot_aligned", "unknown", "invalid", "mixed"
    ]
    policy_ids: list[str]


class RosterData(Strict):
    members: list[str] | None
    candidate_member_ids: list[str]
    placements: dict[str, Placement] | None
    bench_ids: list[str] | None
    categories: list[CategoryState]
    duplicates: dict[str, list[str]]
    conflicts: list[str]
    extras: dict[str, list[str]]


class RosterScope(Strict):
    league_id: str
    roster_id: int
    season: str
    view: Literal["latest_retrieved_selected_league_roster"]
    original_week_membership: Literal["not_established"]


class RosterSection(Strict):
    coverage: RosterCoverage
    observed: list[Observation]
    data: RosterData
    scope: RosterScope
    source_refs: list[str]


class WeeklyCoverage(Strict):
    assignments: Coverage
    mapping: Coverage


class Assignment(Strict):
    index: int
    raw: JsonValue


class Slot(Strict):
    index: int
    label: str
    player_id: str | None
    state: SlotState
    raw_assignment: JsonValue
    policy_ids: list[str]


class WeeklyData(Strict):
    row_state: Literal[
        "selected_row", "no_selected_row", "invalid_body", "source_failure", "not_attempted"
    ]
    starters_state: FieldState | None
    assignments: list[Assignment] | None
    slots: list[Slot] | None
    mapping_basis: str | None
    duplicate_ids: list[str]
    scoring_effect: Literal["not_established"]
    autosub_finality: Literal["not_established"]


class WeeklyScope(Strict):
    league_id: str
    roster_id: int
    season: str
    week: int | None


class WeeklySection(Strict):
    coverage: WeeklyCoverage
    observed: list[Observation]
    data: WeeklyData
    scope: WeeklyScope
    source_refs: list[str]


class PlayerEligibility(Strict):
    policy_id: str
    state: EligibilityState
    fantasy_positions_state: FieldState | None
    recognized: list[str]
    unfamiliar: list[str]
    duplicates: list[str]


class PlayerEntry(Strict):
    roles: list[PlayerRole]
    lookup_status: LookupStatus
    key_binding: Literal["directory_key", "directory_key_and_player_id", "conflict"] | None
    profile_container: FieldState | None
    profile_fields_ref: str
    fields: list[RawField] | None
    eligibility: PlayerEligibility
    applicability: Literal["current_observation", "unknown"]


class PlayerDetailsCoverage(Strict):
    lookup: Coverage
    eligibility: Coverage


class PlayerDetailsScope(Strict):
    directory_source_ref: str | None
    directory_origin: Literal["this_call", "local_reuse"] | None
    applicability_basis: str
    historical_eligibility: Literal["not_established"]


class PlayerDetailsSection(Strict):
    coverage: PlayerDetailsCoverage
    data: dict[str, PlayerEntry]
    scope: PlayerDetailsScope
    source_refs: list[str]


class ViewDifference(Strict):
    kind: Literal["weekly_player_not_in_current_roster", "current_weekly_assignment_difference"]
    difference_type: Literal[
        "weekly_player_absent_from_current_membership",
        "current_placement_differs_from_weekly_assignment",
        "current_starter_not_observed_as_weekly_starter",
        "ordered_starter_arrays_differ",
    ]
    player_id: str | None
    weekly_index: int | None
    current_index: int | None
    current_placement: Placement | None
    indexes: list[int]


class ViewComparison(Strict):
    policy_id: str
    state: Literal["compared", "weekly_unavailable", "current_unavailable"]
    current_source_ref: str | None
    weekly_source_ref: str | None
    differences: list[ViewDifference]
    membership_states: dict[str, Literal["present", "absent", "unknown"]]
    atomic_snapshot: Literal[False]
    source_refs: list[str]


class HttpAge(Strict):
    state: Literal["present", "absent", "invalid"]
    seconds: int | None
    raw: str | None


class CacheControl(Strict):
    raw: str | None
    max_age: int | None
    s_maxage: int | None
    no_store: bool
    no_cache: bool
    must_revalidate: bool


class FreshnessSource(Strict):
    source_ref: str
    route: str
    origin: Literal["this_call", "local_reuse"]
    acquired_at: str | None
    body_completed_at: str | None
    local_age_seconds: float | None
    http_age: HttpAge
    cache_control: CacheControl
    stale_delivery: bool | None
    stale_basis: str


class ObservationWindow(Strict):
    start: str
    end: str
    duration_seconds: float


class FreshnessSection(Strict):
    policy_id: str
    per_source: list[FreshnessSource]
    observation_window: ObservationWindow | None
    directory_reuse_age_seconds: float | None
    origin_freshness: Literal["unestablished"]


class Context(Strict):
    league: LeagueSection
    roster: RosterSection
    weekly_lineup: WeeklySection
    player_details: PlayerDetailsSection
    view_comparison: ViewComparison
    freshness: FreshnessSection


# Definitions ----------------------------------------------------------------------------------


class PolicyDefinition(Strict):
    assumption: str
    description: str


class Definitions(Strict):
    policies: dict[str, PolicyDefinition]
    slot_rules: dict[str, list[str]]
    field_catalog: dict[str, list[str]]


# Root -----------------------------------------------------------------------------------------


def _root_schema_extra(schema: dict[str, Any]) -> None:
    schema["$schema"] = JSON_SCHEMA_DIALECT
    properties = schema["properties"]
    properties["requested"] = {
        "type": "object",
        "description": "Only the supplied inputs, with their original JSON types and values.",
        "propertyNames": {"enum": list(REQUEST_FIELDS)},
    }
    schema["allOf"] = [
        {
            "if": {"properties": {"outcome": {"enum": ["complete", "partial"]}}},
            "then": {"properties": {"context": {"type": "object"}}},
        },
        {
            "if": {"properties": {"outcome": {"enum": ["selection_required", "error"]}}},
            "then": {"properties": {"context": {"type": "null"}}},
        },
    ]


class LineupContextV1(Strict):
    model_config = ConfigDict(
        extra="forbid",
        title="get_lineup_context.v1",
        json_schema_extra=_root_schema_extra,
    )

    schema_version: Literal["get_lineup_context.v1"]
    outcome: Outcome
    requested: dict[str, JsonValue]
    resolved: Resolved
    resolution_evidence: ResolutionEvidence
    context: Context | None
    issues: list[Issue]
    sources: list[Source]
    definitions: Definitions

    @field_validator("requested")
    @classmethod
    def _only_known_inputs(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        unknown = set(value) - set(REQUEST_FIELDS)
        if unknown:
            raise ValueError("requested contains keys that are not tool inputs")
        return value

    @model_validator(mode="after")
    def _outcome_relationships(self) -> LineupContextV1:
        selected = self.outcome in ("complete", "partial")
        if selected != (self.context is not None):
            raise ValueError("context must be present exactly for complete and partial outcomes")
        if selected:
            for key in ("user_id", "league_id", "roster_id", "sport", "season", "season_type"):
                if getattr(self.resolved, key) is None:
                    raise ValueError(f"a selected outcome needs resolved.{key}")
        if self.outcome == "selection_required":
            if self.resolved.roster_id is not None:
                raise ValueError("selection_required cannot have a resolved roster")
            if not self.resolution_evidence.association.candidates:
                raise ValueError("selection_required needs candidates")
        severities = {issue.severity for issue in self.issues}
        if (self.outcome == "error") != ("error" in severities):
            raise ValueError("error outcomes, and only they, carry error-severity issues")
        if self.outcome == "partial" and "limitation" not in severities:
            raise ValueError("partial outcomes need a limiting issue")
        if self.outcome == "complete" and "limitation" in severities:
            raise ValueError("complete outcomes cannot carry limiting issues")
        return self
