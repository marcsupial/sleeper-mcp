from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from pydantic import TypeAdapter, ValidationError

from sleeper_mcp.lineup_context.references import reference_errors
from sleeper_mcp.lineup_context.schema import LineupContextV1
from sleeper_mcp.lineup_context.service import FinalizeError, finalize
from sleeper_mcp.tools.lineup_context import dual_channel

from ..helpers import schema_errors
from ..world import World, roster_row, run

pytestmark = pytest.mark.anyio


def published_schema() -> dict[str, Any]:
    from mcp.server.mcpserver.utilities.func_metadata import StrictJsonSchema

    return TypeAdapter(LineupContextV1).json_schema(schema_generator=StrictJsonSchema)


def test_published_schema_is_valid_2020_12() -> None:
    schema = published_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    jsonschema.Draft202012Validator.check_schema(schema)


async def outcomes(tmp_path: Path) -> dict[str, LineupContextV1]:
    complete, _ = await run(World(), tmp_path / "a")
    partial_world = World()
    del partial_world.league["scoring_settings"]
    partial, _ = await run(partial_world, tmp_path / "b")
    selection_world = World()
    selection_world.rosters = [
        {**roster_row(7), "co_owners": ["test-owner-1"]},
        {**roster_row(8, owner="x"), "co_owners": ["test-owner-1"]},
    ]
    selection, _ = await run(selection_world, tmp_path / "c")
    error_world = World()
    error_world.overrides["rosters"] = httpx.Response(404)
    error, _ = await run(error_world, tmp_path / "d")
    return {
        "complete": complete,
        "partial": partial,
        "selection_required": selection,
        "error": error,
    }


async def test_every_branch_validates_against_the_published_schema(tmp_path: Path) -> None:
    for outcome, result in (await outcomes(tmp_path)).items():
        assert result.outcome == outcome
        call = dual_channel(result)
        assert call.structured_content is not None
        assert schema_errors(published_schema(), call.structured_content) == []
        assert call.is_error == (outcome == "error")
        assert reference_errors(result) == []


async def test_schema_rejects_context_on_selection_and_error(tmp_path: Path) -> None:
    results = await outcomes(tmp_path)
    broken = results["error"].model_dump(mode="json")
    broken["context"] = results["complete"].model_dump(mode="json")["context"]
    assert schema_errors(published_schema(), broken)
    with pytest.raises(ValidationError):
        LineupContextV1.model_validate(broken)


async def test_unexpected_normalized_keys_are_rejected_but_raw_keys_survive(
    tmp_path: Path,
) -> None:
    result = (await outcomes(tmp_path))["complete"]
    data = result.model_dump(mode="json")
    extra = copy.deepcopy(data)
    extra["context"]["roster"]["data"]["surprise"] = 1
    with pytest.raises(ValidationError):
        LineupContextV1.model_validate(extra)
    assert schema_errors(published_schema(), extra)
    version = copy.deepcopy(data)
    version["schema_version"] = "get_lineup_context.v2"
    with pytest.raises(ValidationError):
        LineupContextV1.model_validate(version)
    assert (
        data["context"]["league"]["data"]["scoring_settings"]["synthetic_unfamiliar_term"] == 2.125
    )
    custom = copy.deepcopy(data)
    custom["context"]["league"]["data"]["settings"]["brand_new_flag"] = {"nested": [1]}
    LineupContextV1.model_validate(custom)


async def test_requested_only_allows_tool_inputs(tmp_path: Path) -> None:
    data = (await outcomes(tmp_path))["complete"].model_dump(mode="json")
    data["requested"]["username"] = "x"
    with pytest.raises(ValidationError):
        LineupContextV1.model_validate(data)


async def test_reference_checker_catches_dangling_references(tmp_path: Path) -> None:
    result = (await outcomes(tmp_path))["partial"]
    data = result.model_dump(mode="json")
    data["context"]["roster"]["source_refs"] = ["src-nowhere"]
    data["issues"][0]["affected"].append({"kind": "player", "ref": "NOBODY"})
    data["context"]["player_details"]["data"]["P1"]["profile_fields_ref"] = "missing_catalog"
    data["context"]["weekly_lineup"]["data"]["slots"][0]["policy_ids"] = ["unknown_policy_v9"]
    broken = LineupContextV1.model_validate(data)
    errors = reference_errors(broken)
    assert any("src-nowhere" in e for e in errors)
    assert any("NOBODY" in e for e in errors)
    assert any("missing_catalog" in e for e in errors)
    assert any("unknown_policy_v9" in e for e in errors)
    with pytest.raises(FinalizeError):
        finalize(broken)


async def test_branch_relationships_are_enforced(tmp_path: Path) -> None:
    data = (await outcomes(tmp_path))["complete"].model_dump(mode="json")
    wrong = copy.deepcopy(data)
    wrong["resolved"]["roster_id"] = 8
    assert "resolved.roster_id disagrees with the association selection" in reference_errors(
        LineupContextV1.model_validate(wrong)
    )
    partial = copy.deepcopy(data)
    partial["outcome"] = "partial"
    with pytest.raises(ValidationError):
        LineupContextV1.model_validate(partial)


async def test_nan_cannot_be_emitted(tmp_path: Path) -> None:
    result = (await outcomes(tmp_path))["complete"]
    assert result.context is not None
    result.context.league.data.scoring_settings = {"rec": float("nan")}
    with pytest.raises(ValueError):
        dual_channel(result)
