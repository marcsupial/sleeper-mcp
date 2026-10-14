"""MCP server wiring: one shared runtime (registry, rate limiter, directory store) per process."""

from __future__ import annotations

import logging
from functools import cache
from importlib.metadata import version

from mcp.server.mcpserver import MCPServer

from sleeper_mcp.config import Config
from sleeper_mcp.lineup_context.service import Runtime
from sleeper_mcp.scope import load_registry
from sleeper_mcp.sleeper.directory import PlayerDirectoryStore
from sleeper_mcp.sleeper.fetch import Fetcher, ProcessLimiter, SystemClock
from sleeper_mcp.tools import lineup_context

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Read-only access to public Sleeper fantasy football data. Tools never change a lineup, "
    "roster, league or account."
)


def build_runtime(config: Config) -> Runtime:
    clock = SystemClock()
    fetcher = Fetcher(Fetcher.build_client(), config.base_url, ProcessLimiter(clock), clock)
    return Runtime(
        registry=load_registry(config.scope_registry_path),
        fetcher=fetcher,
        directory=PlayerDirectoryStore(config.cache_dir, fetcher, clock),
        clock=clock,
    )


def create_server(runtime: Runtime | None = None) -> MCPServer:
    server = MCPServer("sleeper-mcp", version=version("sleeper-mcp"), instructions=INSTRUCTIONS)

    @cache
    def lazy_runtime() -> Runtime:
        return runtime if runtime is not None else build_runtime(Config.from_env())

    lineup_context.register(server, lazy_runtime)
    return server
