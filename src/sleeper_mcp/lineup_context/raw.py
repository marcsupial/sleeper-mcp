"""Builders for raw-field records."""

from __future__ import annotations

from sleeper_mcp.json_values import OMITTED, Json, JsonObject, get_field, value_state
from sleeper_mcp.lineup_context.schema import Observation, RawField


def raw_field(obj: JsonObject | None, key: str, path: str | None = None) -> RawField:
    """Record `obj[key]` with its state; a missing object reads as an omitted field."""
    value = OMITTED if obj is None else get_field(obj, key)
    return RawField(
        path=path or key,
        state=value_state(value),
        value=None if value is OMITTED else value,  # pyright: ignore[reportArgumentType]
    )


def raw_value(path: str, value: Json) -> RawField:
    return RawField(path=path, state=value_state(value), value=value)


def observation(
    source_ref: str,
    selector: str,
    container: Json,
    keys: list[str],
    obj: JsonObject | None,
) -> Observation:
    return Observation(
        source_ref=source_ref,
        selector=selector,
        container=value_state(container),
        fields=[raw_field(obj, key) for key in keys],
    )
