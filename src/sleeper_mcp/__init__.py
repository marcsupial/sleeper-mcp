def main(argv: list[str] | None = None) -> None:
    """Run the read-only Sleeper MCP server over stdio, or check its configuration."""
    import argparse
    import logging
    import sys
    from importlib.metadata import version

    parser = argparse.ArgumentParser(
        prog="sleeper-mcp", description="Read-only MCP server for Sleeper fantasy football."
    )
    parser.add_argument("--version", action="version", version=version("sleeper-mcp"))
    parser.add_argument(
        "--check",
        action="store_true",
        help="report the configuration without contacting Sleeper, then exit",
    )
    args = parser.parse_args(argv)

    # Logs go to stderr: stdout carries the MCP protocol.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from sleeper_mcp.config import Config

    if args.check:
        from sleeper_mcp.check import run_check

        sys.exit(run_check(Config.from_env(), sys.stdout))

    from sleeper_mcp.server import create_server

    create_server().run("stdio")
