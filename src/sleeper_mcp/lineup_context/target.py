"""User and league resolution with scope guards (A02/A03/A05/A10)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sleeper_mcp.json_values import (
    JsonObject,
    as_object,
    field_state,
    is_id_string,
    is_id_string_list,
    is_json_int,
)
from sleeper_mcp.lineup_context.inputs import NormalizedRequest
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.raw import observation, raw_field
from sleeper_mcp.lineup_context.schema import (
    DefaultApplied,
    Guard,
    LeagueEvidence,
    LeagueIdentity,
    LeagueScope,
    UserData,
    UserEvidence,
)
from sleeper_mcp.scope import Target
from sleeper_mcp.sleeper.observation import SourceObservation

LEAGUE_FIELDS = [
    "league_id",
    "sport",
    "season",
    "season_type",
    "status",
    "total_rosters",
    "roster_positions",
    "previous_league_id",
]


def source_failure_issue(
    obs: SourceObservation, stage: str | None, explanation: str, severity: str = "error"
) -> IssueSpec:
    failure = obs.failure
    retryable = failure.retryable if failure else None
    return IssueSpec(
        reason="source_failure" if obs.parse != "failed" else "invalid_source_data",
        severity="error" if severity == "error" else "limitation",
        explanation=explanation + (f" ({failure.detail})" if failure else ""),
        affected=[("source", obs.id)],
        cause=failure.cause if failure else None,
        next_action=("retry", [], [obs.id]) if retryable else ("review_evidence", [], [obs.id]),
        retryable=retryable,
        stage=stage,
    )


# User -----------------------------------------------------------------------------------------


def evaluate_user(obs: SourceObservation, user_id: str) -> tuple[UserEvidence, list[IssueSpec]]:
    if not obs.ok:
        evidence = UserEvidence(
            status="source_failure" if obs.parse != "failed" else "invalid_source_data",
            observed=None,
            data=None,
            source_refs=[obs.id],
        )
        return evidence, [
            source_failure_issue(obs, "user", "The user lookup could not be retrieved.")
        ]
    body = obs.body
    user = as_object(body)
    observed = observation(obs.id, "body", body, ["user_id"], user)
    if body is None:
        return UserEvidence(
            status="user_unresolved", observed=observed, data=None, source_refs=[obs.id]
        ), [
            IssueSpec(
                reason="user_unresolved",
                severity="error",
                explanation=(
                    "The provider returned null for this user ID: it did not resolve the input. "
                    "This does not prove the account never existed."
                ),
                affected=[("input", "user_id"), ("source", obs.id)],
                next_action=("correct_input", ["user_id"], []),
                retryable=False,
                stage="user",
            )
        ]
    if user is None or user.get("user_id") != user_id:
        return UserEvidence(
            status="invalid_source_data", observed=observed, data=None, source_refs=[obs.id]
        ), [
            IssueSpec(
                reason="invalid_source_data",
                severity="error",
                explanation="The user lookup did not return an object with the requested user_id.",
                affected=[("source", obs.id), ("field", "resolution_evidence.user")],
                next_action=("review_evidence", [], [obs.id]),
                retryable=None,
                stage="user",
            )
        ]
    return (
        UserEvidence(
            status="resolved",
            observed=observed,
            data=UserData(user_id=user_id),
            source_refs=[obs.id],
        ),
        [],
    )


# League ---------------------------------------------------------------------------------------


LeagueStatus = Literal[
    "resolved",
    "league_unresolved",
    "invalid_source_data",
    "source_failure",
    "unsupported_scope",
    "invalid_input",
]


@dataclass
class LeagueResult:
    evidence: LeagueEvidence
    issues: list[IssueSpec]
    league: JsonObject | None
    configuration_status: Literal["mapped", "unavailable", "invalid", "not_evaluated"]

    @property
    def resolved(self) -> bool:
        return self.evidence.status == "resolved"


def base_scope(target: Target | None, extension_id: str | None, registry: bool) -> LeagueScope:
    return LeagueScope(
        registry="configured" if registry else "absent",
        extension_id=extension_id,
        target="registered" if target else None,
        automatic_week=target.automatic_week if target else None,
        week_domain=list(target.week_domain) if target else None,
        starting_configuration=list(target.starting_configuration) if target else None,
        guards=[],
    )


def defaults_for(request: NormalizedRequest) -> list[DefaultApplied]:
    if request.sport_defaulted:
        return [DefaultApplied(field="sport", value=request.sport, basis="contract_default")]
    return []


def evaluate_league(
    obs: SourceObservation,
    request: NormalizedRequest,
    target: Target,
    scope: LeagueScope,
) -> LeagueResult:
    defaults = defaults_for(request)

    def result(
        status: LeagueStatus,
        issues: list[IssueSpec],
        league: JsonObject | None = None,
        observed_body: bool = True,
        configuration: Literal["mapped", "unavailable", "invalid", "not_evaluated"] = (
            "not_evaluated"
        ),
    ) -> LeagueResult:
        body = obs.body if obs.ok else None
        data = None
        if status == "resolved" and league is not None:
            data = LeagueIdentity(
                league_id=str(league["league_id"]),
                sport=str(league["sport"]),
                season=str(league["season"]),
                season_type=str(league["season_type"]),
                status=league.get("status"),
            )
        evidence = LeagueEvidence(
            status=status,
            scope=scope,
            defaults=defaults,
            observed=observation(obs.id, "body", body, LEAGUE_FIELDS, as_object(body))
            if observed_body and obs.ok
            else None,
            data=data,
            source_refs=[obs.id],
        )
        return LeagueResult(
            evidence, issues, league if status == "resolved" else None, configuration
        )

    def invalid(explanation: str, field: str) -> LeagueResult:
        return result(
            "invalid_source_data",
            [
                IssueSpec(
                    reason="invalid_source_data",
                    severity="error",
                    explanation=explanation,
                    affected=[("source", obs.id), ("field", f"league.{field}")],
                    next_action=("review_evidence", [], [obs.id]),
                    retryable=None,
                    stage="league",
                )
            ],
        )

    def unsupported(explanation: str, field: str) -> LeagueResult:
        return result(
            "unsupported_scope",
            [
                IssueSpec(
                    reason="unsupported_scope",
                    severity="error",
                    explanation=explanation,
                    affected=[("source", obs.id), ("field", f"league.{field}")],
                    next_action=None,
                    retryable=False,
                    stage="league",
                )
            ],
        )

    if not obs.ok:
        status: LeagueStatus = "source_failure" if obs.parse != "failed" else "invalid_source_data"
        return result(
            status,
            [
                source_failure_issue(
                    obs, "league", "The league lookup could not be retrieved or parsed."
                )
            ],
            observed_body=False,
        )
    body = obs.body
    if body is None:
        return result(
            "league_unresolved",
            [
                IssueSpec(
                    reason="league_unresolved",
                    severity="error",
                    explanation=(
                        "The provider returned null for this league ID: it did not resolve the "
                        "input. The meaning of a null league response is unverified."
                    ),
                    affected=[("input", "league_id"), ("source", obs.id)],
                    next_action=("correct_input", ["league_id"], []),
                    retryable=False,
                    stage="league",
                )
            ],
        )
    league = as_object(body)
    if league is None:
        return invalid("The league lookup did not return a JSON object.", "body")

    guards = scope.guards

    def guard(
        name: str, expected: object, key: str, ok: bool | None, path: str | None = None
    ) -> None:
        guards.append(
            Guard(
                name=name,
                expected=expected,  # pyright: ignore[reportArgumentType]
                observed=raw_field(league, key, path),
                result="pass" if ok else ("invalid" if ok is None else "fail"),
            )
        )

    for key in ("league_id", "sport", "season", "season_type"):
        valid = is_id_string(league.get(key))
        if not valid:
            guard(f"{key}_present", "nonempty string", key, None)
            return invalid(f"The league response has a missing or malformed {key}.", key)
    guard(
        "league_id_matches_request",
        request.league_id,
        "league_id",
        league["league_id"] == request.league_id,
    )
    if league["league_id"] != request.league_id:
        return invalid("The league response names a different league_id.", "league_id")
    if request.season is not None and league["season"] != request.season:
        guard("season_matches_request", request.season, "season", False)
        return result(
            "invalid_input",
            [
                IssueSpec(
                    reason="invalid_input",
                    severity="error",
                    explanation=(
                        f"The league's season is {league['season']}, not {request.season}. "
                        "Renewed leagues are not followed; supply the matching league ID or season."
                    ),
                    affected=[("input", "season"), ("source", obs.id)],
                    next_action=("correct_input", ["season", "league_id"], []),
                    retryable=False,
                    stage="input",
                )
            ],
        )
    settings = as_object(league.get("settings"))
    best_ball = settings.get("best_ball") if settings is not None else None
    if settings is None or not is_json_int(best_ball):
        guards.append(
            Guard(
                name="best_ball_integer_zero",
                expected=target.best_ball,
                observed=raw_field(settings, "best_ball", "settings.best_ball")
                if settings is not None
                else raw_field(league, "settings"),
                result="invalid",
            )
        )
        return invalid(
            "settings.best_ball is missing or not a JSON integer, so the managed-lineup format "
            "cannot be verified. An explicit week does not bypass this guard.",
            "settings.best_ball",
        )
    for key, expected in (
        ("sport", target.sport),
        ("season", target.season),
        ("season_type", target.season_type),
    ):
        ok = league[key] == expected
        guard(f"{key}_matches_target", expected, key, ok)
        if not ok:
            return unsupported(
                f"The league's {key} is outside the supported scope for this league.", key
            )
    bb_ok = best_ball == target.best_ball
    guards.append(
        Guard(
            name="best_ball_integer_zero",
            expected=target.best_ball,
            observed=raw_field(settings, "best_ball", "settings.best_ball"),
            result="pass" if bb_ok else "fail",
        )
    )
    if not bb_ok:
        return unsupported(
            "Best-ball leagues are outside the supported scope.", "settings.best_ball"
        )

    # Starting configuration (A05).
    positions = league.get("roster_positions")
    issues: list[IssueSpec] = []
    configuration: Literal["mapped", "unavailable", "invalid", "not_evaluated"] = "mapped"
    expected_configuration = list(target.starting_configuration)
    if positions is None:
        configuration = "unavailable"
        guard("starting_configuration_matches", expected_configuration, "roster_positions", None)
        issues.append(
            IssueSpec(
                reason="slot_mapping_unavailable",
                severity="limitation",
                explanation=(
                    f"roster_positions is {field_state(league, 'roster_positions')}, so weekly "
                    "assignments cannot be mapped to slots. Raw assignments are kept."
                ),
                affected=[("field", "league.roster_positions"), ("source", obs.id)],
                next_action=("review_evidence", [], [obs.id]),
                retryable=None,
            )
        )
    else:
        starting = (
            [p for p in positions if p != "BN"]  # pyright: ignore[reportUnknownVariableType]
            if is_id_string_list(positions)
            else None
        )
        if starting is None or not positions or not starting:
            configuration = "invalid"
            guard(
                "starting_configuration_matches", expected_configuration, "roster_positions", None
            )
            issues.extend(
                [
                    IssueSpec(
                        reason="slot_mapping_unavailable",
                        severity="limitation",
                        explanation="roster_positions is malformed, so slots cannot be mapped.",
                        affected=[("field", "league.roster_positions"), ("source", obs.id)],
                        next_action=("review_evidence", [], [obs.id]),
                        retryable=None,
                    ),
                    IssueSpec(
                        reason="invalid_source_data",
                        severity="limitation",
                        explanation="roster_positions is not a nonempty array of slot labels.",
                        affected=[("field", "league.roster_positions"), ("source", obs.id)],
                        next_action=("review_evidence", [], [obs.id]),
                        retryable=None,
                    ),
                ]
            )
        else:
            ok = starting == expected_configuration
            guard("starting_configuration_matches", expected_configuration, "roster_positions", ok)
            if not ok:
                return unsupported(
                    "The league's starting configuration differs from the supported configuration "
                    "for this league.",
                    "roster_positions",
                )
    return result("resolved", issues, league, configuration=configuration)
