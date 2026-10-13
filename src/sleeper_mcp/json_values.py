"""JSON value helpers that keep omitted, null, empty and malformed values distinct."""

from __future__ import annotations

import json
import math
from typing import Literal, TypeGuard

from pydantic import JsonValue

type Json = JsonValue
type JsonObject = dict[str, JsonValue]

FieldState = Literal["omitted", "null", "string", "integer", "number", "boolean", "array", "object"]

FIELD_STATES: tuple[FieldState, ...] = (
    "omitted",
    "null",
    "string",
    "integer",
    "number",
    "boolean",
    "array",
    "object",
)


class _Omitted:
    """Marker for a key that is absent from its JSON object."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "OMITTED"


OMITTED = _Omitted()


def value_state(value: Json | _Omitted) -> FieldState:
    """Classify a JSON value, or the omission marker, without coercing it."""
    if isinstance(value, _Omitted):
        return "omitted"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def get_field(obj: JsonObject, key: str) -> Json | _Omitted:
    """Return the raw value for `key`, or `OMITTED` when the key is absent."""
    return obj.get(key, OMITTED)


def field_state(obj: JsonObject, key: str) -> FieldState:
    return value_state(get_field(obj, key))


def is_json_int(value: object) -> TypeGuard[int]:
    """True for a JSON integer. Booleans are not integers."""
    return type(value) is int


def is_json_number(value: object) -> TypeGuard[int | float]:
    """True for a finite JSON number. Booleans are not numbers."""
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


def is_id_string(value: object) -> TypeGuard[str]:
    """True for a nonempty string with no surrounding whitespace."""
    return isinstance(value, str) and bool(value) and value.strip() == value


def is_id_string_list(value: object) -> TypeGuard[list[str]]:
    """True for a JSON array whose members are all ID strings. `[]` qualifies."""
    if not isinstance(value, list):
        return False
    items: list[object] = list(value)  # pyright: ignore[reportUnknownArgumentType]
    return all(is_id_string(item) for item in items)


def as_object(value: object) -> JsonObject | None:
    """Return `value` as a JSON object, or None when it is not one."""
    if isinstance(value, dict):
        return value  # pyright: ignore[reportUnknownVariableType]
    return None


def as_list(value: object) -> list[Json] | None:
    if isinstance(value, list):
        return value  # pyright: ignore[reportUnknownVariableType]
    return None


ParseCause = Literal[
    "empty_body", "invalid_utf8", "invalid_json", "non_json_value", "duplicate_key"
]


class JsonParseError(Exception):
    def __init__(self, cause: ParseCause, detail: str) -> None:
        super().__init__(f"{cause}: {detail}")
        self.cause: ParseCause = cause
        self.detail = detail


def _reject_constant(name: str) -> Json:
    raise JsonParseError("non_json_value", f"{name} is not a JSON value")


def _reject_duplicates(pairs: list[tuple[str, Json]]) -> JsonObject:
    obj: JsonObject = {}
    for key, value in pairs:
        if key in obj:
            raise JsonParseError("duplicate_key", "an object repeats a key")
        obj[key] = value
    return obj


def strict_json_loads(body: bytes) -> Json:
    """Parse a UTF-8 JSON document, rejecting NaN/Infinity, duplicate keys and empty bodies."""
    if not body:
        raise JsonParseError("empty_body", "the response body is empty")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JsonParseError("invalid_utf8", "the response body is not UTF-8") from exc
    if not text.strip():
        raise JsonParseError("empty_body", "the response body is only whitespace")
    try:
        parsed: Json = json.loads(
            text,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicates,
        )
    except JsonParseError:
        raise
    except (ValueError, RecursionError) as exc:
        raise JsonParseError("invalid_json", "the response body is not valid JSON") from exc
    return parsed


def dumps_compact(value: Json) -> str:
    """Serialize compactly, refusing NaN and Infinity."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
