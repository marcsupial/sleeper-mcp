"""The `get_lineup_context` MCP tool (D3).

Every argument is typed `JsonValue` so the SDK cannot coerce it; validation happens in domain code.
`user_id` and `league_id` stay schema-required, so a missing one is an SDK protocol-style error.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import JsonValue, WithJsonSchema

from sleeper_mcp.lineup_context.inputs import MISSING, RawArgument
from sleeper_mcp.lineup_context.schema import LineupContextV1
from sleeper_mcp.lineup_context.service import Runtime, get_lineup_context

TOOL_NAME = "get_lineup_context"

DESCRIPTION = """\
Read-only. Returns one Sleeper NFL league's rules, the user's current roster with placements, and \
the starting lineup for one pinned week, as a get_lineup_context.v1 object. Nothing is changed in \
Sleeper.

Inputs: user_id is the stable numeric Sleeper user ID (not a username). league_id is the exact \
league ID. Omit week to use the provider-observed current week when all week signals agree; \
otherwise the result is partial with reason week_resolution_required: call again with an explicit \
integer week. If the user has several rosters (outcome selection_required), call again with one \
of the candidate roster_id values. Supported leagues are NFL, regular season and not best ball, \
with starting slots from QB, RB, WR, TE, FLEX, SUPER_FLEX, K and DEF; others are unsupported_scope.

The resolved league, roster, season and week are pinned for the call and reported in `resolved`. \
Unknown, missing, malformed and stale data are reported explicitly in `issues` and section \
coverage; they are never filled with defaults. A complete outcome does not prove freshness, \
health, lock status or historical eligibility."""

ANNOTATIONS = ToolAnnotations(
    title="Get lineup context",
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

UserId = Annotated[
    JsonValue,
    WithJsonSchema({"type": "string", "description": "Stable Sleeper user ID (not a username)."}),
]
LeagueId = Annotated[
    JsonValue, WithJsonSchema({"type": "string", "description": "Exact Sleeper league ID."})
]
RosterId = Annotated[
    JsonValue,
    WithJsonSchema(
        {
            "type": "integer",
            "description": "League-local roster ID; needed when selection_required lists "
            "several candidates.",
        }
    ),
]
Sport = Annotated[
    JsonValue, WithJsonSchema({"type": "string", "description": 'Defaults to "nfl".'})
]
Season = Annotated[
    JsonValue,
    WithJsonSchema(
        {"type": "string", "description": 'Expected league season, e.g. "2026"; must match.'}
    ),
]
Week = Annotated[
    JsonValue,
    WithJsonSchema(
        {
            "type": "integer",
            "description": "Lineup week (JSON integer). Omit for automatic resolution.",
        }
    ),
]


def dual_channel(result: LineupContextV1) -> CallToolResult:
    """The same object in structuredContent and as JSON text; isError only for `error`."""
    structured: dict[str, Any] = result.model_dump(mode="json")
    text = json.dumps(structured, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=structured,
        is_error=result.outcome == "error",
    )


def register(server: MCPServer, runtime: Callable[[], Runtime]) -> None:
    async def get_lineup_context_tool(
        user_id: UserId,
        league_id: LeagueId,
        roster_id: RosterId = MISSING,  # pyright: ignore[reportArgumentType]
        sport: Sport = MISSING,  # pyright: ignore[reportArgumentType]
        season: Season = MISSING,  # pyright: ignore[reportArgumentType]
        week: Week = MISSING,  # pyright: ignore[reportArgumentType]
    ) -> Annotated[CallToolResult, LineupContextV1]:
        arguments: dict[str, RawArgument] = {
            "user_id": user_id,
            "league_id": league_id,
            "roster_id": roster_id,
            "sport": sport,
            "season": season,
            "week": week,
        }
        return dual_channel(await get_lineup_context(runtime(), arguments))

    with warnings.catch_warnings():
        # The private sentinel default is not JSON-serializable; pydantic drops it from the
        # published inputSchema, which is what we want.
        warnings.filterwarnings("ignore", message="Default value MISSING")
        server.tool(
            name=TOOL_NAME,
            title="Get lineup context",
            description=DESCRIPTION,
            annotations=ANNOTATIONS,
        )(get_lineup_context_tool)
