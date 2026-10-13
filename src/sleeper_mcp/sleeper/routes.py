"""Sleeper public read routes used by this server. All are GETs without authentication."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import quote

from sleeper_mcp.json_values import Json


class Route(StrEnum):
    USER = "user"
    LEAGUE = "league"
    ROSTERS = "rosters"
    MATCHUPS = "matchups"
    STATE_NFL = "state_nfl"
    PLAYERS_NFL = "players_nfl"


# The player directory is enrichment; every other route is a core read eligible for a retry.
CORE_ROUTES = frozenset({Route.USER, Route.LEAGUE, Route.ROSTERS, Route.MATCHUPS, Route.STATE_NFL})


@dataclass(frozen=True)
class RouteRequest:
    route: Route
    path: str
    coordinate: dict[str, Json] = field(default_factory=lambda: {})

    def url(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/{self.path}"


def _segment(value: str) -> str:
    return quote(value, safe="")


def user(user_id: str) -> RouteRequest:
    return RouteRequest(Route.USER, f"user/{_segment(user_id)}", {"user_id": user_id})


def league(league_id: str) -> RouteRequest:
    return RouteRequest(Route.LEAGUE, f"league/{_segment(league_id)}", {"league_id": league_id})


def rosters(league_id: str) -> RouteRequest:
    return RouteRequest(
        Route.ROSTERS, f"league/{_segment(league_id)}/rosters", {"league_id": league_id}
    )


def matchups(league_id: str, week: int) -> RouteRequest:
    return RouteRequest(
        Route.MATCHUPS,
        f"league/{_segment(league_id)}/matchups/{week}",
        {"league_id": league_id, "week": week},
    )


def state_nfl() -> RouteRequest:
    return RouteRequest(Route.STATE_NFL, "state/nfl", {"sport": "nfl"})


def players_nfl() -> RouteRequest:
    return RouteRequest(Route.PLAYERS_NFL, "players/nfl", {"sport": "nfl"})
