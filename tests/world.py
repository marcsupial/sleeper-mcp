"""A synthetic Sleeper world mounted on respx. Every user, league and team identifier is made up."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import respx

from sleeper_mcp.lineup_context.inputs import RawArgument
from sleeper_mcp.lineup_context.schema import LineupContextV1
from sleeper_mcp.lineup_context.service import Runtime, get_lineup_context
from sleeper_mcp.scope import NoRegistry, ScopeRegistry, load_registry
from sleeper_mcp.sleeper.directory import PlayerDirectoryStore
from sleeper_mcp.sleeper.fetch import Fetcher, ProcessLimiter

from .helpers import BASE_URL, REGISTRY_PATH, FakeClock

USER = "test-owner-1"
OTHER = "test-owner-2"
LEAGUE = "test-league-redraft"
REDRAFT = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"]
STARTERS = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"]

Responder = httpx.Response | Callable[[httpx.Request], Any] | Exception


def profile(pid: str, *positions: str) -> dict[str, Any]:
    return {
        "player_id": pid,
        "full_name": f"Synthetic Player {pid}",
        "position": positions[0] if positions else None,
        "fantasy_positions": list(positions),
        "team": "KC",
        "status": "Active",
        "injury_status": None,
    }


def default_players() -> dict[str, Any]:
    positions = {
        "P1": "QB",
        "P2": "RB",
        "P3": "RB",
        "P4": "WR",
        "P5": "WR",
        "P6": "TE",
        "P7": "RB",
        "P8": "WR",
        "B1": "WR",
        "B2": "RB",
        "R1": "TE",
        "T1": "QB",
    }
    return {pid: profile(pid, pos) for pid, pos in positions.items()}


def league_body(
    league_id: str = LEAGUE,
    configuration: list[str] | None = None,
    season: str = "2026",
    leg: int = 6,
) -> dict[str, Any]:
    slots = REDRAFT if configuration is None else configuration
    return {
        "league_id": league_id,
        "name": "Synthetic Test League",
        "sport": "nfl",
        "season": season,
        "season_type": "regular",
        "status": "in_season",
        "total_rosters": 2,
        "roster_positions": [*slots, "BN", "BN", "BN"],
        "settings": {"best_ball": 0, "leg": leg, "type": 0, "reserve_slots": 1, "taxi_slots": 1},
        "scoring_settings": {
            "rec": 0.5,
            "pass_td": 6.0,
            "pass_int": -3.0,
            "bonus_rec_te": 0.5,
            "pass_yd": 0.03999999910593033,
            "synthetic_unfamiliar_term": 2.125,
            "explicit_zero": 0,
        },
    }


def roster_row(
    roster_id: int = 7,
    owner: str | None = USER,
    league_id: str = LEAGUE,
    starters: list[str] | None = None,
) -> dict[str, Any]:
    starting = STARTERS if starters is None else starters
    return {
        "roster_id": roster_id,
        "league_id": league_id,
        "owner_id": owner,
        "co_owners": None,
        "players": [*[p for p in starting if p != "0"], "B1", "B2", "R1", "T1"],
        "starters": list(starting),
        "reserve": ["R1"],
        "taxi": ["T1"],
    }


def other_row(roster_id: int = 8, league_id: str = LEAGUE) -> dict[str, Any]:
    return {
        "roster_id": roster_id,
        "league_id": league_id,
        "owner_id": OTHER,
        "co_owners": None,
        "players": ["X1"],
        "starters": ["X1", "0", "0", "0", "0", "0", "0", "0"],
        "reserve": None,
        "taxi": None,
    }


def matchup_row(roster_id: int = 7, starters: list[str] | None = None) -> dict[str, Any]:
    starting = STARTERS if starters is None else starters
    return {
        "roster_id": roster_id,
        "matchup_id": 1,
        "points": 0.0,
        "starters": list(starting),
        "players": [*[p for p in starting if p != "0"], "B1", "B2"],
    }


def state_body(
    week: int = 6, display_week: int | None = None, season: str = "2026"
) -> dict[str, Any]:
    return {
        "season": season,
        "season_type": "regular",
        "week": week,
        "leg": week,
        "display_week": week if display_week is None else display_week,
    }


@dataclass
class World:
    league_id: str = LEAGUE
    user: Any = field(default_factory=lambda: {"user_id": USER, "username": "synthetic_user"})
    league: Any = field(default_factory=league_body)
    rosters: Any = field(default_factory=lambda: [roster_row(), other_row()])
    state: Any = field(default_factory=state_body)
    matchups: dict[int, Any] = field(
        default_factory=lambda: {
            6: [matchup_row(), {"roster_id": 8, "starters": []}],
            5: [matchup_row(starters=["P1", "B2", *STARTERS[2:]])],
        }
    )
    players: Any = field(default_factory=default_players)
    overrides: dict[str, Responder] = field(default_factory=lambda: {})
    headers: dict[str, str] = field(
        default_factory=lambda: {"Cache-Control": "public, s-maxage=60", "Age": "12"}
    )

    def _respond(self, name: str, body: Any) -> Responder:
        if name in self.overrides:
            return self.overrides[name]
        return httpx.Response(200, content=json.dumps(body).encode(), headers=self.headers)

    def mount(self, router: respx.MockRouter) -> dict[str, respx.Route]:
        mounted: dict[str, respx.Route] = {}

        def add(name: str, path: str, body: Any) -> None:
            responder = self._respond(name, body)
            route = router.get(f"{BASE_URL}/{path}")
            if isinstance(responder, httpx.Response):
                payload = responder

                def side(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(
                        payload.status_code, content=payload.content, headers=payload.headers
                    )

                route.mock(side_effect=side)
            else:
                route.mock(side_effect=responder)
            mounted[name] = route

        user_id = _user_id(self.user)
        add("user", f"user/{user_id}", self.user)
        add("league", f"league/{self.league_id}", self.league)
        add("rosters", f"league/{self.league_id}/rosters", self.rosters)
        add("state", "state/nfl", self.state)
        for week in range(1, 19):
            add(
                f"matchups:{week}",
                f"league/{self.league_id}/matchups/{week}",
                self.matchups.get(week, []),
            )
        add("players", "players/nfl", self.players)
        return mounted

    def copy(self) -> World:
        return copy.deepcopy(self)


def make_runtime(
    cache_dir: Path,
    clock: FakeClock | None = None,
    registry: ScopeRegistry | NoRegistry | None = None,
) -> Runtime:
    clock = clock or FakeClock()
    fetcher = Fetcher(Fetcher.build_client(), BASE_URL, ProcessLimiter(clock), clock)
    loaded = registry if registry is not None else load_registry(REGISTRY_PATH)
    return Runtime(
        registry=loaded,
        fetcher=fetcher,
        directory=PlayerDirectoryStore(cache_dir, fetcher, clock),
        clock=clock,
    )


async def run(
    world: World,
    cache_dir: Path,
    arguments: dict[str, RawArgument] | None = None,
    runtime: Runtime | None = None,
) -> tuple[LineupContextV1, dict[str, respx.Route]]:
    args: dict[str, RawArgument] = {"user_id": USER, "league_id": world.league_id}
    args.update(arguments or {})
    with respx.mock(assert_all_called=False) as router:
        routes = world.mount(router)
        result = await get_lineup_context(runtime or make_runtime(cache_dir), args)
    return result, routes


def reasons(result: LineupContextV1) -> list[str]:
    return [issue.reason for issue in result.issues]


def calls(routes: dict[str, respx.Route]) -> dict[str, int]:
    return {name: route.call_count for name, route in routes.items() if route.call_count}


def _user_id(user: object) -> str:
    if isinstance(user, dict) and "user_id" in user:
        return str(user["user_id"])  # pyright: ignore[reportUnknownArgumentType]
    return USER
