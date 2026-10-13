"""Shared, persisted player-directory snapshot with a daily refresh gate (D7).

The directory is public NFL player data, not user data. A snapshot is reusable for less than
24 hours from its original acquisition. At most one refresh attempt is made per rolling 24 hours,
recorded in a gate file written before the attempt so that failed attempts and restarts count.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import anyio

from sleeper_mcp.contract import FETCH_CONTROLS, FetchControls
from sleeper_mcp.json_values import (
    Json,
    JsonObject,
    JsonParseError,
    as_object,
    dumps_compact,
    strict_json_loads,
)
from sleeper_mcp.sleeper import routes
from sleeper_mcp.sleeper.fetch import CallBudget, Clock, Fetcher
from sleeper_mcp.sleeper.observation import (
    RECORDED_HEADERS,
    FailureCause,
    SourceObservation,
    parse_utc_iso,
    utc_iso,
)
from sleeper_mcp.sleeper.routes import Route

logger = logging.getLogger(__name__)

SNAPSHOT_FORMAT = "sleeper_mcp.directory_snapshot.v1"
GATE_FORMAT = "sleeper_mcp.directory_gate.v1"
BODY_FILE = "players_nfl.json"
META_FILE = "players_nfl.meta.json"
GATE_FILE = "players_nfl.gate.json"


@dataclass(frozen=True)
class CacheDirectives:
    no_store: bool
    no_cache: bool
    must_revalidate: bool
    max_age: int | None

    @classmethod
    def parse(cls, header: str | None) -> CacheDirectives:
        names: dict[str, str | None] = {}
        for part in (header or "").split(","):
            name, _, value = part.strip().partition("=")
            if name:
                names[name.strip().lower()] = value.strip().strip('"') if value else None
        max_age_text = names.get("max-age")
        max_age = int(max_age_text) if max_age_text and max_age_text.isdigit() else None
        return cls(
            no_store="no-store" in names,
            no_cache="no-cache" in names,
            must_revalidate="must-revalidate" in names,
            max_age=max_age,
        )


@dataclass
class Snapshot:
    profiles: JsonObject
    url: str
    http_status: int | None
    headers: dict[str, str | None]
    request_started_at: datetime
    body_completed_at: datetime | None
    body_sha256: str
    body_bytes: int
    persisted: bool

    @property
    def directives(self) -> CacheDirectives:
        return CacheDirectives.parse(self.headers.get("cache-control"))


DirectoryStatus = Literal["reused", "refreshed", "unavailable"]


@dataclass
class DirectoryResult:
    status: DirectoryStatus
    observation: SourceObservation | None
    profiles: JsonObject | None
    acquired_at: datetime | None
    reuse_age_seconds: float | None
    cause: FailureCause | Literal["invalid_source_data"] | None
    explanation: str


class PlayerDirectoryStore:
    def __init__(
        self,
        cache_dir: Path,
        fetcher: Fetcher,
        clock: Clock,
        controls: FetchControls = FETCH_CONTROLS,
    ) -> None:
        self.cache_dir = cache_dir
        self.fetcher = fetcher
        self.clock = clock
        self.controls = controls
        self._lock = anyio.Lock()
        self._memory: Snapshot | None = None
        self._memory_gate: datetime | None = None
        self._disk_checked = False

    # Snapshot ---------------------------------------------------------------------------------

    def _age(self, snapshot: Snapshot) -> float:
        return (self.clock.now() - snapshot.request_started_at).total_seconds()

    def _reusable(self, snapshot: Snapshot) -> bool:
        age = self._age(snapshot)
        if not 0 <= age < self.controls.directory_snapshot_age_seconds_exclusive:
            return False
        directives = snapshot.directives
        if directives.no_cache:
            return False
        stale = directives.max_age is not None and age > directives.max_age
        return not (directives.must_revalidate and stale)

    def _load_disk_snapshot(self) -> Snapshot | None:
        try:
            meta = as_object(strict_json_loads((self.cache_dir / META_FILE).read_bytes()))
            if meta is None or meta.get("format") != SNAPSHOT_FORMAT:
                return None
            body = (self.cache_dir / BODY_FILE).read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest != meta.get("body_sha256") or len(body) != meta.get("body_bytes"):
                logger.warning("Directory snapshot discarded: hash or length mismatch.")
                return None
            profiles = as_object(strict_json_loads(body))
            headers_raw = as_object(meta.get("headers"))
            started = meta.get("request_started_at")
            completed = meta.get("body_completed_at")
            url = meta.get("url")
            status = meta.get("http_status")
            if (
                profiles is None
                or headers_raw is None
                or not isinstance(started, str)
                or not isinstance(url, str)
            ):
                return None
            headers: dict[str, str | None] = {}
            for name in RECORDED_HEADERS:
                value = headers_raw.get(name)
                headers[name] = value if isinstance(value, str) else None
            return Snapshot(
                profiles=profiles,
                url=url,
                http_status=status if type(status) is int else None,
                headers=headers,
                request_started_at=parse_utc_iso(started),
                body_completed_at=parse_utc_iso(completed) if isinstance(completed, str) else None,
                body_sha256=digest,
                body_bytes=len(body),
                persisted=True,
            )
        except FileNotFoundError:
            return None
        except (OSError, JsonParseError, ValueError) as exc:
            logger.warning("Directory snapshot unreadable (%s); ignoring it.", type(exc).__name__)
            return None

    def _current_snapshot(self) -> Snapshot | None:
        if self._memory is None and not self._disk_checked:
            self._disk_checked = True
            self._memory = self._load_disk_snapshot()
        return self._memory

    def _persist(self, snapshot: Snapshot, body: bytes) -> bool:
        meta: Json = {
            "format": SNAPSHOT_FORMAT,
            "route": Route.PLAYERS_NFL.value,
            "url": snapshot.url,
            "http_status": snapshot.http_status,
            "headers": dict(snapshot.headers),
            "request_started_at": utc_iso(snapshot.request_started_at),
            "body_completed_at": (
                utc_iso(snapshot.body_completed_at) if snapshot.body_completed_at else None
            ),
            "body_sha256": snapshot.body_sha256,
            "body_bytes": snapshot.body_bytes,
        }
        try:
            _atomic_write(self.cache_dir / BODY_FILE, body)
            _atomic_write(self.cache_dir / META_FILE, dumps_compact(meta).encode())
        except OSError as exc:
            logger.warning("Directory snapshot not persisted (%s).", type(exc).__name__)
            return False
        return True

    # Gate -------------------------------------------------------------------------------------

    def _last_attempt(self) -> datetime | None:
        latest = self._memory_gate
        path = self.cache_dir / GATE_FILE
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return latest
        except OSError:
            # An unreadable gate cannot prove that no recent attempt happened.
            return self.clock.now()
        recorded: datetime | None = None
        try:
            gate = as_object(strict_json_loads(raw))
            text = gate.get("last_attempt_at") if gate is not None else None
            if gate is not None and gate.get("format") == GATE_FORMAT and isinstance(text, str):
                recorded = parse_utc_iso(text)
        except (JsonParseError, ValueError):
            recorded = None
        if recorded is None:
            # A corrupt gate counts as an attempt at its modification time.
            try:
                recorded = datetime.fromtimestamp(path.stat().st_mtime, tz=self.clock.now().tzinfo)
            except OSError:
                recorded = self.clock.now()
        if latest is None or recorded > latest:
            latest = recorded
        return latest

    def _gate_open(self) -> bool:
        last = self._last_attempt()
        if last is None:
            return True
        elapsed = (self.clock.now() - last).total_seconds()
        return elapsed >= self.controls.directory_snapshot_age_seconds_exclusive or elapsed < 0

    def _record_attempt(self) -> bool:
        now = self.clock.now()
        self._memory_gate = now
        gate: Json = {"format": GATE_FORMAT, "last_attempt_at": utc_iso(now)}
        try:
            _atomic_write(self.cache_dir / GATE_FILE, dumps_compact(gate).encode())
        except OSError as exc:
            logger.warning("Directory gate not writable (%s); not refreshing.", type(exc).__name__)
            return False
        return True

    # Public -----------------------------------------------------------------------------------

    def _reuse(self, snapshot: Snapshot, source_id: str) -> DirectoryResult:
        request = routes.players_nfl()
        observation = SourceObservation(
            id=source_id,
            route=Route.PLAYERS_NFL,
            url=snapshot.url,
            coordinate=dict(request.coordinate),
            origin="local_reuse",
            request_started_at=snapshot.request_started_at,
            body_completed_at=snapshot.body_completed_at,
            http_status=snapshot.http_status,
            headers=dict(snapshot.headers),
            body_sha256=snapshot.body_sha256,
            body_bytes=snapshot.body_bytes,
            parse="parsed",
        )
        return DirectoryResult(
            status="reused",
            observation=observation,
            profiles=snapshot.profiles,
            acquired_at=snapshot.request_started_at,
            reuse_age_seconds=self._age(snapshot),
            cause=None,
            explanation="A local directory snapshot under 24 hours old was reused.",
        )

    async def get(self, budget: CallBudget, source_id: str) -> DirectoryResult:
        snapshot = self._current_snapshot()
        if snapshot is not None and self._reusable(snapshot):
            return self._reuse(snapshot, source_id)

        result: DirectoryResult | None = None
        with anyio.move_on_after(max(0.0, budget.remaining())):
            async with self._lock:
                result = await self._refresh_locked(budget, source_id)
        if result is None:
            return _unavailable(
                "deadline_exceeded", "The call deadline passed while another refresh ran."
            )
        return result

    async def _refresh_locked(self, budget: CallBudget, source_id: str) -> DirectoryResult:
        # Another caller may have refreshed while this one waited (single flight).
        snapshot = self._memory
        if snapshot is not None and self._reusable(snapshot):
            return self._reuse(snapshot, source_id)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return _unavailable("cache_unavailable", "The cache directory cannot be created.")
        if not self._gate_open():
            return _unavailable(
                "directory_gate_used",
                "No reusable snapshot, and the one refresh attempt for this 24-hour window "
                "has already been made.",
            )
        if not self._record_attempt():
            return _unavailable(
                "cache_unavailable",
                "The refresh gate cannot be recorded, so no refresh attempt was made.",
            )

        observation = await self.fetcher.fetch(routes.players_nfl(), budget, source_id)
        if not observation.ok:
            failure = observation.failure
            return DirectoryResult(
                status="unavailable",
                observation=observation,
                profiles=None,
                acquired_at=None,
                reuse_age_seconds=None,
                cause=failure.cause if failure else None,
                explanation="The directory refresh attempt failed.",
            )
        profiles = as_object(observation.body)
        if profiles is None:
            return DirectoryResult(
                status="unavailable",
                observation=observation,
                profiles=None,
                acquired_at=None,
                reuse_age_seconds=None,
                cause="invalid_source_data",
                explanation="The directory response is not a JSON object.",
            )
        assert observation.request_started_at is not None
        assert observation.body_sha256 is not None and observation.body_bytes is not None
        headers = observation.headers or {}
        fresh = Snapshot(
            profiles=profiles,
            url=observation.url,
            http_status=observation.http_status,
            headers=dict(headers),
            request_started_at=observation.request_started_at,
            body_completed_at=observation.body_completed_at,
            body_sha256=observation.body_sha256,
            body_bytes=observation.body_bytes,
            persisted=False,
        )
        if not fresh.directives.no_store and observation.raw_body is not None:
            fresh.persisted = self._persist(fresh, observation.raw_body)
        self._memory = fresh
        return DirectoryResult(
            status="refreshed",
            observation=observation,
            profiles=profiles,
            acquired_at=fresh.request_started_at,
            reuse_age_seconds=None,
            cause=None,
            explanation="The directory was retrieved during this call.",
        )


def _unavailable(cause: FailureCause, explanation: str) -> DirectoryResult:
    return DirectoryResult(
        status="unavailable",
        observation=None,
        profiles=None,
        acquired_at=None,
        reuse_age_seconds=None,
        cause=cause,
        explanation=explanation,
    )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
