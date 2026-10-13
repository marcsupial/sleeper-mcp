from __future__ import annotations

from typing import Any

import pytest

from sleeper_mcp.lineup_context.inputs import week_domain_issue
from sleeper_mcp.lineup_context.week import automatic_week, explicit_week, needs_state
from sleeper_mcp.scope import ScopeRegistry, Target, load_registry
from sleeper_mcp.sleeper.observation import Failure, SourceObservation
from sleeper_mcp.sleeper.routes import Route

from ..helpers import REGISTRY_PATH

REGISTRY = load_registry(REGISTRY_PATH)
assert isinstance(REGISTRY, ScopeRegistry)
T2026: Target = REGISTRY.targets["test-league-redraft"]
T2023: Target = REGISTRY.targets["test-league-kdef-2023"]


def league(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"season": "2026", "status": "in_season", "settings": {"leg": 6}}
    base.update(changes)
    return base


def state(body: Any) -> SourceObservation:
    obs = SourceObservation(id="src-state", route=Route.STATE_NFL, url="u", coordinate={})
    obs.parse = "parsed"
    obs.body = body
    return obs


def agreeing(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "season": "2026",
        "season_type": "regular",
        "week": 6,
        "leg": 6,
        "display_week": 6,
    }
    base.update(changes)
    return base


def test_four_agreeing_signals_resolve_the_provider_observed_week() -> None:
    result = automatic_week(T2026, league(), "src-league", state(agreeing()))
    assert result.week == 6
    assert result.evidence.basis == "provider_observed_week"
    assert result.evidence.pinned
    assert [s.name for s in result.evidence.signals] == [
        "state.week",
        "state.leg",
        "state.display_week",
        "league.settings.leg",
    ]
    assert result.issues == []


def test_observed_divergence_requires_an_explicit_week() -> None:
    # state.week = leg = 6, display_week = 5, league leg = 5.
    result = automatic_week(
        T2026, league(settings={"leg": 5}), "src-league", state(agreeing(display_week=5))
    )
    assert result.week is None
    assert result.evidence.status == "unresolved"
    assert result.issues[0].reason == "week_resolution_required"
    assert result.issues[0].next_action == ("provide_week", ["week"], [])


@pytest.mark.parametrize(
    ("league_changes", "state_body"),
    [
        ({}, agreeing(season_type="post")),
        ({}, agreeing(season="2027")),
        ({"status": "complete"}, agreeing()),
        ({"status": "pre_draft"}, agreeing()),
        ({"status": "drafting"}, agreeing()),
        ({}, agreeing(week=True, leg=True, display_week=True)),
        ({}, agreeing(week="6")),
        ({"settings": {}}, agreeing()),
        ({}, agreeing(week=19, leg=19, display_week=19)),
        ({}, None),
        ({}, []),
    ],
)
def test_failed_guards_leave_the_week_unresolved(
    league_changes: dict[str, Any], state_body: Any
) -> None:
    result = automatic_week(T2026, league(**league_changes), "src-league", state(state_body))
    assert result.week is None
    assert [i.reason for i in result.issues] == ["week_resolution_required"]


def test_state_failure_adds_a_separate_source_failure() -> None:
    failed = SourceObservation(id="src-state", route=Route.STATE_NFL, url="u", coordinate={})
    failed.failure = Failure("timeout", "the attempt timed out", True)
    result = automatic_week(T2026, league(), "src-league", failed)
    assert [i.reason for i in result.issues] == ["week_resolution_required", "source_failure"]


def test_archived_target_always_needs_an_explicit_week() -> None:
    assert not needs_state(T2023, None)
    result = automatic_week(T2023, league(season="2023"), "src-league", None)
    assert result.week is None
    assert result.evidence.automatic_week_mode == "explicit_week_only"
    assert result.issues[0].reason == "week_resolution_required"


def test_explicit_week_never_reads_state() -> None:
    assert not needs_state(T2026, 5)
    result = explicit_week(5, T2026)
    assert result.week == 5
    assert result.evidence.basis == "explicit_input"
    assert result.evidence.source_refs == []


@pytest.mark.parametrize("week", [0, 19, -1])
def test_explicit_week_outside_domain(week: int) -> None:
    issue = week_domain_issue(week, (1, 18))
    assert issue is not None and issue.reason == "invalid_input"
    assert week_domain_issue(18, (1, 18)) is None
