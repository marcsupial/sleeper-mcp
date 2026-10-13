from __future__ import annotations

from typing import Any

import pytest

from sleeper_mcp.json_values import OMITTED
from sleeper_mcp.lineup_context.slots import map_slots

CONFIG = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "SUPER_FLEX"]
FULL = ["A", "B", "C", "D", "E", "F", "G", "H", "I"]


def test_valid_array_maps_every_index_and_repeated_label() -> None:
    mapping = map_slots(CONFIG, FULL)
    assert mapping.status == "mapped"
    assert mapping.slots is not None
    assert [s.label for s in mapping.slots] == CONFIG
    assert [s.index for s in mapping.slots if s.label == "FLEX"] == [6, 7]
    assert mapping.slots[8].label == "SUPER_FLEX"


def test_string_zero_is_an_explicit_vacancy() -> None:
    mapping = map_slots(CONFIG, ["A", "0", *FULL[2:]])
    assert mapping.slots is not None
    slot = mapping.slots[1]
    assert (slot.player_id, slot.state, slot.raw_assignment) == (None, "empty", "0")
    assert "rest_string_zero_vacancy_v1" in slot.policy_ids


def test_all_zero_array_is_completely_interpreted() -> None:
    mapping = map_slots(CONFIG, ["0"] * 9)
    assert mapping.status == "mapped"
    assert mapping.slots is not None
    assert {s.state for s in mapping.slots} == {"empty"}


def test_zero_also_listed_in_players_is_unknown() -> None:
    mapping = map_slots(CONFIG, ["A", "0", "0", *FULL[3:]], ["A", "0"])
    assert mapping.status == "defective"
    assert mapping.slots is not None
    assert [s.state for s in mapping.slots[1:3]] == ["unknown", "unknown"]


def test_duplicate_nonzero_ids_keep_every_occurrence() -> None:
    mapping = map_slots(CONFIG, ["A", "B", "B", *FULL[3:]])
    assert mapping.status == "defective"
    assert mapping.duplicate_ids == ["B"]
    assert mapping.slots is not None
    assert [s.player_id for s in mapping.slots[1:3]] == ["B", "B"]


@pytest.mark.parametrize("starters", [OMITTED, None, []])
def test_missing_null_or_empty_starters_are_unavailable(starters: Any) -> None:
    assert map_slots(CONFIG, starters).status == "lineup_unavailable"


@pytest.mark.parametrize(
    "starters",
    [
        FULL[:-1],  # too short: never padded
        [*FULL, "J"],  # too long: never truncated
        ["A", 0, *FULL[2:]],  # numeric zero is not the sentinel
        ["A", "", *FULL[2:]],
        ["A", " ", *FULL[2:]],
        ["A", None, *FULL[2:]],
        "A,B",
        {"0": "A"},
    ],
)
def test_malformed_starters_are_not_mapped(starters: Any) -> None:
    mapping = map_slots(CONFIG, starters)
    assert mapping.status == "invalid"
    assert mapping.slots is None


def test_missing_configuration_leaves_mapping_unavailable() -> None:
    assert map_slots(None, FULL).status == "slot_mapping_unavailable"
