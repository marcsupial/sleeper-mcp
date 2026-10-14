"""Supported scope: structural rules, plus an optional private registry of pinned leagues.

A league is supported structurally when its live settings meet the STRUCTURAL_* rules and every
starting slot has a slot rule. A registry, when configured, pins exact instances (which then must
match their entry) and can exclude leagues. A configured registry that is refused supports nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sleeper_mcp.contract import (
    FETCH_CONTROLS,
    OPTIONAL_POLICY_BINDINGS,
    POLICY_BINDINGS,
    SLOT_RULES,
    FetchControls,
)
from sleeper_mcp.json_values import (
    Json,
    JsonParseError,
    as_list,
    as_object,
    is_id_string,
    is_id_string_list,
    is_json_int,
    strict_json_loads,
)

logger = logging.getLogger(__name__)

AutomaticWeek = Literal["agreeing_same_season_four_signals", "explicit_week_only"]
MappingBasis = Literal[
    "observed_selected_configuration",
    "retained_selected_league_configuration_observed_view",
]


TargetBasis = Literal["registered", "structural_match"]

STRUCTURAL_SPORT = "nfl"
STRUCTURAL_SEASON_TYPE = "regular"
STRUCTURAL_BEST_BALL = 0
STRUCTURAL_WEEK_DOMAIN = (1, 18)
# Sleeper's league status for a finished season. Such a league keeps only its latest settings, so
# it gets the rules accepted for the 2023 archive: explicit week, retained-configuration basis.
COMPLETED_LEAGUE_STATUS = "complete"


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Target:
    league_id: str
    season: str
    sport: str
    season_type: str
    best_ball: int
    starting_configuration: tuple[str, ...]
    week_domain: tuple[int, int]
    automatic_week: AutomaticWeek
    mapping_basis: MappingBasis
    basis: TargetBasis = "registered"


def structural_target(
    league_id: str, season: str, starting_configuration: tuple[str, ...], status: object
) -> Target:
    """The target for a league whose live settings already passed the structural rules."""
    completed = status == COMPLETED_LEAGUE_STATUS
    return Target(
        league_id=league_id,
        season=season,
        sport=STRUCTURAL_SPORT,
        season_type=STRUCTURAL_SEASON_TYPE,
        best_ball=STRUCTURAL_BEST_BALL,
        starting_configuration=starting_configuration,
        week_domain=STRUCTURAL_WEEK_DOMAIN,
        automatic_week="explicit_week_only" if completed else "agreeing_same_season_four_signals",
        mapping_basis="retained_selected_league_configuration_observed_view"
        if completed
        else "observed_selected_configuration",
        basis="structural_match",
    )


@dataclass(frozen=True)
class ScopeRegistry:
    extension_id: str | None
    targets: dict[str, Target]
    unsupported_comparisons: frozenset[str]
    slot_rules: dict[str, list[str]]

    def lookup(self, league_id: str) -> Target | Unregistered:
        target = self.targets.get(league_id)
        if target is not None:
            return target
        if league_id in self.unsupported_comparisons:
            return Unregistered("unsupported_comparison")
        return Unregistered("not_registered")


@dataclass(frozen=True)
class Unregistered:
    kind: Literal["not_registered", "unsupported_comparison"]


@dataclass(frozen=True)
class NoRegistry:
    """No registry is configured (structural scope only), or the configured one was refused."""

    reason: Literal["not_configured", "refused"]


def _json_equal(left: Json | object, right: Json | object) -> bool:
    """Equality that does not treat booleans as numbers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    left_obj, right_obj = as_object(left), as_object(right)
    if left_obj is not None or right_obj is not None:
        if left_obj is None or right_obj is None or left_obj.keys() != right_obj.keys():
            return False
        return all(_json_equal(left_obj[key], right_obj[key]) for key in left_obj)
    left_list, right_list = as_list(left), as_list(right)
    if left_list is not None or right_list is not None:
        if left_list is None or right_list is None or len(left_list) != len(right_list):
            return False
        return all(_json_equal(a, b) for a, b in zip(left_list, right_list, strict=True))
    return left == right


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RegistryError(message)


