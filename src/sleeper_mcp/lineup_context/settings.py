"""Raw settings and scoring preservation (A10). No defaults, rounding or scoring formulas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sleeper_mcp.json_values import JsonObject, as_object, is_id_string, is_json_number
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.raw import raw_field
from sleeper_mcp.lineup_context.schema import RawField

ScoringState = Literal["omitted", "null", "empty_object", "complete", "invalid"]

# Rule fields relevant to the assessment handoff, kept as uninterpreted raw context.
RULE_FIELDS = [
    "type",
    "best_ball",
    "leg",
    "reserve_slots",
    "taxi_slots",
    "taxi_years",
    "taxi_deadline",
    "taxi_allow_vets",
    "reserve_allow_out",
    "reserve_allow_doubtful",
    "reserve_allow_sus",
    "reserve_allow_cov",
    "reserve_allow_dnr",
    "reserve_allow_na",
    "bench_lock",
    "capacity_override",
    "max_subs",
    "sub_start_time_eligibility",
    "sub_lock_if_starter_active",
]


@dataclass
class SettingsResult:
    scoring_state: ScoringState
    rule_fields: list[RawField]
    issues: list[IssueSpec] = field(default_factory=lambda: [])


def scoring_state(league: JsonObject) -> ScoringState:
    if "scoring_settings" not in league:
        return "omitted"
    raw = league["scoring_settings"]
    if raw is None:
        return "null"
    scoring = as_object(raw)
    if scoring is not None and not scoring:
        return "empty_object"
    if scoring is not None and all(
        is_id_string(key) and is_json_number(value) for key, value in scoring.items()
    ):
        return "complete"
    return "invalid"


def evaluate_settings(league: JsonObject, source_id: str) -> SettingsResult:
    state = scoring_state(league)
    settings = as_object(league.get("settings"))
    rules = [raw_field(settings, key, f"settings.{key}") for key in RULE_FIELDS]
    issues: list[IssueSpec] = []
    if state != "complete":
        meaning = {
            "omitted": "omitted",
            "null": "null",
            "empty_object": "an observed empty object, whose scoring meaning is unestablished",
            "invalid": "malformed",
        }[state]
        issues.append(
            IssueSpec(
                reason="league_settings_unavailable",
                severity="limitation",
                explanation=f"scoring_settings is {meaning}. No preset or zero scoring is "
                "assumed; roster and lineup facts are unaffected.",
                affected=[("field", "league.scoring_settings"), ("source", source_id)],
                next_action=None,
                retryable=None,
            )
        )
        if state == "invalid":
            issues.append(
                IssueSpec(
                    reason="invalid_source_data",
                    severity="limitation",
                    explanation="scoring_settings contains a non-numeric value or is not an "
                    "object; raw values are kept without coercion.",
                    affected=[("field", "league.scoring_settings"), ("source", source_id)],
                    next_action=("review_evidence", [], [source_id]),
                    retryable=None,
                )
            )
    return SettingsResult(state, rules, issues)
