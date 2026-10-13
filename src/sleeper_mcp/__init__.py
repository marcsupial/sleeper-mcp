def main() -> None:
    """Run the read-only Sleeper MCP server over stdio."""
    import logging

    from sleeper_mcp.server import create_server

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    create_server().run("stdio")
