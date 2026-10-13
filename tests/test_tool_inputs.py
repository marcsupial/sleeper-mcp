"""D3: raw argument handling through the real MCP SDK path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from mcp.client import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent

from sleeper_mcp.server import create_server

from .world import LEAGUE, USER, World, make_runtime

pytestmark = pytest.mark.anyio


async def call(tmp_path: Path, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool, int]:
    server = create_server(make_runtime(tmp_path))
    with respx.mock(assert_all_called=False) as router:
        routes = World().mount(router)
        result = await server.call_tool("get_lineup_context", arguments)
    assert isinstance(result, CallToolResult)
    structured = result.structured_content
    assert structured is not None
    block = result.content[0]
    assert isinstance(block, TextContent)
    assert json.loads(block.text) == structured
    total = sum(route.call_count for route in routes.values())
    return structured, bool(result.is_error), total


@pytest.mark.parametrize("week", [True, False, "5", 5.0, 0, 19, -1, None])
async def test_week_is_never_coerced(tmp_path: Path, week: Any) -> None:
    obj, is_error, requests = await call(
        tmp_path, {"user_id": USER, "league_id": LEAGUE, "week": week}
    )
    assert is_error
    assert obj["outcome"] == "error"
    assert [i["reason"] for i in obj["issues"]] == ["invalid_input"]
    assert obj["requested"]["week"] == week
    assert type(obj["requested"]["week"]) is type(week)
    assert requests == 0


async def test_omitted_week_differs_from_null(tmp_path: Path) -> None:
    obj, is_error, _ = await call(tmp_path, {"user_id": USER, "league_id": LEAGUE})
    assert not is_error
    assert "week" not in obj["requested"]
    assert obj["resolution_evidence"]["week"]["basis"] == "provider_observed_week"


@pytest.mark.parametrize(
    ("raw", "parsed"),
    # "true" is left as a string by this SDK version; lists, objects and null are parsed.
    [("[5]", [5]), ("null", None), ("true", "true"), ("{}", {})],
)
async def test_sdk_pre_parses_json_looking_strings(tmp_path: Path, raw: str, parsed: Any) -> None:
    # Pinned SDK behaviour: such strings reach the tool already parsed. The echo shows that.
    obj, is_error, _ = await call(tmp_path, {"user_id": USER, "league_id": LEAGUE, "week": raw})
    assert is_error
    assert obj["requested"]["week"] == parsed


async def test_numeric_id_strings_are_not_pre_parsed(tmp_path: Path) -> None:
    obj, _, _ = await call(tmp_path, {"user_id": "12345", "league_id": LEAGUE, "week": 0})
    assert obj["requested"]["user_id"] == "12345"


@pytest.mark.parametrize(
    ("arguments", "field", "reason"),
    [
        ({"user_id": 735}, "user_id", "invalid_input"),
        ({"user_id": ""}, "user_id", "invalid_input"),
        ({"user_id": " test-owner-1"}, "user_id", "invalid_input"),
        ({"league_id": None}, "league_id", "invalid_input"),
        ({"roster_id": "7"}, "roster_id", "invalid_input"),
        ({"roster_id": True}, "roster_id", "invalid_input"),
        ({"roster_id": 7.0}, "roster_id", "invalid_input"),
        ({"sport": "NFL"}, "sport", "unsupported_scope"),
        ({"sport": "nba"}, "sport", "unsupported_scope"),
        ({"sport": 1}, "sport", "invalid_input"),
        ({"sport": "  "}, "sport", "invalid_input"),
        ({"season": 2026}, "season", "invalid_input"),
    ],
)
async def test_input_validation(
    tmp_path: Path, arguments: dict[str, Any], field: str, reason: str
) -> None:
    obj, is_error, requests = await call(
        tmp_path, {"user_id": USER, "league_id": LEAGUE, **arguments}
    )
    assert is_error
    assert [i["reason"] for i in obj["issues"]] == [reason]
    assert obj["issues"][0]["affected"] == [{"kind": "input", "ref": field}]
    assert obj["requested"][field] == arguments[field]
    assert requests == 0


async def test_requested_echoes_only_supplied_fields_and_defaults_are_visible(
    tmp_path: Path,
) -> None:
    obj, _, _ = await call(tmp_path, {"user_id": USER, "league_id": LEAGUE, "week": 6})
    assert obj["requested"] == {"user_id": USER, "league_id": LEAGUE, "week": 6}
    assert obj["resolution_evidence"]["league"]["defaults"] == [
        {"field": "sport", "value": "nfl", "basis": "contract_default"}
    ]


async def test_unknown_extra_arguments_are_ignored(tmp_path: Path) -> None:
    obj, _, _ = await call(tmp_path, {"user_id": USER, "league_id": LEAGUE, "username": "x"})
    assert "username" not in obj["requested"]


async def test_missing_required_argument_is_an_sdk_error(tmp_path: Path) -> None:
    server = create_server(make_runtime(tmp_path))
    with pytest.raises(ToolError):
        await server.call_tool("get_lineup_context", {"league_id": LEAGUE})
    async with Client(server) as client:
        result = await client.call_tool("get_lineup_context", {"league_id": LEAGUE})
    assert result.is_error
    assert result.structured_content is None


async def test_published_tool_metadata(tmp_path: Path) -> None:
    async with Client(create_server(make_runtime(tmp_path))) as client:
        listed = await client.list_tools()
    tool = next(t for t in listed.tools if t.name == "get_lineup_context")
    schema = tool.input_schema
    assert schema["required"] == ["user_id", "league_id"]
    types = {name: prop["type"] for name, prop in schema["properties"].items()}
    assert types == {
        "user_id": "string",
        "league_id": "string",
        "roster_id": "integer",
        "sport": "string",
        "season": "string",
        "week": "integer",
    }
    assert "MISSING" not in json.dumps(schema)
    assert all("default" not in prop for prop in schema["properties"].values())
    annotations = tool.annotations
    assert annotations is not None
    assert annotations.read_only_hint is True
    assert annotations.destructive_hint is False
    assert annotations.idempotent_hint is True
    assert annotations.open_world_hint is True
    assert tool.output_schema is not None
    assert tool.output_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "username" in (tool.description or "")
