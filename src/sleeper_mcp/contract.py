"""Policy versions and fetch controls this code implements.

A scope registry must name exactly these values (D2); otherwise it is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

ASSOCIATION_POLICY: Final = "rest_owner_co_owner_null_v1"
WEEK_POLICY: Final = "nfl_agreeing_week_signals_v1"
PLACEMENT_POLICY: Final = "rest_roster_placements_v1"
SLOT_POLICY: Final = "rest_configured_starting_slots_v1"
VACANCY_POLICY: Final = "rest_string_zero_vacancy_v1"
WEEKLY_POLICY: Final = "rest_weekly_observed_assignments_v1"
VIEWS_POLICY: Final = "rest_separate_current_weekly_views_v1"
FETCH_POLICY: Final = "manual_observed_context_fetch_v1"
ELIGIBILITY_POLICY: Final = "rest_observed_fantasy_eligibility_v1"
HANDOFF_POLICY: Final = "lineup_assessment_handoff_v1"

POLICY_BINDINGS: Final[dict[str, str | list[str]]] = {
    "A02": ASSOCIATION_POLICY,
    "A03": WEEK_POLICY,
    "A04": PLACEMENT_POLICY,
    "A05": [SLOT_POLICY, VACANCY_POLICY],
    "A06": WEEKLY_POLICY,
    "A07": VIEWS_POLICY,
    "A08": FETCH_POLICY,
    "A09": ELIGIBILITY_POLICY,
}
# A10 is implemented in code; a registry may name it, but need not.
OPTIONAL_POLICY_BINDINGS: Final[dict[str, str]] = {"A10": HANDOFF_POLICY}

POLICY_DESCRIPTIONS: Final[dict[str, tuple[str, str]]] = {
    ASSOCIATION_POLICY: (
        "A02",
        "Exact owner_id or co_owners membership; present-null co_owners means none within scope.",
    ),
    WEEK_POLICY: (
        "A03",
        "Explicit week, or the provider-observed week when four same-season signals agree.",
    ),
    PLACEMENT_POLICY: (
        "A04",
        "Current placements from players/starters/reserve/taxi; bench only from complete evidence.",
    ),
    SLOT_POLICY: ("A05", "Map valid full-length starters to the configured non-BN order."),
    VACANCY_POLICY: ("A05", 'Exact string "0" in a mapped slot is an explicit vacancy.'),
    WEEKLY_POLICY: (
        "A06",
        "Observed assignments for the pinned week; no other-week or current substitution.",
    ),
    VIEWS_POLICY: ("A07", "Current roster and weekly lineup are separate views."),
    FETCH_POLICY: ("A08", "This-call core reads, bounded budgets, daily directory snapshot."),
    ELIGIBILITY_POLICY: ("A09", "Observed fantasy_positions; no primary-position fallback."),
    HANDOFF_POLICY: ("A10", "Minimum assessment handoff; scoring kept separate from structure."),
}

SLOT_RULES: Final[dict[str, list[str]]] = {
    "QB": ["QB"],
    "RB": ["RB"],
    "WR": ["WR"],
    "TE": ["TE"],
    "FLEX": ["RB", "WR", "TE"],
    "SUPER_FLEX": ["QB", "RB", "WR", "TE"],
    "K": ["K"],
    "DEF": ["DEF"],
}

RECOGNIZED_POSITIONS: Final = frozenset({"QB", "RB", "WR", "TE", "DB", "DL", "LB", "K", "DEF"})


@dataclass(frozen=True)
class FetchControls:
    """A08 fetch controls. Production always uses `FETCH_CONTROLS`; tests may scale them."""

    call_deadline_seconds: float = 15
    attempt_deadline_seconds: float = 5
    connect_seconds: float = 2
    read_seconds: float = 3
    write_seconds: float = 2
    pool_seconds: float = 1
    route_budget: int = 6
    attempt_budget: int = 7
    max_in_flight: int = 2
    process_attempts_per_minute: int = 60
    eligible_core_retry_budget: int = 1
    directory_snapshot_age_seconds_exclusive: float = 86_400
    directory_refresh_attempts_per_24h: int = 1
    core_older_local_fallback: bool = False
    http_429_missing_retry_after_cooldown_seconds: float = 60
    rate_window_seconds: float = 60

    def registry_form(self) -> dict[str, object]:
        """The `fetch_controls` object a registry must contain."""
        return {
            "call_deadline_seconds": self.call_deadline_seconds,
            "attempt_deadline_seconds": self.attempt_deadline_seconds,
            "httpx_phase_seconds": {
                "connect": self.connect_seconds,
                "read": self.read_seconds,
                "write": self.write_seconds,
                "pool": self.pool_seconds,
            },
            "route_budget": self.route_budget,
            "attempt_budget": self.attempt_budget,
            "max_in_flight": self.max_in_flight,
            "process_attempts_per_minute": self.process_attempts_per_minute,
            "eligible_core_retry_budget": self.eligible_core_retry_budget,
            "directory_snapshot_age_seconds_exclusive": (
                self.directory_snapshot_age_seconds_exclusive
            ),
            "directory_refresh_attempts_per_24h": self.directory_refresh_attempts_per_24h,
            "core_older_local_fallback": self.core_older_local_fallback,
            "http_429_missing_retry_after_cooldown_seconds": (
                self.http_429_missing_retry_after_cooldown_seconds
            ),
        }


FETCH_CONTROLS: Final = FetchControls()
