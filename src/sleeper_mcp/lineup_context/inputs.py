"""Raw tool arguments -> request echo plus normalized request or input issues (A01/A03/A11).

No coercion: booleans, numeric strings and floats are not integers.
"""

from __future__ import annotations

from dataclasses import dataclass

from sleeper_mcp.json_values import Json, is_id_string, is_json_int
from sleeper_mcp.lineup_context.issues import IssueSpec

DEFAULT_SPORT = "nfl"


class Missing:
    """An omitted tool argument. Deliberately not JSON-serializable."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "MISSING"


MISSING = Missing()

type RawArgument = Json | Missing


@dataclass(frozen=True)
class NormalizedRequest:
    user_id: str
    league_id: str
    roster_id: int | None
    sport: str
    sport_defaulted: bool
    season: str | None
    week: int | None


@dataclass
class InputResult:
    requested: dict[str, Json]
    request: NormalizedRequest | None
    issues: list[IssueSpec]


def _invalid(field: str, explanation: str) -> IssueSpec:
    return IssueSpec(
        reason="invalid_input",
        severity="error",
        explanation=explanation,
        affected=[("input", field)],
        next_action=("correct_input", [field], []),
        retryable=False,
        stage="input",
    )


def parse_inputs(arguments: dict[str, RawArgument]) -> InputResult:
    requested: dict[str, Json] = {
        key: value for key, value in arguments.items() if not isinstance(value, Missing)
    }
    issues: list[IssueSpec] = []

    def id_string(field: str) -> str | None:
        value = arguments.get(field, MISSING)
        if isinstance(value, Missing):
            issues.append(_invalid(field, f"{field} is required."))
            return None
        if not is_id_string(value):
            issues.append(
                _invalid(
                    field,
                    f"{field} must be a nonempty string without surrounding whitespace. "
                    "Use the stable Sleeper ID, not a username.",
                )
            )
            return None
        return value

    user_id = id_string("user_id")
    league_id = id_string("league_id")

    sport = DEFAULT_SPORT
    sport_defaulted = True
    raw_sport = arguments.get("sport", MISSING)
    if not isinstance(raw_sport, Missing):
        sport_defaulted = False
        if not isinstance(raw_sport, str) or not raw_sport.strip():
            issues.append(_invalid("sport", 'sport must be a nonempty string such as "nfl".'))
        elif raw_sport != DEFAULT_SPORT:
            issues.append(
                IssueSpec(
                    reason="unsupported_scope",
                    severity="error",
                    explanation='Only sport "nfl" is supported.',
                    affected=[("input", "sport")],
                    next_action=("correct_input", ["sport"], []),
                    retryable=False,
                    stage="input",
                )
            )
        else:
            sport = raw_sport

    season: str | None = None
    raw_season = arguments.get("season", MISSING)
    if not isinstance(raw_season, Missing):
        if is_id_string(raw_season):
            season = raw_season
        else:
            issues.append(_invalid("season", 'season must be a string such as "2026".'))

    roster_id: int | None = None
    raw_roster = arguments.get("roster_id", MISSING)
    if not isinstance(raw_roster, Missing):
        if is_json_int(raw_roster):
            roster_id = raw_roster
        else:
            issues.append(_invalid("roster_id", "roster_id must be a JSON integer."))

    week: int | None = None
    raw_week = arguments.get("week", MISSING)
    if not isinstance(raw_week, Missing):
        if is_json_int(raw_week):
            week = raw_week
        else:
            issues.append(
                _invalid(
                    "week",
                    "week must be a JSON integer; omit it for automatic resolution. "
                    "Strings, booleans, decimals and null are not accepted.",
                )
            )

    request = None
    if not issues and user_id is not None and league_id is not None:
        request = NormalizedRequest(
            user_id=user_id,
            league_id=league_id,
            roster_id=roster_id,
            sport=sport,
            sport_defaulted=sport_defaulted,
            season=season,
            week=week,
        )
    return InputResult(requested=requested, request=request, issues=issues)


def week_domain_issue(week: int, domain: tuple[int, int]) -> IssueSpec | None:
    low, high = domain
    if low <= week <= high:
        return None
    return _invalid("week", f"week must be between {low} and {high} for this league.")
