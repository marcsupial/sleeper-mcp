# sleeper-mcp

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server for [Sleeper](https://sleeper.com) fantasy football. It lets an AI assistant look up your leagues, rosters and weekly lineups so it can help you review them.

> **Status:** early development. No tools are available yet.

## What it will do

- List a user's NFL leagues for a season.
- Show a league's starting slots, your current roster and the starters for a given week.

The server only reads Sleeper's public API. It never changes a lineup, and it needs no login. Sleeper user IDs select whose data to read; they are not credentials.

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
