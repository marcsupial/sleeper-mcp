import zipfile
from pathlib import Path
from typing import Any

import pytest

from scripts.build_mcpb import (
    BuildError,
    check_archive,
    check_lock_change,
    requirement_name,
    runtime_closure,
)


def package(name: str, version: str = "1.0", **fields: Any) -> dict[str, Any]:
    # Lock fields use hyphens, so they're spelled with underscores here.
    return {"name": name, "version": version} | {k.replace("_", "-"): v for k, v in fields.items()}


def dependency(name: str, *extras: str) -> dict[str, Any]:
    return {"name": name, "extra": list(extras)} if extras else {"name": name}


PROJECT = package(
    "demo",
    dependencies=[dependency("web", "cli")],
    dev_dependencies={"dev": [dependency("tester")]},
)
LOCK: dict[str, Any] = {
    "package": [
        PROJECT,
        package("web", optional_dependencies={"cli": [dependency("shared")]}),
        package("shared"),
        package("tester", dependencies=[dependency("shared"), dependency("plugin")]),
        package("plugin"),
    ]
}


def without(*names: str) -> dict[str, Any]:
    packages = [p for p in LOCK["package"] if p["name"] not in names]
    project = {key: value for key, value in PROJECT.items() if key != "dev-dependencies"}
    return {"package": [project if p["name"] == "demo" else p for p in packages]}


def test_requirement_name_strips_specifiers_and_normalizes() -> None:
    assert requirement_name("Types_JSONSchema>=4.25") == "types-jsonschema"
    assert requirement_name("pytest[testing] ; python_version < '4'") == "pytest"


def test_runtime_closure_follows_extras_but_not_dev_dependencies() -> None:
    assert runtime_closure(LOCK, "demo") == {"demo", "web", "shared"}


def test_lock_change_accepts_removing_exactly_the_dev_only_packages() -> None:
    assert check_lock_change(LOCK, without("tester", "plugin"), "demo") == ["plugin", "tester"]


def test_lock_change_rejects_a_dev_only_package_left_behind() -> None:
    with pytest.raises(BuildError, match=r"dev-only packages still locked: \['plugin'\]"):
        check_lock_change(LOCK, without("tester"), "demo")


def test_lock_change_rejects_removing_a_runtime_package() -> None:
    with pytest.raises(BuildError, match=r"runtime packages removed: \['shared'\]"):
        check_lock_change(LOCK, without("tester", "plugin", "shared"), "demo")


def test_lock_change_rejects_a_version_change() -> None:
    after = without("tester", "plugin")
    after["package"] = [
        package("shared", "2.0") if p["name"] == "shared" else p for p in after["package"]
    ]
    with pytest.raises(BuildError, match=r"packages changed: \['shared'\]"):
        check_lock_change(LOCK, after, "demo")


def make_bundle(path: Path, extra: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", "{}")
        archive.writestr("pyproject.toml", "[dependency-groups]\ndev = []\n")
        archive.writestr("uv.lock", "")
        archive.writestr("LICENSE", "")
        archive.writestr("src/demo/__init__.py", "")
        for name in extra:
            archive.writestr(name, "")
    return path


def test_archive_check_accepts_only_the_allowlisted_files(tmp_path: Path) -> None:
    assert len(check_archive(make_bundle(tmp_path / "ok.mcpb", []))) == 5


@pytest.mark.parametrize(
    ("extra", "problem"),
    [
        ("tests/test_demo.py", "unexpected files"),
        (".venv/bin/python", "unexpected files"),
        ("src/demo/__pycache__/x.cpython-312.pyc", "bytecode caches"),
    ],
)
def test_archive_check_rejects_other_files(tmp_path: Path, extra: str, problem: str) -> None:
    with pytest.raises(BuildError, match=problem):
        check_archive(make_bundle(tmp_path / "bad.mcpb", [extra]))
