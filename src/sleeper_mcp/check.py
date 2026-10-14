"""`sleeper-mcp --check`: report the effective configuration without contacting Sleeper."""

from __future__ import annotations

from typing import TextIO

from sleeper_mcp.config import ENV_CACHE_DIR, ENV_SCOPE_REGISTRY, Config
from sleeper_mcp.scope import NoRegistry, ScopeRegistry, load_registry
from sleeper_mcp.sleeper.directory import BODY_FILE


def describe_registry(registry: ScopeRegistry | NoRegistry) -> tuple[str, bool]:
    """One status line for the scope registry, and whether the server can serve any league."""
    # Counts only, like load_registry's log: this output gets pasted where league IDs shouldn't go.
    match registry:
        case ScopeRegistry(targets=targets):
            return (
                f"loaded with {len(targets)} pinned leagues; others are checked structurally",
                True,
            )
        case NoRegistry(reason="not_configured"):
            return "not set (optional); leagues are checked structurally", True
        case NoRegistry():
            return "refused (reason logged above); every request will be unsupported_scope", False


def run_check(config: Config, out: TextIO) -> int:
    """Print one line per setting; return the process exit code (0 when ready to serve)."""
    registry = load_registry(config.scope_registry_path)
    registry_line, ready = describe_registry(registry)
    snapshot = config.cache_dir / BODY_FILE
    snapshot_state = (
        "player directory cached"
        if snapshot.is_file()
        else "no player directory yet; the first call downloads about 15 MB"
    )
    print(f"{ENV_SCOPE_REGISTRY}: {config.scope_registry_path or '(not set)'}", file=out)
    print(f"  {registry_line}", file=out)
    print(f"{ENV_CACHE_DIR}: {config.cache_dir}", file=out)
    print(f"  {snapshot_state}", file=out)
    print(f"Sleeper API: {config.base_url}", file=out)
    return 0 if ready else 1
