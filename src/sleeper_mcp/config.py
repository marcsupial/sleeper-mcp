"""Runtime configuration read from environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://api.sleeper.app/v1"

ENV_SCOPE_REGISTRY = "SLEEPER_MCP_SCOPE_REGISTRY"
ENV_CACHE_DIR = "SLEEPER_MCP_CACHE_DIR"
ENV_BASE_URL = "SLEEPER_MCP_BASE_URL"


def default_cache_dir(env: dict[str, str] | None = None, platform: str | None = None) -> Path:
    env = dict(os.environ) if env is None else env
    platform = sys.platform if platform is None else platform
    home = Path(env.get("HOME") or Path.home())
    if platform == "darwin":
        return home / "Library" / "Caches" / "sleeper-mcp"
    xdg = env.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "sleeper-mcp"
    return home / ".cache" / "sleeper-mcp"


@dataclass(frozen=True)
class Config:
    scope_registry_path: Path | None
    cache_dir: Path
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        env = dict(os.environ) if env is None else env
        registry = env.get(ENV_SCOPE_REGISTRY) or None
        cache = env.get(ENV_CACHE_DIR) or None
        base_url = env.get(ENV_BASE_URL) or DEFAULT_BASE_URL
        return cls(
            scope_registry_path=Path(registry).expanduser() if registry else None,
            cache_dir=Path(cache).expanduser() if cache else default_cache_dir(env),
            base_url=base_url.rstrip("/"),
        )
