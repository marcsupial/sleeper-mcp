# Spike: one-click Claude Desktop install with an MCPB `uv` bundle

**Verdict: go.** The `uv` server type works in Claude Desktop today. Installing the bundle
replaces the hand-edited `claude_desktop_config.json` (absolute uv path, `--directory`, env vars)
with a double-click and an optional settings field. Two follow-ups make it release-ready:
keep the `dev` dependency group out of the bundle, and test the newest CPython in CI.
PyPI plus `uvx` is still worth adding for other MCP clients, but not as Desktop's install path.

Tested on 2026-10-13 with Claude Desktop 2.37337.1 (macOS, Apple silicon), `@anthropic-ai/mcpb`
2.1.2 (latest) and manifest version 0.4.

## What was verified

| Check | Result |
| --- | --- |
| `mcpb validate` / `mcpb pack` | Pass, no warnings. 108.5 kB packed, 337 kB unpacked, 36 files. |
| Bundle contents | Only `manifest.json`, `pyproject.toml`, `uv.lock`, `LICENSE`, `src/`. No tests, fixtures, caches, `.venv` or local config. |
| Real install in Desktop | Installed and enabled; `uv sync` took 3 s (warm uv cache). |
| Server start in Desktop | `Connected to Sleeper (read-only) (1 tools)`; `initialize` and `tools/list` answered. |
| Tool list of the installed copy | `['get_lineup_context']` (stdio probe against the installed directory). |
| Optional registry left empty | Server received `SLEEPER_MCP_SCOPE_REGISTRY=""`, so it reports `not_configured`. |
| Desktop's fallback uv 0.9.7 (simulated) | `uv sync` and launch work; it reads our revision-5 `uv.lock` without rewriting it. |
| Test suite on Python 3.15 | 336 passed (CI runs only 3.12 and 3.14). |

Not verified: choosing a registry file in the settings UI (same substitution code as the empty
default, so expected to work), Windows, and Desktop's own uv download on a machine without uv.
The tool was not called against live Sleeper data.

## How Desktop runs a `uv` bundle

This is Desktop's implementation as observed in its logs and process tree, not the spec. It may change.

1. **Install.** Desktop looks for `uv` on its own search path (`/opt/homebrew/bin`,
   `/usr/local/bin`, `~/.local/bin`, …). If it can't find one, it downloads a pinned uv
   (0.9.7) into its app-data directory. It then runs `uv sync --quiet` in the unpacked extension
   directory, `~/Library/Application Support/Claude/Claude Extensions/local.mcpb.<author>.<name>/`.
2. **Launch.** The command is that uv binary; the manifest's `mcp_config.command` is ignored.
   The arguments are `mcp_config.args` after variable substitution, the working directory is the
   extension directory, and the environment comes from `mcp_config.env`. This is why the
   absolute-uv-path step disappears.
3. **Interpreter.** uv chooses one from `requires-python` (`>=3.12`): the newest suitable
   Python it finds, or a download. On the test Mac it chose Homebrew's 3.15.0.
   `compatibility.runtimes.python` is checked only for `type: "python"`, not `uv`.
   `compatibility.platforms` is enforced.
4. **Reinstall.** Installing a newer `.mcpb` over an old one keeps the user's settings.
   Locally installed bundles never update themselves.

## Spec gaps found

- **`mcp_config` is required.** MANIFEST.md says it is optional for `uv`, but the 0.4 schema
  (CLI and Desktop) requires it, and Desktop launches with its `args`. Without them, uv just
  prints its help and exits.
- **`entry_point` must be an existing `.py` file**, but for `uv` Desktop never runs it directly.
  The manifest points it at `src/sleeper_mcp/__main__.py`, and the args run that same file via
  `python -m sleeper_mcp`, so the two stay consistent.
- **An optional `user_config` field with no `default` is never substituted.** The env var
  receives the literal text `${user_config.scope_registry}`. The server would read that as a
  path, fail to open it, and report the registry as `refused` instead of `not_configured`.
  Every optional field therefore declares `"default": ""`.
- **`mcpb pack` ignores `.gitignore`.** It walks the directory and would bundle `.venv/`,
  `tests/`, caches and any local `*.local.json`. `.mcpbignore` is therefore an allowlist
  (`/*` followed by `!` entries).
- MANIFEST.md's header still says "Current version: 0.3", and `mcpb init` still defaults to 0.3.

## Files added

- `manifest.json`: manifest 0.4, `server.type: "uv"`, one optional `file` field for
  `SLEEPER_MCP_SCOPE_REGISTRY`. `platforms` is `darwin` and `linux` until Windows is tested.
  `privacy_policies` links Sleeper's privacy notice, because the server sends user and league
  IDs to Sleeper's API.
- Launch args: `uv run --directory ${__dirname} --frozen python -m sleeper_mcp`.
  - `--directory` makes the args work for any host, not just Desktop, which already sets the working directory.
  - `--frozen` stops a launch from ever re-resolving or rewriting the bundled lock, so startup
    works offline.
- `.mcpbignore`: an allowlist. `.python-version` is deliberately left out; see friction item 2.
- `src/sleeper_mcp/__main__.py`: a three-line shim that calls `sleeper_mcp.main()`. It also enables
  `python -m sleeper_mcp`.
