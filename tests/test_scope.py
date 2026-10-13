from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from sleeper_mcp.config import Config, default_cache_dir
from sleeper_mcp.scope import NoRegistry, ScopeRegistry, Target, Unregistered, load_registry

from .helpers import REGISTRY_PATH


def registry_document() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text())


def write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "registry.local.json"
    path.write_text(json.dumps(document))
    return path


def test_synthetic_registry_loads_five_targets() -> None:
    registry = load_registry(REGISTRY_PATH)
    assert isinstance(registry, ScopeRegistry)
    assert len(registry.targets) == 5
    kdef = registry.targets["test-league-kdef-2023"]
    assert kdef.automatic_week == "explicit_week_only"
    assert kdef.starting_configuration[-2:] == ("K", "DEF")
    assert kdef.mapping_basis == "retained_selected_league_configuration_observed_view"
    assert registry.targets["test-league-dynasty"].mapping_basis == (
        "observed_selected_configuration"
    )


def test_lookup_distinguishes_target_unregistered_and_comparison() -> None:
    registry = load_registry(REGISTRY_PATH)
    assert isinstance(registry, ScopeRegistry)
    assert isinstance(registry.lookup("test-league-redraft"), Target)
    assert registry.lookup("test-league-unknown") == Unregistered("not_registered")
    assert registry.lookup("test-league-lookalike-2024") == Unregistered("unsupported_comparison")


def test_no_registry_configured() -> None:
    assert load_registry(None) == NoRegistry("not_configured")


def test_unreadable_or_malformed_registry_is_refused(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "missing.json") == NoRegistry("refused")
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert load_registry(bad) == NoRegistry("refused")
    assert load_registry(write(tmp_path, [])) == NoRegistry("refused")


Mutation = Callable[[dict[str, Any]], object]

MUTATIONS: list[Mutation] = [
    lambda d: d["existing_policy_bindings"].__setitem__("A03", "some_other_week_policy_v2"),
    lambda d: d["existing_policy_bindings"].pop("A09"),
    lambda d: d["existing_policy_bindings"].__setitem__("A10", "other_handoff_v2"),
    lambda d: d["existing_policy_bindings"].__setitem__("A99", "unknown_policy_v1"),
    lambda d: d["slot_rules"].__setitem__("FLEX", ["RB", "WR"]),
    lambda d: d["slot_rules"].__setitem__("IDP_FLEX", ["DL", "LB", "DB"]),
    lambda d: d["fetch_controls"].__setitem__("attempt_budget", 8),
    lambda d: d["fetch_controls"]["httpx_phase_seconds"].__setitem__("read", 4),
    lambda d: d["fetch_controls"].__setitem__("core_older_local_fallback", 0),
    lambda d: d["targets"]["test-league-redraft"].__setitem__("automatic_week", "latest"),
    lambda d: d["targets"]["test-league-redraft"].__setitem__("best_ball", False),
    lambda d: d["targets"]["test-league-redraft"]["starting_configuration"].append("BN"),
    lambda d: d["targets"]["test-league-redraft"]["starting_configuration"].append("IDP"),
    lambda d: d["unsupported_comparisons"].append("test-league-redraft"),
]


@pytest.mark.parametrize("mutate", MUTATIONS)
def test_registry_mismatching_the_implementation_is_refused(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], object]
) -> None:
    document = registry_document()
    mutate(document)
    assert load_registry(write(tmp_path, document)) == NoRegistry("refused")


def test_registry_may_name_the_implemented_a10_policy(tmp_path: Path) -> None:
    document = copy.deepcopy(registry_document())
    document["existing_policy_bindings"]["A10"] = "lineup_assessment_handoff_v1"
    assert isinstance(load_registry(write(tmp_path, document)), ScopeRegistry)


def test_config_from_env(tmp_path: Path) -> None:
    config = Config.from_env(
        {
            "SLEEPER_MCP_SCOPE_REGISTRY": str(REGISTRY_PATH),
            "SLEEPER_MCP_CACHE_DIR": str(tmp_path),
            "SLEEPER_MCP_BASE_URL": "https://sleeper.test/v1/",
        }
    )
    assert config.scope_registry_path == REGISTRY_PATH
    assert config.cache_dir == tmp_path
    assert config.base_url == "https://sleeper.test/v1"
    assert Config.from_env({"HOME": "/home/t"}).scope_registry_path is None


def test_default_cache_dir_per_platform() -> None:
    assert default_cache_dir({"HOME": "/Users/t"}, "darwin") == Path(
        "/Users/t/Library/Caches/sleeper-mcp"
    )
    assert default_cache_dir({"HOME": "/home/t", "XDG_CACHE_HOME": "/x"}, "linux") == Path(
        "/x/sleeper-mcp"
    )
    assert default_cache_dir({"HOME": "/home/t"}, "linux") == Path("/home/t/.cache/sleeper-mcp")
