from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sleeper_mcp.lineup_context.freshness import evaluate_freshness, parse_age, parse_cache_control
from sleeper_mcp.lineup_context.settings import evaluate_settings, scoring_state
from sleeper_mcp.sleeper.observation import Attempt, SourceObservation
from sleeper_mcp.sleeper.routes import Route


@pytest.mark.parametrize(
    ("league", "state", "reasons"),
    [
        ({}, "omitted", ["league_settings_unavailable"]),
        ({"scoring_settings": None}, "null", ["league_settings_unavailable"]),
        ({"scoring_settings": {}}, "empty_object", ["league_settings_unavailable"]),
        (
            {"scoring_settings": {"rec": "0.5"}},
            "invalid",
            ["league_settings_unavailable", "invalid_source_data"],
        ),
        (
            {"scoring_settings": {"rec": True}},
            "invalid",
            ["league_settings_unavailable", "invalid_source_data"],
        ),
        (
            {"scoring_settings": "ppr"},
            "invalid",
            ["league_settings_unavailable", "invalid_source_data"],
        ),
        ({"scoring_settings": {"rec": 0.5, "pass_int": -3, "zero": 0}}, "complete", []),
    ],
)
def test_scoring_states(league: dict[str, Any], state: str, reasons: list[str]) -> None:
    assert scoring_state(league) == state
    result = evaluate_settings({**league, "settings": {"best_ball": 0}}, "src-league")
    assert [i.reason for i in result.issues] == reasons


def test_rule_fields_keep_raw_states() -> None:
    result = evaluate_settings({"settings": {"best_ball": 0, "bench_lock": 1}}, "src")
    fields = {f.path: f for f in result.rule_fields}
    assert fields["settings.bench_lock"].value == 1
    assert fields["settings.taxi_slots"].state == "omitted"


def test_age_parsing_never_assumes_zero() -> None:
    assert parse_age(None).state == "absent"
    assert parse_age("12").seconds == 12
    assert parse_age("-1").state == "invalid"
    assert parse_age("abc").state == "invalid"


def test_cache_control_parsing() -> None:
    parsed = parse_cache_control("public, s-maxage=600, stale-while-revalidate=300")
    assert parsed.s_maxage == 600 and parsed.max_age is None and not parsed.no_store


def source(source_id: str, age: str | None, cache: str | None, origin: str = "this_call"):
    start = datetime(2026, 10, 13, 16, 0, tzinfo=UTC)
    obs = SourceObservation(id=source_id, route=Route.LEAGUE, url="u", coordinate={})
    obs.origin = origin  # pyright: ignore[reportAttributeAccessIssue]
    obs.request_started_at = start
    obs.body_completed_at = start + timedelta(seconds=1)
    obs.headers = {"age": age, "cache-control": cache}
    if origin == "this_call":
        obs.attempts = [Attempt(0, start, start + timedelta(seconds=1), "response", 200)]
    return obs


def test_stale_delivery_is_a_notice_and_unknowns_stay_unknown() -> None:
    now = datetime(2026, 10, 13, 16, 0, 5, tzinfo=UTC)
    sources = [
        source("src-league", "407", "public, s-maxage=300"),
        source("src-rosters", None, "public, s-maxage=60"),
        source("src-state", "x", "public, s-maxage=60"),
        source("src-user", "5", None),
    ]
    result = evaluate_freshness(sources, now, None)
    stale = {s.source_ref: s.stale_delivery for s in result.section.per_source}
    assert stale == {"src-league": True, "src-rosters": None, "src-state": None, "src-user": None}
    assert [(i.reason, i.severity) for i in result.issues] == [("source_cache_stale", "notice")]
    assert result.section.origin_freshness == "unestablished"
    window = result.section.observation_window
    assert window is not None and window.duration_seconds == 1.0
    assert result.section.per_source[0].local_age_seconds == 5.0
