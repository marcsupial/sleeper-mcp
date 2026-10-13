"""`manual_observed_context_fetch_v1` freshness disclosure (A08).

Ages and cache evidence are descriptive; origin freshness is never established.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sleeper_mcp.contract import FETCH_POLICY
from sleeper_mcp.lineup_context.issues import IssueSpec
from sleeper_mcp.lineup_context.schema import (
    CacheControl,
    FreshnessSection,
    FreshnessSource,
    HttpAge,
    ObservationWindow,
)
from sleeper_mcp.sleeper.observation import SourceObservation, utc_iso


def parse_cache_control(raw: str | None) -> CacheControl:
    directives: dict[str, str | None] = {}
    for part in (raw or "").split(","):
        name, _, value = part.strip().partition("=")
        if name:
            directives[name.strip().lower()] = value.strip().strip('"') if value else None

    def seconds(name: str) -> int | None:
        value = directives.get(name)
        return int(value) if value is not None and value.isdigit() else None

    return CacheControl(
        raw=raw,
        max_age=seconds("max-age"),
        s_maxage=seconds("s-maxage"),
        no_store="no-store" in directives,
        no_cache="no-cache" in directives,
        must_revalidate="must-revalidate" in directives,
    )


def parse_age(raw: str | None) -> HttpAge:
    if raw is None:
        return HttpAge(state="absent", seconds=None, raw=None)
    text = raw.strip()
    if text.isdigit():
        return HttpAge(state="present", seconds=int(text), raw=raw)
    return HttpAge(state="invalid", seconds=None, raw=raw)


@dataclass
class FreshnessResult:
    section: FreshnessSection
    issues: list[IssueSpec] = field(default_factory=lambda: [])


def evaluate_freshness(
    sources: list[SourceObservation], now: datetime, directory_reuse_age: float | None
) -> FreshnessResult:
    per_source: list[FreshnessSource] = []
    stale_refs: list[str] = []
    for obs in sources:
        if obs.headers is None or obs.acquired_at is None:
            continue
        cache = parse_cache_control(obs.headers.get("cache-control"))
        age = parse_age(obs.headers.get("age"))
        lifetime = cache.s_maxage if cache.s_maxage is not None else cache.max_age
        if age.state != "present":
            stale: bool | None = None
            basis = f"Age header {age.state}; stale delivery unknown (never assumed zero)."
        elif lifetime is None:
            stale = None
            basis = "No s-maxage or max-age; stale delivery unknown."
        else:
            assert age.seconds is not None
            stale = age.seconds > lifetime
            basis = f"Age {age.seconds} s against a {lifetime} s shared-cache lifetime."
        if stale:
            stale_refs.append(obs.id)
        per_source.append(
            FreshnessSource(
                source_ref=obs.id,
                route=obs.route.value,
                origin=obs.origin,
                acquired_at=utc_iso(obs.acquired_at),
                body_completed_at=utc_iso(obs.body_completed_at) if obs.body_completed_at else None,
                local_age_seconds=max(0.0, (now - obs.acquired_at).total_seconds()),
                http_age=age,
                cache_control=cache,
                stale_delivery=stale,
                stale_basis=basis,
            )
        )
    this_call = [o for o in sources if o.origin == "this_call" and o.attempts]
    window = None
    if this_call:
        start = min(a.started_at for o in this_call for a in o.attempts)
        end = max(a.ended_at for o in this_call for a in o.attempts)
        window = ObservationWindow(
            start=utc_iso(start),
            end=utc_iso(end),
            duration_seconds=(end - start).total_seconds(),
        )
    issues: list[IssueSpec] = []
    if stale_refs:
        issues.append(
            IssueSpec(
                reason="source_cache_stale",
                severity="notice",
                explanation="Some responses were delivered from a cache past their stated "
                "lifetime. The data is shown as observed; origin freshness is unestablished.",
                affected=[("source", ref) for ref in stale_refs],
                next_action=None,
                retryable=None,
            )
        )
    return FreshnessResult(
        FreshnessSection(
            policy_id=FETCH_POLICY,
            per_source=per_source,
            observation_window=window,
            directory_reuse_age_seconds=directory_reuse_age,
            origin_freshness="unestablished",
        ),
        issues,
    )
