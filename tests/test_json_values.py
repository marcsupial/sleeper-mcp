from __future__ import annotations

import pytest

from sleeper_mcp.json_values import (
    OMITTED,
    Json,
    JsonParseError,
    dumps_compact,
    field_state,
    is_id_string,
    is_id_string_list,
    is_json_int,
    is_json_number,
    strict_json_loads,
    value_state,
)


def test_field_state_keeps_every_state_distinct() -> None:
    obj: dict[str, Json] = {
        "n": None,
        "s": "x",
        "i": 1,
        "f": 1.5,
        "b": True,
        "a": [],
        "o": {},
    }
    assert field_state(obj, "missing") == "omitted"
    assert field_state(obj, "n") == "null"
    assert field_state(obj, "s") == "string"
    assert field_state(obj, "i") == "integer"
    assert field_state(obj, "f") == "number"
    assert field_state(obj, "b") == "boolean"
    assert field_state(obj, "a") == "array"
    assert field_state(obj, "o") == "object"
    assert value_state(OMITTED) == "omitted"


@pytest.mark.parametrize("value", [True, False, 5.0, "5", None])
def test_booleans_strings_and_floats_are_not_json_integers(value: object) -> None:
    assert not is_json_int(value)


def test_json_integer_and_number() -> None:
    assert is_json_int(5)
    assert is_json_number(0.03999999910593033)
    assert not is_json_number(True)
    assert not is_json_number(float("nan"))


@pytest.mark.parametrize("value", ["", " x", "x ", 5, None, ["x"]])
def test_invalid_id_strings(value: object) -> None:
    assert not is_id_string(value)


def test_id_string_list() -> None:
    assert is_id_string_list([])
    assert is_id_string_list(["1", "JAX"])
    assert not is_id_string_list(["1", 2])
    assert not is_id_string_list(["1", ""])
    assert not is_id_string_list(None)


@pytest.mark.parametrize(
    ("body", "cause"),
    [
        (b"", "empty_body"),
        (b"   ", "empty_body"),
        (b"\xff\xfe", "invalid_utf8"),
        (b"{", "invalid_json"),
        (b'{"a": NaN}', "non_json_value"),
        (b"[Infinity]", "non_json_value"),
        (b"-Infinity", "non_json_value"),
        (b'{"a": 1, "a": 2}', "duplicate_key"),
        (b'[{"k": 1, "k": 1}]', "duplicate_key"),
    ],
)
def test_strict_parse_failures(body: bytes, cause: str) -> None:
    with pytest.raises(JsonParseError) as info:
        strict_json_loads(body)
    assert info.value.cause == cause


def test_strict_parse_keeps_exact_floats_and_null() -> None:
    assert strict_json_loads(b"null") is None
    parsed = strict_json_loads(b'{"pass_yd": 0.03999999910593033, "z": 0, "neg": -3}')
    assert parsed == {"pass_yd": 0.03999999910593033, "z": 0, "neg": -3}
    assert dumps_compact(parsed) == '{"pass_yd":0.03999999910593033,"z":0,"neg":-3}'


def test_dumps_refuses_nan() -> None:
    with pytest.raises(ValueError):
        dumps_compact(float("nan"))
