from importlib.metadata import version

import sleeper_mcp


def test_package_is_installed() -> None:
    assert version("sleeper-mcp")


def test_entry_point_is_callable() -> None:
    assert callable(sleeper_mcp.main)
