"""Build the Claude Desktop bundle (`dist/sleeper-mcp-<version>.mcpb`) without the dev group.

Claude Desktop installs a `uv` bundle with a plain `uv sync`, which would also install the `dev`
dependency group (pyright, ruff, pytest, ...). This script packs a staged copy of the project
whose `dev` group has been removed, so contributors' `uv sync` is unchanged.

    uv run python scripts/build_mcpb.py                  # build and check the archive
    uv run python scripts/build_mcpb.py --check-install  # also install it as Desktop would

Requires uv and npx on PATH.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"
BUNDLE_FILES = ("manifest.json", "pyproject.toml", "uv.lock", "LICENSE")
BUNDLE_DIRS = ("src",)


class BuildError(Exception):
    pass


def normalize(name: str) -> str:
    """PEP 503 name normalization, as used in uv.lock."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if match is None:
        raise BuildError(f"cannot read a package name from dev requirement {requirement!r}")
    return normalize(match.group(1))


def load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # `uv run` exports VIRTUAL_ENV for the repo's own venv; don't let it leak into the stage.
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        raise BuildError(
            f"`{' '.join(args)}` failed with exit code {result.returncode}\n"
            f"{result.stdout}{result.stderr}"
        )
    return result


def locked_packages(lock: dict[str, Any]) -> dict[str, list[str]]:
    """Map each package name to its lock entries, serialized so they compare exactly."""
    packages: dict[str, list[str]] = {}
    for entry in lock.get("package", []):
        packages.setdefault(entry["name"], []).append(json.dumps(entry, sort_keys=True))
    return {name: sorted(entries) for name, entries in packages.items()}


def runtime_closure(lock: dict[str, Any], project: str) -> set[str]:
    """Names of the project and every package its runtime dependencies reach in the lock."""
    entries: dict[str, list[dict[str, Any]]] = {}
    for entry in lock.get("package", []):
        entries.setdefault(entry["name"], []).append(entry)

    reached: set[str] = set()
    pending: list[tuple[str, tuple[str, ...]]] = [(project, ())]
    while pending:
        name, extras = pending.pop()
        key = f"{name}[{','.join(extras)}]"
        if key in reached:
            continue
        reached.add(key)
        for entry in entries.get(name, []):
            dependencies = list(entry.get("dependencies", []))
            for extra in extras:
                dependencies += entry.get("optional-dependencies", {}).get(extra, [])
            for dependency in dependencies:
                pending.append((dependency["name"], tuple(sorted(dependency.get("extra", [])))))
    return {key.split("[", 1)[0] for key in reached}


def check_lock_change(before: dict[str, Any], after: dict[str, Any], project: str) -> list[str]:
    """Fail unless exactly the dev-only packages left the lock and nothing else changed."""
    old = locked_packages(before)
    new = locked_packages(after)
    dev_only = set(old) - runtime_closure(before, project)
    removed = set(old) - set(new)
    added = set(new) - set(old)
    changed = {name for name in set(old) & set(new) - {project} if old[name] != new[name]}

    problems: list[str] = []
    if removed != dev_only:
        if dev_only - removed:
            problems.append(f"dev-only packages still locked: {sorted(dev_only - removed)}")
        if removed - dev_only:
            problems.append(f"runtime packages removed: {sorted(removed - dev_only)}")
    if added:
        problems.append(f"packages added: {sorted(added)}")
    if changed:
        problems.append(f"packages changed: {sorted(changed)}")

    project_old = [e for e in before["package"] if e["name"] == project]
    project_new = [e for e in after["package"] if e["name"] == project]
    for field in ("version", "dependencies", "optional-dependencies"):
        if [e.get(field) for e in project_old] != [e.get(field) for e in project_new]:
            problems.append(f"{project} {field} changed")

    if problems:
        raise BuildError(
            "unexpected uv.lock change after removing the dev group:\n  " + "\n  ".join(problems)
        )
    return sorted(removed)


def stage_project(stage: Path) -> None:
    for name in BUNDLE_FILES:
        shutil.copy2(REPO / name, stage / name)
    for name in BUNDLE_DIRS:
        shutil.copytree(
            REPO / name, stage / name, ignore=shutil.ignore_patterns("__pycache__", "*.py[cod]")
        )


