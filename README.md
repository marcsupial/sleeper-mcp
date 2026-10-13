# sleeper-mcp

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server for [Sleeper](https://sleeper.com) fantasy football. It lets an AI assistant look up your leagues, rosters and weekly lineups so it can help you review them.

> **Status:** experimental. One tool is available. Its output size is not yet bounded.

## Tools

### `get_lineup_context`

Read-only. For one league you choose, returns the league's rules, the user's current roster with
each player's placement (starter, bench, IR, taxi), and the starting lineup for one week. The result
is a single `get_lineup_context.v1` object, returned both as structured content and as JSON text.

| Input | Type | Notes |
| --- | --- | --- |
| `user_id` | string, required | Stable Sleeper user ID. Not a username. |
| `league_id` | string, required | Exact league ID. The server never picks a league for you. |
| `roster_id` | integer | Needed when the user is associated with several rosters. |
| `sport` | string | Defaults to `nfl`, the only supported sport. The default is reported. |
| `season` | string | Optional check; must match the league's own season. |
| `week` | integer | Omit for automatic resolution, which only succeeds when Sleeper's week signals agree. |

Outcomes:

- `complete` and `partial` include the selected context. `partial` lists what is missing in
  `issues`, for example `week_resolution_required` (pass an explicit `week`) or
  `player_details_unavailable`.
- `selection_required` lists candidate rosters; call again with one of their `roster_id` values.
- `error` (MCP `isError: true`) explains the failure. No context is fabricated.

Missing, null, empty, malformed and stale data stay visible: they are never replaced with defaults.
A complete result does not prove freshness, player health, lock status or historical eligibility.

## Configuration

| Variable | Meaning |
| --- | --- |
| `SLEEPER_MCP_SCOPE_REGISTRY` | Path to a JSON scope registry listing the supported leagues. |
| `SLEEPER_MCP_CACHE_DIR` | Where the daily player-directory snapshot is kept. Defaults to `~/Library/Caches/sleeper-mcp` on macOS, otherwise `$XDG_CACHE_HOME/sleeper-mcp` or `~/.cache/sleeper-mcp`. |

**Supported scope.** The tool only serves leagues listed in the scope registry, and it checks that
each league's sport, season, format and starting slots match its registry entry. Without a registry
every request returns `unsupported_scope`. The registry is private configuration; this repository
ships only a synthetic one for tests (`tests/fixtures/scope-registry.json`).

**Fetching.** Each call reads the user, league, rosters, the requested week (and NFL state for an
automatic week) directly from Sleeper's public API, within a 15-second call deadline, at most seven
requests and one retry. The roughly 15 MB player directory is downloaded at most once per 24 hours
and reused from the cache directory.

**Argument limitation.** The MCP SDK parses string arguments that look like JSON lists, objects or
`null` before the tool sees them, so `week: "[5]"` arrives as `[5]`. Such values are still rejected,
and `requested` shows the value as received.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # install dependencies
uv run pytest           # run tests
uv run ruff check .     # lint
uv run ruff format .    # format
uv run pyright          # type-check
```

## Not affiliated with Sleeper

This is an independent project and is not endorsed by or affiliated with Sleeper.

## License

[MIT](LICENSE)
