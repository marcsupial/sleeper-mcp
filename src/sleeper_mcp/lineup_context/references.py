"""Internal-reference checks for a v1 object: every reference resolves inside the object."""

from __future__ import annotations

from collections.abc import Iterator

from sleeper_mcp.json_values import Json, as_list, as_object
from sleeper_mcp.lineup_context.schema import LineupContextV1

SOURCE_KEYS = {"source_ref", "current_source_ref", "weekly_source_ref", "directory_source_ref"}


def _walk(value: Json, path: str = "$") -> Iterator[tuple[str, str, Json]]:
    obj = as_object(value)
    if obj is not None:
        for key, child in obj.items():
            yield path, key, child
            # Raw provider values are data, never references.
            if key in (
                "value",
                "raw",
                "raw_assignment",
                "settings",
                "scoring_settings",
                "roster_positions",
                "expected",
                "requested",
                "coordinate",
            ):
                continue
            yield from _walk(child, f"{path}.{key}")
        return
    items = as_list(value)
    if items is not None:
        for index, child in enumerate(items):
            yield from _walk(child, f"{path}[{index}]")


def reference_errors(result: LineupContextV1) -> list[str]:
    errors: list[str] = []
    data: Json = result.model_dump(mode="json")
    source_ids = [s.id for s in result.sources]
    if len(set(source_ids)) != len(source_ids):
        errors.append("source ids are not unique")
    sources = set(source_ids)
    policies = set(result.definitions.policies)
    catalogs = set(result.definitions.field_catalog)
    rules = set(result.definitions.slot_rules)

    for path, key, value in _walk(data):
        if key == "source_refs":
            for ref in as_list(value) or []:
                if ref not in sources:
                    errors.append(f"{path}.source_refs names unknown source {ref!r}")
        elif key in SOURCE_KEYS and value is not None and value not in sources:
            errors.append(f"{path}.{key} names unknown source {value!r}")
        elif key == "policy_id" and value not in policies:
            errors.append(f"{path}.policy_id {value!r} is not defined")
        elif key == "policy_ids":
            for ref in as_list(value) or []:
                if ref not in policies:
                    errors.append(f"{path}.policy_ids {ref!r} is not defined")
        elif key == "profile_fields_ref" and value not in catalogs:
            errors.append(f"{path}.profile_fields_ref {value!r} is not in the field catalog")
        elif key == "slot_rule_labels":
            for label in as_list(value) or []:
                if label not in rules:
                    errors.append(f"{path} names undefined slot rule {label!r}")

    issue_ids = [i.id for i in result.issues]
    if len(set(issue_ids)) != len(issue_ids):
        errors.append("issue ids are not unique")

    association = result.resolution_evidence.association
    rosters = {c.roster_id for c in association.candidates}
    rosters |= {
        r.roster_id.value for r in association.row_evidence if isinstance(r.roster_id.value, int)
    }
    candidates = {c.roster_id for c in association.candidates}
    context = result.context
    players: set[str] = set(context.player_details.data) if context else set()
    slots = {s.index for s in context.weekly_lineup.data.slots or []} if context else set[int]()
    for issue in result.issues:
        for ref in issue.affected:
            if ref.kind == "source" and ref.ref not in sources:
                errors.append(f"{issue.id} affects unknown source {ref.ref!r}")
            elif ref.kind == "player" and ref.ref not in players:
                errors.append(f"{issue.id} affects player {ref.ref!r} absent from player_details")
            elif ref.kind == "slot" and (not ref.ref.isdigit() or int(ref.ref) not in slots):
                errors.append(f"{issue.id} affects unknown slot {ref.ref!r}")
            elif ref.kind == "roster" and (not ref.ref.isdigit() or int(ref.ref) not in rosters):
                errors.append(f"{issue.id} affects unknown roster {ref.ref!r}")
        if issue.next_action is not None:
            for ref in issue.next_action.refs:
                if ref.startswith("roster:"):
                    number = ref.removeprefix("roster:")
                    if not number.isdigit() or int(number) not in candidates:
                        errors.append(f"{issue.id} next action names non-candidate {ref!r}")
                elif ref not in sources:
                    errors.append(f"{issue.id} next action names unknown source {ref!r}")

    # Branch/target agreement.
    selected = association.selected
    if result.resolved.roster_id != (selected.roster_id if selected else None):
        errors.append("resolved.roster_id disagrees with the association selection")
    week = result.resolution_evidence.week
    if (
        week.status == "resolved"
        and result.resolved.week is None
        and result.outcome
        in (
            "complete",
            "partial",
        )
    ):
        errors.append("a resolved week is missing from resolved.week")
    if result.resolved.week is not None and week.status != "resolved":
        errors.append("resolved.week is set without a resolved week")
    if context is not None:
        scope = context.weekly_lineup.scope
        if scope.week != result.resolved.week or scope.roster_id != result.resolved.roster_id:
            errors.append("weekly_lineup scope disagrees with resolved coordinates")
        if context.roster.scope.roster_id != result.resolved.roster_id:
            errors.append("roster scope disagrees with resolved.roster_id")
    return errors
