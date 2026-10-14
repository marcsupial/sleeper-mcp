# sleeper-mcp

Read-only MCP server for Sleeper fantasy football, written in Python and managed with uv.

## Commands

```bash
uv sync                    # install dependencies
uv run pytest              # tests
uv run ruff check .        # lint
uv run ruff format .       # format
uv run pyright             # type-check (strict)
uv run python scripts/build_mcpb.py --check-install   # build the Claude Desktop bundle
```

CI runs the checks on Python 3.12, 3.14 and 3.15 and builds the bundle. Keep code compatible with
3.12. Build bundles with the script, never `mcpb pack .` directly.

## Rules

- **Read-only.** Only call Sleeper's public read endpoints. Never add code that changes a lineup, roster, league or account.
- **No real user data.** Tests and fixtures use made-up user IDs, league IDs, usernames and team names. Never copy captured Sleeper responses into this repo. Real NFL player names and IDs are fine.
- **Keep unknowns visible.** Missing, null, empty and malformed values mean different things; don't coerce one into another, and don't silently pick a league, roster or week for the user.
- **MCP SDK is 2.x.** Use `MCPServer` from `mcp.server.mcpserver`. `FastMCP` and `mcp.server.fastmcp` are the old 1.x names and fail to import.
- **Tests never hit the network.** Mock HTTP with `respx`.
- **Never commit a real scope registry.** `SLEEPER_MCP_SCOPE_REGISTRY` points at a private file;
  tests use the synthetic `tests/fixtures/scope-registry.json`. `SLEEPER_MCP_CACHE_DIR` holds the
  player-directory snapshot.
- **Every result branch is validated by our code.** The SDK skips output validation for error
  results, so `finalize()` validates the v1 model and its internal references on every branch.
