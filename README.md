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

### Supported leagues

Any Sleeper league that meets all of these rules, checked against the live league on every call:

- NFL, regular season, not best ball.
- Every starting slot is one of `QB`, `RB`, `WR`, `TE`, `FLEX`, `SUPER_FLEX`, `K` or `DEF`.
  Leagues with IDP or other flex slots (for example `DL`, `REC_FLEX`) return `unsupported_scope`
  and name the slots they use.
- Completed past seasons need an explicit `week`. Their slots are mapped from the league's latest
  configuration, and the result labels that basis because it may differ from the original week's.

Each result reports how the league was admitted in `resolution_evidence.league.scope`, together
with every guard it checked.

## Setup

You need [uv](https://docs.astral.sh/uv/), a Sleeper league, and an MCP client such as Claude
Desktop or Claude Code. No Sleeper login or API key is needed; the server only reads public data.

### 1. Get the code

```bash
git clone https://github.com/marcsupial/sleeper-mcp.git
cd sleeper-mcp
uv sync
```

Clients start the server with `uv run --directory /absolute/path/to/sleeper-mcp sleeper-mcp`, which
uses this checkout as-is. To try it without cloning, use
`uvx --from git+https://github.com/marcsupial/sleeper-mcp sleeper-mcp` as the command instead.

### 2. Connect a client

Use absolute paths. Clients start the server with a minimal environment, so a bare `uv` may not be
found. Find uv's path with `which uv`.

**Claude Desktop.** Open Settings → Developer → Edit Config, add the server to
`claude_desktop_config.json`, then quit and reopen Claude Desktop:

```json
{
  "mcpServers": {
    "sleeper": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--directory", "/absolute/path/to/sleeper-mcp", "sleeper-mcp"]
    }
  }
}
```

**Claude Code.**

```bash
claude mcp add sleeper -- uv run --directory /absolute/path/to/sleeper-mcp sleeper-mcp
```

**MCP Inspector**, to call the tool by hand without an assistant:

```bash
npx @modelcontextprotocol/inspector -- uv run --directory /absolute/path/to/sleeper-mcp sleeper-mcp
```

### 3. Find your IDs

The tool currently takes IDs, not names.

- **User ID:** open `https://api.sleeper.app/v1/user/<your-username>` and copy `user_id`.
- **League ID:** the number in the league's web address, `https://sleeper.com/leagues/<league_id>`,
  or `league_id` in `https://api.sleeper.app/v1/user/<user_id>/leagues/nfl/<season>`.

Then ask your assistant something like *"Show my lineup for league `<league_id>`. My Sleeper user
ID is `<user_id>`."*

### Troubleshooting

- **Check the configuration.** `uv run sleeper-mcp --check` reports the effective settings without
  contacting Sleeper. It exits with status 1 when the server could not serve any league.
- **Logs.** The server logs to stderr. Claude Desktop saves it to
  `~/Library/Logs/Claude/mcp-server-sleeper.log` on macOS and `%APPDATA%\Claude\logs` on Windows.
- **`unsupported_scope`.** The issue's explanation names the rule the league failed. If every call
  fails with cause `registry_refused`, a configured scope registry was refused; the log says why.
- **Slow first call.** The first call downloads Sleeper's player directory (about 15 MB). Later calls
  reuse it for 24 hours.

## Configuration

Everything is optional.

| Variable | Meaning |
| --- | --- |
| `SLEEPER_MCP_SCOPE_REGISTRY` | Path to a JSON scope registry that pins or excludes specific leagues. |
| `SLEEPER_MCP_CACHE_DIR` | Where the daily player-directory snapshot is kept. Defaults to `~/Library/Caches/sleeper-mcp` on macOS, otherwise `$XDG_CACHE_HOME/sleeper-mcp` or `~/.cache/sleeper-mcp`. |

**Pinning leagues.** A scope registry lists exact league instances
([example](examples/scope-registry.example.json)). A pinned league must keep the season, format and
starting slots recorded in its entry, so a change in Sleeper fails the call instead of passing
silently. Leagues in `unsupported_comparisons` are refused. Any other league is still checked
structurally. Keep `slot_rules`, `existing_policy_bindings` and `fetch_controls` exactly as in the
example: the server refuses a registry whose values differ from the behavior it implements, and
then serves no league at all.

**Fetching.** Each call reads the user, league, rosters, the requested week (and NFL state for an
automatic week) directly from Sleeper's public API, within a 15-second call deadline, at most seven
requests and one retry. The roughly 15 MB player directory is downloaded at most once per 24 hours
and reused from the cache directory.

**Argument limitation.** The MCP SDK parses string arguments that look like JSON lists, objects or
`null` before the tool sees them, so `week: "[5]"` arrives as `[5]`. Such values are still rejected,
and `requested` shows the value as received.

## Development

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
