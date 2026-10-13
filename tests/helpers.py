"""Shared test helpers. Every identifier here is synthetic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio.lowlevel

FIXTURES = Path(__file__).parent / "fixtures"
REGISTRY_PATH = FIXTURES / "scope-registry.json"
BASE_URL = "https://sleeper.test/v1"


class FakeClock:
    """Monotonic and wall clocks that only move when told to, or when slept on."""

    def __init__(self, start: datetime | None = None) -> None:
        self.start = start or datetime(2026, 10, 13, 16, 0, 0, tzinfo=UTC)
        self.elapsed = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return 1000.0 + self.elapsed

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.elapsed)

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds
        await anyio.lowlevel.checkpoint()