def drop_dev_group(stage: Path, project: str) -> list[str]:
    pyproject = load_toml(stage / "pyproject.toml")
    dev = pyproject.get("dependency-groups", {}).get("dev", [])
    if not all(isinstance(item, str) for item in dev):
        raise BuildError("the dev group includes another group; expand it before building")
    names = [requirement_name(item) for item in dev]
    if not names:
        raise BuildError("pyproject.toml has no dev dependency group to remove")

    before = load_toml(stage / "uv.lock")
    # --no-sync: never create a .venv in the stage, since everything there is packed.
    run(["uv", "remove", "--no-sync", "--group", "dev", *names], cwd=stage)
    after = load_toml(stage / "uv.lock")

    remaining = load_toml(stage / "pyproject.toml").get("dependency-groups", {}).get("dev", [])
    if remaining:
        raise BuildError(f"dev group still lists {remaining}")
    return check_lock_change(before, after, project)


def check_archive(bundle: Path) -> list[str]:
    with zipfile.ZipFile(bundle) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        pyproject = tomllib.loads(archive.read("pyproject.toml").decode())

    unexpected = [
        name
        for name in names
        if name not in BUNDLE_FILES and name.split("/", 1)[0] not in BUNDLE_DIRS
    ]
    caches = [
        name
        for name in names
        if "__pycache__" in name.split("/") or name.endswith((".pyc", ".pyo"))
    ]
    missing = [name for name in BUNDLE_FILES if name not in names]
    problems: list[str] = []
    if unexpected:
        problems.append(f"unexpected files: {unexpected}")
    if caches:
        problems.append(f"bytecode caches: {caches}")
    if missing:
        problems.append(f"missing files: {missing}")
    if pyproject.get("dependency-groups", {}).get("dev"):
        problems.append("the bundled pyproject.toml still has a dev group")
    if problems:
        raise BuildError(f"{bundle.name} failed the content check:\n  " + "\n  ".join(problems))
    return names


def venv_size(venv: Path) -> int:
    """Bytes on disk, like `du`: symlinks aren't followed and hardlinks count once."""
    seen: set[int] = set()
    size = 0
    for path in venv.rglob("*"):
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_ino in seen:
            continue
        seen.add(info.st_ino)
        size += info.st_size
    return size


def check_install(bundle: Path, dev_only: list[str]) -> None:
    """Install the bundle the way Claude Desktop does and confirm no dev tool lands in the venv."""
    with tempfile.TemporaryDirectory(prefix="sleeper-mcp-install-") as tmp:
        target = Path(tmp)
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(target)
        run(["uv", "sync", "--locked", "--quiet"], cwd=target)
        listed = run(
            ["uv", "pip", "list", "--format", "json", "--python", str(target / ".venv")],
            cwd=target,
        )
        installed = {normalize(item["name"]) for item in json.loads(listed.stdout)}
        leaked = sorted(installed & set(dev_only))
        if leaked:
            raise BuildError(f"the installed bundle contains dev packages: {leaked}")
        run(["uv", "run", "--frozen", "python", "-c", "import sleeper_mcp.server"], cwd=target)
        size = venv_size(target / ".venv")
        print(
            f"install check: {len(installed)} packages, venv {size / 1e6:.0f} MB, no dev packages"
        )


def build(check_installed: bool) -> Path:
    pyproject = load_toml(REPO / "pyproject.toml")
    project = normalize(pyproject["project"]["name"])
    version = pyproject["project"]["version"]
    manifest = json.loads((REPO / "manifest.json").read_text())
    if manifest["version"] != version:
        raise BuildError(f"manifest.json version {manifest['version']} != pyproject {version}")

    bundle = REPO / "dist" / f"{project}-{version}.mcpb"
    bundle.parent.mkdir(exist_ok=True)
    bundle.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="sleeper-mcp-stage-") as tmp:
        stage = Path(tmp)
        stage_project(stage)
        removed = drop_dev_group(stage, project)
        print(f"removed {len(removed)} dev-only packages from uv.lock: {', '.join(removed)}")
        run(["npx", "--yes", MCPB_CLI, "pack", str(stage), str(bundle)], cwd=REPO)

    names = check_archive(bundle)
    print(f"built {bundle.relative_to(REPO)}: {len(names)} files, {bundle.stat().st_size:,} bytes")
    if check_installed:
        check_install(bundle, removed)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the Claude Desktop bundle without the dev group."
    )
    parser.add_argument(
        "--check-install",
        action="store_true",
        help="also install the bundle with `uv sync --locked` and check its venv",
    )
    args = parser.parse_args()
    try:
        build(args.check_install)
    except BuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
