from __future__ import annotations

import io
from pathlib import Path

import pytest

import sleeper_mcp
from sleeper_mcp.check import run_check
from sleeper_mcp.config import Config
from sleeper_mcp.scope import ScopeRegistry, load_registry
from sleeper_mcp.sleeper.directory import BODY_FILE

from .helpers import BASE_URL, REGISTRY_PATH

EXAMPLE_REGISTRY = Path(__file__).parents[1] / "examples" / "scope-registry.example.json"


def check(registry: Path | None, cache_dir: Path) -> tuple[int, str]:
    out = io.StringIO()
    code = run_check(Config(registry, cache_dir, BASE_URL), out)
    return code, out.getvalue()


def test_example_registry_parses_with_one_placeholder_target() -> None:
    registry = load_registry(EXAMPLE_REGISTRY)
    assert isinstance(registry, ScopeRegistry)
    assert list(registry.targets) == ["REPLACE_WITH_YOUR_LEAGUE_ID"]


def test_check_passes_with_a_loaded_registry(tmp_path: Path) -> None:
    code, output = check(REGISTRY_PATH, tmp_path)
    assert code == 0
    assert str(REGISTRY_PATH) in output
    assert "first call downloads" in output
    assert BASE_URL in output


def test_check_passes_without_a_registry(tmp_path: Path) -> None:
    code, output = check(None, tmp_path)
    assert code == 0
    assert "(not set)" in output
    assert "checked structurally" in output


def test_check_fails_with_a_refused_registry(tmp_path: Path) -> None:
    bad = tmp_path / "registry.local.json"
    bad.write_text("[]")
    code, _ = check(bad, tmp_path)
    assert code == 1


def test_check_reports_a_cached_player_directory(tmp_path: Path) -> None:
    (tmp_path / BODY_FILE).write_text("{}")
    _, output = check(REGISTRY_PATH, tmp_path)
    assert "player directory cached" in output


def test_check_never_names_registry_targets(tmp_path: Path) -> None:
    _, output = check(REGISTRY_PATH, tmp_path)
    assert "test-league" not in output


def test_main_check_flag_exits_with_the_check_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLEEPER_MCP_SCOPE_REGISTRY", str(REGISTRY_PATH))
    monkeypatch.setenv("SLEEPER_MCP_CACHE_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exited:
        sleeper_mcp.main(["--check"])
    assert exited.value.code == 0