def _parse_target(league_id: str, raw: Json) -> Target:
    entry = as_object(raw)
    _require(entry is not None, "a target is not an object")
    assert entry is not None
    for key in ("season", "sport", "season_type"):
        _require(is_id_string(entry.get(key)), f"a target has an invalid {key}")
    best_ball = entry.get("best_ball")
    _require(is_json_int(best_ball), "a target has a non-integer best_ball")
    configuration = entry.get("starting_configuration")
    _require(
        is_id_string_list(configuration) and bool(configuration),
        "a target has an invalid starting_configuration",
    )
    assert isinstance(configuration, list)
    labels = [str(label) for label in configuration]
    _require("BN" not in labels, "a starting_configuration contains BN")
    _require(all(label in SLOT_RULES for label in labels), "a target uses an unknown slot label")
    domain = as_list(entry.get("week_domain"))
    _require(
        domain is not None
        and len(domain) == 2
        and is_json_int(domain[0])
        and is_json_int(domain[1])
        and 1 <= domain[0] <= domain[1],
        "a target has an invalid week_domain",
    )
    assert domain is not None
    automatic = entry.get("automatic_week")
    _require(
        automatic in ("agreeing_same_season_four_signals", "explicit_week_only"),
        "a target has an unknown automatic_week mode",
    )
    basis = entry.get("mapping_basis", "observed_selected_configuration")
    _require(
        basis
        in (
            "observed_selected_configuration",
            "retained_selected_league_configuration_observed_view",
        ),
        "a target has an unknown mapping_basis",
    )
    return Target(
        league_id=league_id,
        season=str(entry["season"]),
        sport=str(entry["sport"]),
        season_type=str(entry["season_type"]),
        best_ball=int(best_ball),  # pyright: ignore[reportArgumentType]
        starting_configuration=tuple(labels),
        week_domain=(int(domain[0]), int(domain[1])),  # pyright: ignore[reportArgumentType]
        automatic_week=automatic,  # pyright: ignore[reportArgumentType]
        mapping_basis=basis,  # pyright: ignore[reportArgumentType]
    )


def parse_registry(raw: Json, controls: FetchControls = FETCH_CONTROLS) -> ScopeRegistry:
    """Validate a registry document against the policies and controls this code implements."""
    document = as_object(raw)
    _require(document is not None, "the registry is not a JSON object")
    assert document is not None

    bindings = as_object(document.get("existing_policy_bindings"))
    _require(bindings is not None, "existing_policy_bindings is missing")
    assert bindings is not None
    for key, expected in POLICY_BINDINGS.items():
        _require(
            key in bindings and _json_equal(bindings[key], expected),
            f"policy binding {key} differs from the implemented policy",
        )
    for key, expected in OPTIONAL_POLICY_BINDINGS.items():
        _require(
            key not in bindings or _json_equal(bindings[key], expected),
            f"policy binding {key} differs from the implemented policy",
        )
    unknown = set(bindings) - set(POLICY_BINDINGS) - set(OPTIONAL_POLICY_BINDINGS)
    _require(not unknown, "the registry binds policies this code does not implement")

    _require(
        _json_equal(document.get("slot_rules"), SLOT_RULES),
        "slot_rules differ from the implemented rules",
    )
    _require(
        _json_equal(document.get("fetch_controls"), controls.registry_form()),
        "fetch_controls differ from the implemented controls",
    )

    raw_targets = as_object(document.get("targets"))
    _require(raw_targets is not None and bool(raw_targets), "targets is missing or empty")
    assert raw_targets is not None
    targets: dict[str, Target] = {}
    for league_id, entry in raw_targets.items():
        _require(is_id_string(league_id), "a target league ID is not a valid string")
        targets[league_id] = _parse_target(league_id, entry)

    comparisons = document.get("unsupported_comparisons", [])
    _require(is_id_string_list(comparisons), "unsupported_comparisons is not a list of IDs")
    assert isinstance(comparisons, list)
    comparison_ids = frozenset(str(item) for item in comparisons)
    _require(not comparison_ids & set(targets), "a league is both a target and unsupported")

    extension_id = document.get("extension_id")
    return ScopeRegistry(
        extension_id=extension_id if isinstance(extension_id, str) else None,
        targets=targets,
        unsupported_comparisons=comparison_ids,
        slot_rules={label: list(rule) for label, rule in SLOT_RULES.items()},
    )


def load_registry(
    path: Path | None, controls: FetchControls = FETCH_CONTROLS
) -> ScopeRegistry | NoRegistry:
    """Load the registry, or explain (in the log, without identifiers) why none is in use."""
    if path is None:
        logger.info("No scope registry configured; leagues are checked structurally.")
        return NoRegistry("not_configured")
    try:
        registry = parse_registry(strict_json_loads(path.read_bytes()), controls)
    except OSError as exc:
        logger.error("Scope registry refused: cannot read the file (%s).", type(exc).__name__)
        return NoRegistry("refused")
    except JsonParseError as exc:
        logger.error("Scope registry refused: %s.", exc.cause)
        return NoRegistry("refused")
    except RegistryError as exc:
        logger.error("Scope registry refused: %s.", exc)
        return NoRegistry("refused")
    logger.info("Scope registry loaded with %d targets.", len(registry.targets))
    return registry
