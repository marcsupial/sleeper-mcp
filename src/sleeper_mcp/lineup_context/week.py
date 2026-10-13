"""`nfl_agreeing_week_signals_v1`: explicit week, or a guarded provider-observed week (A03)."""

from __future__ import annotations

from dataclasses import dataclass

from sleeper_mcp.contract import WEEK_POLICY
from sleeper_mcp.json_values import JsonObject, as_object, is_json_int
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.raw import raw_field
from sleeper_mcp.lineup_context.schema import Guard, WeekEvidence, WeekSignal
from sleeper_mcp.scope import Target
from sleeper_mcp.sleeper.observation import SourceObservation


@dataclass
class WeekResult:
    evidence: WeekEvidence
    week: int | None
    issues: list[IssueSpec]


def explicit_week(week: int, target: Target) -> WeekResult:
    return WeekResult(
        WeekEvidence(
            policy_id=WEEK_POLICY,
            status="resolved",
            basis="explicit_input",
            automatic_week_mode=target.automatic_week,
            week_domain=list(target.week_domain),
            signals=[],
            guards=[],
            pinned=True,
            source_refs=[],
        ),
        week,
        [],
    )


def not_attempted(target: Target | None) -> WeekEvidence:
    return WeekEvidence(
        policy_id=WEEK_POLICY,
        status="not_attempted",
        basis=None,
        automatic_week_mode=target.automatic_week if target else None,
        week_domain=list(target.week_domain) if target else None,
        signals=[],
        guards=[],
        pinned=False,
        source_refs=[],
    )


def needs_state(target: Target, explicit: int | None) -> bool:
    return explicit is None and target.automatic_week == "agreeing_same_season_four_signals"


def automatic_week(
    target: Target,
    league: JsonObject,
    league_source: str,
    state_obs: SourceObservation | None,
) -> WeekResult:
    guards: list[Guard] = []
    signals: list[WeekSignal] = []
    refs = [league_source]
    issues: list[IssueSpec] = []
    low, high = target.week_domain

    def add(
        name: str, expected: object, obj: JsonObject | None, key: str, path: str, ok: bool
    ) -> None:
        guards.append(
            Guard(
                name=name,
                expected=expected,  # pyright: ignore[reportArgumentType]
                observed=raw_field(obj, key, path),
                result="pass" if ok else "fail",
            )
        )

    explicit_only = target.automatic_week == "explicit_week_only"
    guards.append(
        Guard(
            name="target_allows_automatic_week",
            expected="agreeing_same_season_four_signals",
            observed=None,
            result="fail" if explicit_only else "pass",
        )
    )
    state: JsonObject | None = None
    if not explicit_only:
        add(
            "league_status_in_season",
            "in_season",
            league,
            "status",
            "league.status",
            league.get("status") == "in_season",
        )
        if state_obs is not None:
            refs.append(state_obs.id)
            if state_obs.ok:
                state = as_object(state_obs.body)
            else:
                issues.append(
                    IssueSpec(
                        reason="source_failure",
                        severity="limitation",
                        explanation="NFL state could not be retrieved for automatic week "
                        "resolution.",
                        affected=[("source", state_obs.id)],
                        cause=state_obs.failure.cause if state_obs.failure else None,
                        next_action=("provide_week", ["week"], []),
                        retryable=state_obs.failure.retryable if state_obs.failure else None,
                    )
                )
        guards.append(
            Guard(
                name="state_available",
                expected="object",
                observed=None,
                result="pass" if state is not None else "fail",
            )
        )
        settings = as_object(league.get("settings"))
        if state is not None:
            add(
                "state_season_matches_league",
                league.get("season"),
                state,
                "season",
                "state.season",
                state.get("season") == league.get("season"),
            )
            add(
                "state_season_type_regular",
                "regular",
                state,
                "season_type",
                "state.season_type",
                state.get("season_type") == "regular",
            )
            assert state_obs is not None
            for key in ("week", "leg", "display_week"):
                signals.append(
                    WeekSignal(
                        name=f"state.{key}",
                        observed=raw_field(state, key, f"state.{key}"),
                        source_ref=state_obs.id,
                    )
                )
        signals.append(
            WeekSignal(
                name="league.settings.leg",
                observed=raw_field(settings, "leg", "league.settings.leg"),
                source_ref=league_source,
            )
        )
        values = [s.observed.value for s in signals]
        valid = len(values) == 4 and all(is_json_int(v) and low <= v <= high for v in values)
        guards.append(
            Guard(
                name="signals_are_integers_in_domain",
                expected=[low, high],
                observed=None,
                result="pass" if valid else "fail",
            )
        )
        agree = valid and len(set(values)) == 1  # pyright: ignore[reportArgumentType]
        guards.append(
            Guard(
                name="four_signals_agree",
                expected=True,
                observed=None,
                result="pass" if agree else "fail",
            )
        )

    resolved = all(g.result == "pass" for g in guards)
    week = signals[0].observed.value if resolved else None
    evidence = WeekEvidence(
        policy_id=WEEK_POLICY,
        status="resolved" if resolved else "unresolved",
        basis="provider_observed_week" if resolved else None,
        automatic_week_mode=target.automatic_week,
        week_domain=[low, high],
        signals=signals,
        guards=guards,
        pinned=resolved,
        source_refs=refs,
    )
    if not resolved:
        failed = [g.name for g in guards if g.result != "pass"]
        explanation = (
            "This archived league always needs an explicit week."
            if explicit_only
            else "The week could not be resolved automatically (failed guards: "
            + ", ".join(failed)
            + "). Pass an explicit week."
        )
        issues.insert(
            0,
            IssueSpec(
                reason="week_resolution_required",
                severity="limitation",
                explanation=explanation,
                affected=[("section", "resolution_evidence.week"), ("input", "week")],
                next_action=("provide_week", ["week"], []),
                retryable=False,
            ),
        )
    return WeekResult(evidence, week if is_json_int(week) else None, issues)
