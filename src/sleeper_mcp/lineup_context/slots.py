"""`rest_configured_starting_slots_v1` + `rest_string_zero_vacancy_v1` (A05).

Map only valid full-length string arrays to the registered configuration. Exact `"0"` is an
explicit vacancy unless the same source also lists `"0"` as a member. Never shift, pad or truncate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from sleeper_mcp.contract import SLOT_POLICY, VACANCY_POLICY
from sleeper_mcp.json_values import OMITTED, Json, as_list, is_id_string, is_id_string_list
from sleeper_mcp.lineup_context.schema import Slot

VACANCY = "0"

MappingStatus = Literal[
    "mapped",  # every slot interpreted
    "defective",  # mapped, but duplicates or zero contradictions leave some slots unknown
    "lineup_unavailable",  # starters omitted, null or [] with configured slots
    "invalid",  # wrong type, non-string members or length mismatch
    "slot_mapping_unavailable",  # configuration missing, malformed or not applicable
]


@dataclass
class SlotMapping:
    status: MappingStatus
    slots: list[Slot] | None
    duplicate_ids: list[str] = field(default_factory=lambda: [])
    zero_conflict: bool = False

    @property
    def interpreted(self) -> bool:
        return self.status in ("mapped", "defective")


def map_slots(
    configuration: list[str] | None,
    starters: Json | object,
    source_players: Json | object = OMITTED,
) -> SlotMapping:
    """Map raw starters to the configured order. `configuration` None means unavailable."""
    if configuration is None:
        return SlotMapping("slot_mapping_unavailable", None)
    if starters is OMITTED or starters is None or starters == []:
        return SlotMapping("lineup_unavailable", None)
    if not is_id_string_list(starters) or len(starters) != len(configuration):
        return SlotMapping("invalid", None)
    counts = Counter(pid for pid in starters if pid != VACANCY)
    duplicates = sorted(pid for pid, count in counts.items() if count > 1)
    players = as_list(source_players)
    zero_conflict = VACANCY in starters and players is not None and VACANCY in players
    slots: list[Slot] = []
    for index, (label, pid) in enumerate(zip(configuration, starters, strict=True)):
        if pid == VACANCY:
            state: Literal["occupied", "empty", "unknown"] = "unknown" if zero_conflict else "empty"
            policies = [SLOT_POLICY, VACANCY_POLICY]
            player_id = None
        else:
            state = "occupied"
            policies = [SLOT_POLICY]
            player_id = pid
        slots.append(
            Slot(
                index=index,
                label=label,
                player_id=player_id,
                state=state,
                raw_assignment=pid,
                policy_ids=policies,
            )
        )
    defective = bool(duplicates) or zero_conflict
    return SlotMapping("defective" if defective else "mapped", slots, duplicates, zero_conflict)


def occupied_ids(raw: Json) -> list[str]:
    """Distinct valid non-sentinel IDs from a raw starters array, in order."""
    items = as_list(raw)
    if items is None:
        return []
    return list(dict.fromkeys(p for p in items if is_id_string(p) and p != VACANCY))