- `tests/test_package.py`: fails if the manifest's version or tool list drifts from the package.
- `.gitignore`: `*.mcpb`.

Build:

```bash
npx @anthropic-ai/mcpb@2.1.2 pack . dist/sleeper-mcp-0.1.0.mcpb
```

## Friction that remains

1. **Desktop installs the `dev` dependency group.** Its plain `uv sync` includes `dev` by default
   (pyright, ruff, pytest, respx, jsonschema). Cold-cache measurements on the test connection:

   | `uv sync` | Time | Venv |
   | --- | --- | --- |
   | As Desktop runs it (with `dev`) | 252 s | 94 MB |
   | Without `dev` | 79 s | 30 MB |

   Options:
   - **Recommended: build the bundle from a staging directory.** A release script copies the
     allowlisted files, drops the `dev` group with `uv remove --group dev …`, then packs.
     Contributors' `uv sync` is unchanged. Checked on a copy of the bundle: this removes the 11
     dev-only packages from `uv.lock` without changing any other version, `uv sync --locked`
     passes, and the venv is 30 MB.
   - `[tool.uv] default-groups = []` plus `uv lock` (a one-line lock diff). The bundle drops to
     30 MB, but every contributor and CI step must use `uv sync --all-groups`. After a plain
     `uv sync`, `uv run pytest` silently falls back to any global `pytest` on PATH instead of
     failing. That footgun was observed during this spike.
   - Accept it. Desktop's install dialog already warns that setup "may take a few minutes".
2. **The interpreter is whatever uv picks.** Without `.python-version`, users get the newest
   Python ≥ 3.12 on their machine (3.15.0 here), which CI does not test. The suite passes on 3.15.
   Pinning `.python-version` in the bundle would control this. But Desktop has a feature flag
   that adds `--no-python-downloads`, and with it on, the install would fail on machines without
   that exact minor version. Prefer adding the newest CPython to the CI matrix.
3. **Unsigned.** Desktop logs "Installing unsigned extension". `mcpb sign` exists, but a
   self-signed certificate doesn't make the bundle trusted. That needs a trusted certificate or
   a listing in the extension directory.
4. **Scope registry.** Without a registry, every call returns `unsupported_scope`, and building
   a registry is not a one-click task. The structural league scope work being done separately
   removes this. Until it lands, the bundle is useful only to people who already have a registry.
5. **Cosmetic issues.** There is no icon, so Desktop shows a default; add a 512×512
   `icon.png`. `serverInfo.version` is empty because `MCPServer` is created without a version.
6. **Stale manual config.** Anyone who hand-configured the server will run two copies that both
   expose `get_lineup_context`. The README should tell them to delete the old entry.

## Where `SLEEPER_MCP_USERNAME` will go

Not implemented yet. When the server reads it, add a field next to `scope_registry`:

```json
"username": {
  "type": "string",
  "title": "Sleeper username",
  "description": "Your Sleeper username, used to find your leagues.",
  "required": false,
  "default": ""
}
```

and `"SLEEPER_MCP_USERNAME": "${user_config.username}"` under `mcp_config.env`. Keep
`"default": ""` and have `Config.from_env` treat an empty string as unset, as it already does for
the registry. Don't mark it `required`: Desktop then refuses to start the server until the field is
filled ("has missing required configuration"), and the user sees no tools and no explanation.
It is not `sensitive`.

## PyPI plus `uvx`

This is not the better route for Desktop. Desktop users would still edit JSON and need uv
installed, which is the friction this spike set out to remove. For other clients (Claude Code,
Cursor, etc.) it is the standard path: `{"command": "uvx", "args": ["sleeper-mcp"]}`. It
complements the bundle without replacing it. Both can come from the same tagged release.

## Proposed README install section

````markdown
## Install

### Claude Desktop

1. Download `sleeper-mcp-<version>.mcpb` from the
   [latest release](https://github.com/marcsupial/sleeper-mcp/releases/latest).
2. Double-click it, or drag it onto **Settings → Extensions**, and click **Install**. The first
   install downloads Python dependencies and can take a few minutes.
3. Optional: in the extension's settings, choose your **Scope registry** file.

You don't need to install Python or uv, or edit any configuration file. If uv isn't installed,
Claude Desktop downloads its own copy. If you added sleeper-mcp to `claude_desktop_config.json`
by hand before, remove that entry.

### Other MCP clients

Requires [uv](https://docs.astral.sh/uv/). Add a stdio server that runs:

```json
{ "command": "uvx", "args": ["sleeper-mcp"] }
```

Set `SLEEPER_MCP_SCOPE_REGISTRY` in the server's environment to use a scope registry.
````

The "Other MCP clients" part depends on publishing to PyPI first.

## Suggested next steps

1. Add a release workflow that builds the bundle from a staging directory without `dev`
   (friction 1), attaches the `.mcpb` to the GitHub release and publishes the wheel to PyPI.
2. Add the newest CPython to the CI matrix (friction 2).
3. Once structural scope and the username input land, add the `username` field above, update
   the README, and decide whether the registry field is still needed.
4. Add an icon and pass a version to `MCPServer`.
