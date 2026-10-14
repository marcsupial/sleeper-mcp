import json
from importlib.metadata import version
from pathlib import Path

import pytest
from mcp.client import Client

import sleeper_mcp
from sleeper_mcp.server import create_server

from .world import make_runtime

MANIFEST = Path(__file__).resolve().parents[1] / "manifest.json"


def test_package_is_installed() -> None:
    assert version("sleeper-mcp")


def test_entry_point_is_callable() -> None:
    assert callable(sleeper_mcp.main)


def test_mcpb_manifest_version_matches_package() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["version"] == version("sleeper-mcp")


@pytest.mark.anyio
async def test_mcpb_manifest_lists_the_published_tools(tmp_path: Path) -> None:
    manifest = json.loads(MANIFEST.read_text())
    async with Client(create_server(make_runtime(tmp_path))) as client:
        listed = await client.list_tools()
    assert [tool["name"] for tool in manifest["tools"]] == [tool.name for tool in listed.tools]
