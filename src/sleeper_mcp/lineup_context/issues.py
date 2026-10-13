"""Issue vocabulary: closed reason codes, severities, next actions and retry advice."""

from __future__ import annotations

from dataclasses import dataclass, field

from sleeper_mcp.lineup_context.schema import (
    AffectedRef,
    Issue,
    NextAction,
    NextActionType,
    ReasonCode,
    Severity,
)

# Notices describe observed relationships or uncertainty; they never downgrade an outcome.
NOTICE_REASONS: frozenset[str] = frozenset(
    {
        "weekly_player_not_in_current_roster",
        "current_weekly_assignment_difference",
        "source_observation_changed",
        "source_cache_stale",
    }
)

# Error precedence (A10): inputs/scope, then user, league, roster source, association.
ERROR_STAGES = ("input", "user", "league", "rosters", "association")


@dataclass
class IssueSpec:
    reason: ReasonCode
    severity: Severity
    explanation: str
    affected: list[tuple[str, str]] = field(default_factory=lambda: [])
    cause: str | None = None
    next_action: tuple[NextActionType, list[str], list[str]] | None = None
    retryable: bool | None = None
    stage: str | None = None


class IssueLog:
    """Collects issue specs in observation order."""

    def __init__(self) -> None:
        self.specs: list[IssueSpec] = []

    def add(self, spec: IssueSpec) -> IssueSpec:
        self.specs.append(spec)
        return spec

    def extend(self, specs: list[IssueSpec]) -> None:
        self.specs.extend(specs)

    def has(self, reason: str, severity: Severity | None = None) -> bool:
        return any(
            s.reason == reason and (severity is None or s.severity == severity) for s in self.specs
        )

    @property
    def errors(self) -> list[IssueSpec]:
        return [s for s in self.specs if s.severity == "error"]

    @property
    def limitations(self) -> list[IssueSpec]:
        return [s for s in self.specs if s.severity == "limitation"]


def ordered(specs: list[IssueSpec]) -> list[IssueSpec]:
    """Errors in precedence order, then limitations, then notices; stable within groups."""

    def key(item: tuple[int, IssueSpec]) -> tuple[int, int, int]:
        index, spec = item
        severity_rank = {"error": 0, "limitation": 1, "notice": 2}[spec.severity]
        stage_rank = (
            ERROR_STAGES.index(spec.stage)
            if spec.severity == "error" and spec.stage in ERROR_STAGES
            else len(ERROR_STAGES)
        )
        return (severity_rank, stage_rank, index)

    return [spec for _, spec in sorted(enumerate(specs), key=key)]


def build_issues(specs: list[IssueSpec]) -> list[Issue]:
    issues: list[Issue] = []
    for number, spec in enumerate(ordered(specs), start=1):
        action = None
        if spec.next_action is not None:
            kind, fields, refs = spec.next_action
            action = NextAction(type=kind, fields=list(fields), refs=list(refs))
        issues.append(
            Issue(
                id=f"issue-{number}",
                reason=spec.reason,
                severity=spec.severity,
                cause=spec.cause,
                affected=[
                    AffectedRef.model_validate({"kind": kind, "ref": ref})
                    for kind, ref in spec.affected
                ],
                explanation=spec.explanation,
                next_action=action,
                retryable=spec.retryable,
            )
        )
    return issues
