"""Orchestration for one `get_lineup_context` call: sequencing, pinning, assembly, finalize()."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import anyio

from sleeper_mcp.contract import (
    ASSOCIATION_POLICY,
    FETCH_CONTROLS,
    POLICY_DESCRIPTIONS,
    SLOT_RULES,
    FetchControls,
)
from sleeper_mcp.json_values import Json, JsonObject, as_list, as_object, is_id_string
from sleeper_mcp.lineup_context import week as week_policy
from sleeper_mcp.lineup_context import weekly as weekly_policy
from sleeper_mcp.lineup_context.association import evaluate_association
from sleeper_mcp.lineup_context.eligibility import PROFILE_CATALOG, PROFILE_FIELDS, build_details
from sleeper_mcp.lineup_context.freshness import evaluate_freshness
from sleeper_mcp.lineup_context.inputs import (
    NormalizedRequest,
    RawArgument,
    parse_inputs,
    week_domain_issue,
)
from sleeper_mcp.lineup_context.issues import IssueSpec, build_issues
from sleeper_mcp.lineup_context.outcome import combine
from sleeper_mcp.lineup_context.placement import evaluate_placement
from sleeper_mcp.lineup_context.raw import observation, raw_field
from sleeper_mcp.lineup_context.references import reference_errors
from sleeper_mcp.lineup_context.schema import (
    SCHEMA_VERSION,
    AssociationEvidence,
    Context,
    Definitions,
    Enumeration,
    LeagueCoverage,
    LeagueData,
    LeagueEvidence,
    LeagueScope,
    LeagueSection,
    LineupContextV1,
    PlayerDetailsScope,
    PlayerDetailsSection,
    PlayerRole,
    PolicyDefinition,
    ResolutionEvidence,
    Resolved,
    RosterCoverage,
    RosterScope,
    RosterSection,
    Source,
    SourceAttempt,
    SourceFailure,
    StartingSlot,
    UserEvidence,
    WeekEvidence,
    WeeklyScope,
    WeeklySection,
)
from sleeper_mcp.lineup_context.settings import RULE_FIELDS, SettingsResult, evaluate_settings
from sleeper_mcp.lineup_context.slots import VACANCY
from sleeper_mcp.lineup_context.target import (
    LEAGUE_FIELDS,
    base_scope,
    defaults_for,
    evaluate_league,
    evaluate_user,
    source_failure_issue,
)
from sleeper_mcp.lineup_context.views import compare_views
from sleeper_mcp.scope import NoRegistry, ScopeRegistry, Target, Unregistered
from sleeper_mcp.sleeper import routes
from sleeper_mcp.sleeper.directory import DirectoryResult, PlayerDirectoryStore
from sleeper_mcp.sleeper.fetch import CallBudget, Clock, Fetcher
from sleeper_mcp.sleeper.observation import SourceObservation, utc_iso

logger = logging.getLogger(__name__)

ROSTER_ROW_FIELDS = [
    "roster_id",
    "league_id",
    "owner_id",
    "co_owners",
    "players",
    "starters",
    "reserve",
    "taxi",
]
FIELD_CATALOG = {
    "user": ["user_id"],
    "league": LEAGUE_FIELDS,
    "league_rules": [f"settings.{key}" for key in RULE_FIELDS],
    "roster_row": ROSTER_ROW_FIELDS,
    "weekly_row": weekly_policy.WEEKLY_ROW_FIELDS,
    PROFILE_CATALOG: PROFILE_FIELDS,
}


class FinalizeError(Exception):
    """A result failed its own schema or reference checks: an internal bug, never user data."""


@dataclass
class Runtime:
    registry: ScopeRegistry | NoRegistry
    fetcher: Fetcher
    directory: PlayerDirectoryStore
    clock: Clock
    controls: FetchControls = FETCH_CONTROLS


@dataclass
class _Call:
    requested: dict[str, Json]
    issues: list[IssueSpec] = field(default_factory=lambda: [])
    sources: list[SourceObservation] = field(default_factory=lambda: [])
    user: UserEvidence | None = None
    league: LeagueEvidence | None = None
    association: AssociationEvidence | None = None
    week: WeekEvidence | None = None
    scope: LeagueScope | None = None
    target: Target | None = None
    request: NormalizedRequest | None = None
    resolved: dict[str, Json] = field(default_factory=lambda: {})


def definitions() -> Definitions:
    return Definitions(
        policies={
            policy: PolicyDefinition(assumption=assumption, description=description)
            for policy, (assumption, description) in POLICY_DESCRIPTIONS.items()
        },
        slot_rules={label: list(rule) for label, rule in SLOT_RULES.items()},
        field_catalog={key: list(value) for key, value in FIELD_CATALOG.items()},
    )


def _source_model(obs: SourceObservation) -> Source:
    return Source(
        id=obs.id,
        route=obs.route.value,
        url=obs.url,
        coordinate=dict(obs.coordinate),
        origin=obs.origin,
        attempts=[
            SourceAttempt(
                index=a.index,
                started_at=utc_iso(a.started_at),
                ended_at=utc_iso(a.ended_at),
                outcome=a.outcome,
                http_status=a.http_status,
            )
            for a in obs.attempts
        ],
        request_started_at=utc_iso(obs.request_started_at) if obs.request_started_at else None,
        body_completed_at=utc_iso(obs.body_completed_at) if obs.body_completed_at else None,
        http_status=obs.http_status,
        headers=dict(obs.headers) if obs.headers is not None else None,
        body_sha256=obs.body_sha256,
        body_bytes=obs.body_bytes,
        parse=obs.parse,
        failure=SourceFailure(cause=obs.failure.cause, detail=obs.failure.detail)
        if obs.failure
        else None,
    )


def _empty_association(roster_id: int | None) -> AssociationEvidence:
    return AssociationEvidence(
        policy_id=ASSOCIATION_POLICY,
        status="not_attempted",
        selection_basis=None,
        requested_roster_id=roster_id,
        selected=None,
        candidates=[],
        enumeration=Enumeration(
            coverage="unavailable", row_count=None, total_rosters=None, structural_defects=[]
        ),
        uniqueness="not_applicable",
        row_evidence=[],
        source_refs=[],
    )


def _assemble(call: _Call, context: Context | None, selection_required: bool) -> LineupContextV1:
    request = call.request
    scope = call.scope or base_scope(None, None, registry=False)
    outcome = combine(call.issues, selection_required)
    resolved_keys = ("user_id", "league_id", "roster_id", "sport", "season", "season_type", "week")
    resolved = {key: call.resolved.get(key) for key in resolved_keys}
    if outcome in ("selection_required", "error"):
        resolved["roster_id"] = None if outcome == "selection_required" else resolved["roster_id"]
    league = call.league or LeagueEvidence(
        status="not_attempted",
        scope=scope,
        defaults=defaults_for(request) if request else [],
        observed=None,
        data=None,
        source_refs=[],
    )
    return LineupContextV1(
        schema_version=SCHEMA_VERSION,
        outcome=outcome,
        requested=call.requested,
        resolved=Resolved.model_validate(resolved),
        resolution_evidence=ResolutionEvidence(
            user=call.user
            or UserEvidence(status="not_attempted", observed=None, data=None, source_refs=[]),
            league=league,
            association=call.association
            or _empty_association(request.roster_id if request else None),
            week=call.week or week_policy.not_attempted(call.target),
        ),
        context=context if outcome in ("complete", "partial") else None,
        issues=build_issues(call.issues),
        sources=[_source_model(obs) for obs in call.sources],
        definitions=definitions(),
    )


def finalize(result: LineupContextV1) -> LineupContextV1:
    """Validate every branch (the SDK skips error results) and check internal references."""
    try:
        validated = LineupContextV1.model_validate(result.model_dump(mode="json"))
    except ValueError as exc:
        raise FinalizeError(f"result failed v1 validation: {exc}") from exc
    errors = reference_errors(validated)
    if errors:
        raise FinalizeError("result has dangling references: " + "; ".join(errors[:5]))
    return validated


async def get_lineup_context(
    runtime: Runtime, arguments: dict[str, RawArgument]
) -> LineupContextV1:
    return finalize(await _run(runtime, arguments))


async def _run(runtime: Runtime, arguments: dict[str, RawArgument]) -> LineupContextV1:
    parsed = parse_inputs(arguments)
    call = _Call(requested=parsed.requested, issues=list(parsed.issues))
    request = parsed.request
    call.request = request
    if request is None:
        return _assemble(call, None, False)

    # 1. Scope, without any network request.
    registry = runtime.registry
    if isinstance(registry, NoRegistry):
        call.scope = base_scope(None, None, registry=False)
        call.issues.append(
            IssueSpec(
                reason="unsupported_scope",
                severity="error",
                explanation="No scope registry is configured (or the configured one was "
                "refused), so no league is supported. Set SLEEPER_MCP_SCOPE_REGISTRY.",
                affected=[("input", "league_id")],
                cause="registry_absent",
                next_action=None,
                retryable=False,
                stage="input",
            )
        )
        return _assemble(call, None, False)
    found = registry.lookup(request.league_id)
    if isinstance(found, Unregistered):
        call.scope = base_scope(None, registry.extension_id, registry=True).model_copy(
            update={"target": found.kind}
        )
        call.issues.append(
            IssueSpec(
                reason="unsupported_scope",
                severity="error",
                explanation="This league is not one of the registered supported "
                "league/season/configuration instances.",
                affected=[("input", "league_id")],
                cause="league_not_registered",
                next_action=None,
                retryable=False,
                stage="input",
            )
        )
        return _assemble(call, None, False)
    target = found
    call.target = target
    call.scope = base_scope(target, registry.extension_id, registry=True)
    if request.week is not None:
        problem = week_domain_issue(request.week, target.week_domain)
        if problem is not None:
            call.issues.append(problem)
            return _assemble(call, None, False)

    fetcher = runtime.fetcher
    budget = CallBudget(runtime.clock, runtime.controls)

    # 2. User and league in parallel.
    fetched: dict[str, SourceObservation] = {}

    async def get(name: str, route: routes.RouteRequest) -> None:
        fetched[name] = await fetcher.fetch(route, budget, f"src-{name}")

    async with anyio.create_task_group() as group:
        group.start_soon(get, "user", routes.user(request.user_id))
        group.start_soon(get, "league", routes.league(request.league_id))
    call.sources += [fetched["user"], fetched["league"]]

    user_evidence, user_issues = evaluate_user(fetched["user"], request.user_id)
    call.user = user_evidence
    call.issues += user_issues
    if user_evidence.status == "resolved":
        call.resolved["user_id"] = request.user_id
    league_result = evaluate_league(fetched["league"], request, target, call.scope)
    call.league = league_result.evidence
    call.issues += league_result.issues
    league = league_result.league
    if league is not None:
        call.resolved.update(
            league_id=request.league_id,
            sport=league["sport"],
            season=league["season"],
            season_type=league["season_type"],
        )
    if user_evidence.status != "resolved" or league is None:
        return _assemble(call, None, False)

    # 3. Rosters, plus NFL state when the week is automatic.
    explicit = request.week
    use_state = week_policy.needs_state(target, explicit)
    async with anyio.create_task_group() as group:
        group.start_soon(get, "rosters", routes.rosters(request.league_id))
        if use_state:
            group.start_soon(get, "state", routes.state_nfl())
    rosters_obs = fetched["rosters"]
    call.sources.append(rosters_obs)
    state_obs = fetched.get("state")
    if state_obs is not None:
        call.sources.append(state_obs)

    week_result = (
        week_policy.explicit_week(explicit, target)
        if explicit is not None
        else week_policy.automatic_week(target, league, fetched["league"].id, state_obs)
    )
    call.week = week_result.evidence
    pinned_week = week_result.week

    if not rosters_obs.ok:
        call.issues.append(
            source_failure_issue(
                rosters_obs,
                "rosters",
                "The roster list could not be retrieved; no empty roster or older data is "
                "substituted.",
            )
        )
        return _assemble(call, None, False)

    association = evaluate_association(
        rosters_obs.body,
        rosters_obs.id,
        request.league_id,
        request.user_id,
        league.get("total_rosters"),
        "total_rosters" in league,
        request.roster_id,
    )
    call.association = association.evidence
    call.issues += association.issues
    if association.status != "selected" or association.selected_row is None:
        call.week = week_result.evidence
        if pinned_week is not None:
            call.resolved["week"] = pinned_week
        return _assemble(call, None, association.status == "selection_required")

    roster_id = association.evidence.selected.roster_id  # pyright: ignore[reportOptionalMemberAccess]
    call.resolved["roster_id"] = roster_id
    call.resolved["week"] = pinned_week
    call.issues += week_result.issues
    row = association.selected_row

    configuration = (
        list(target.starting_configuration)
        if league_result.configuration_status == "mapped"
        else None
    )
    season = str(league["season"])

    # 4/5. Weekly lineup for the pinned week, and the player directory, in parallel.
    directory_holder: dict[str, DirectoryResult] = {}

    async def get_directory() -> None:
        directory_holder["result"] = await runtime.directory.get(budget, "src-players")

    async with anyio.create_task_group() as group:
        if pinned_week is not None:
            group.start_soon(get, "matchups", routes.matchups(request.league_id, pinned_week))
        group.start_soon(get_directory)

    directory = directory_holder["result"]

    if pinned_week is not None:
        matchups_obs = fetched["matchups"]
        call.sources.append(matchups_obs)
        weekly = weekly_policy.evaluate_weekly(
            matchups_obs,
            request.league_id,
            roster_id,
            pinned_week,
            configuration,
            target.mapping_basis,
        )
        weekly_ref: str | None = matchups_obs.id
    else:
        weekly = weekly_policy.not_attempted(target.mapping_basis)
        weekly_ref = None
    if directory.observation is not None:
        call.sources.append(directory.observation)

    # 6. Compose.
    placement = evaluate_placement(row, configuration, str(roster_id), rosters_obs.id)
    settings = evaluate_settings(league, fetched["league"].id)
    call.issues += placement.issues + weekly.issues + settings.issues

    roles: dict[str, list[PlayerRole]] = {}

    def add_role(pid: Json, role: PlayerRole) -> None:
        if is_id_string(pid) and pid != VACANCY:
            roles.setdefault(pid, [])
            if role not in roles[pid]:
                roles[pid].append(role)

    for pid in placement.data.members or []:
        add_role(pid, "current_member")
    for pid in placement.data.candidate_member_ids:
        add_role(pid, "raw_only_fragment")
    for key, role in (
        ("starters", "current_starter"),
        ("reserve", "current_reserve"),
        ("taxi", "current_taxi"),
    ):
        for pid in as_list(row.get(key)) or []:
            add_role(pid, role)  # pyright: ignore[reportArgumentType]
    occupied: list[str] = []
    if weekly.data.slots is not None:
        occupied = [s.player_id for s in weekly.data.slots if s.player_id is not None]
        for pid in occupied:
            add_role(pid, "weekly_starter")
    elif weekly.row is not None:
        for pid in as_list(weekly.row.get("starters")) or []:
            add_role(pid, "weekly_starter")
    if weekly.row is not None:
        for pid in as_list(weekly.row.get("players")) or []:
            add_role(pid, "weekly_member")

    applicability = (
        "current_observation" if call.week.basis == "provider_observed_week" else "unknown"
    )
    details = build_details(
        roles,
        directory.profiles,
        applicability,
        occupied,
        directory.observation.id if directory.observation else None,
        directory.status == "unavailable" and directory.observation is not None,
        directory.cause,
    )
    call.issues += details.issues

    views = compare_views(
        rosters_obs.id,
        weekly_ref if weekly.data.slots is not None else None,
        placement.data.members,
        placement.membership_complete,
        placement.data.placements,
        row.get("starters"),
        weekly.data.slots,
        weekly.row.get("starters") if weekly.row is not None else None,
    )
    call.issues += views.issues

    freshness = evaluate_freshness(call.sources, runtime.clock.now(), directory.reuse_age_seconds)
    call.issues += freshness.issues

    context = Context(
        league=_league_section(
            league, fetched["league"], target, league_result.configuration_status, settings
        ),
        roster=RosterSection(
            coverage=RosterCoverage(
                membership=placement.membership_coverage,  # pyright: ignore[reportArgumentType]
                placement=placement.placement_coverage,  # pyright: ignore[reportArgumentType]
            ),
            observed=[
                observation(
                    rosters_obs.id, f"body[roster_id={roster_id}]", row, ROSTER_ROW_FIELDS, row
                )
            ],
            data=placement.data,
            scope=RosterScope(
                league_id=request.league_id,
                roster_id=roster_id,
                season=season,
                view="latest_retrieved_selected_league_roster",
                original_week_membership="not_established",
            ),
            source_refs=[rosters_obs.id],
        ),
        weekly_lineup=WeeklySection(
            coverage=weekly.coverage,
            observed=weekly.observed,
            data=weekly.data,
            scope=WeeklyScope(
                league_id=request.league_id, roster_id=roster_id, season=season, week=pinned_week
            ),
            source_refs=[weekly_ref] if weekly_ref else [],
        ),
        player_details=PlayerDetailsSection(
            coverage=details.coverage,
            data=details.entries,
            scope=PlayerDetailsScope(
                directory_source_ref=directory.observation.id if directory.observation else None,
                directory_origin=directory.observation.origin if directory.observation else None,
                applicability_basis=(
                    "Profiles are the current directory observation; they apply to the target "
                    "week only when the week was selected automatically as the provider-observed "
                    "current week. An explicit week stays unknown, even when it equals the "
                    "current week, because explicit weeks do not read NFL state."
                ),
                historical_eligibility="not_established",
            ),
            source_refs=[directory.observation.id] if directory.observation else [],
        ),
        view_comparison=views.comparison,
        freshness=freshness.section,
    )
    return _assemble(call, context, False)


def _league_section(
    league: JsonObject,
    obs: SourceObservation,
    target: Target,
    configuration_status: str,
    settings: SettingsResult,
) -> LeagueSection:
    positions = league.get("roster_positions")
    mapped = configuration_status == "mapped"
    starting = (
        [
            StartingSlot(index=i, label=label)
            for i, label in enumerate(target.starting_configuration)
        ]
        if mapped
        else None
    )
    scoring_coverage = {
        "complete": "complete",
        "omitted": "unavailable",
        "null": "unavailable",
        "empty_object": "unknown",
        "invalid": "partial",
    }[settings.scoring_state]
    raw_settings = as_object(league.get("settings"))
    return LeagueSection(
        coverage=LeagueCoverage(
            configuration="complete" if mapped else "unavailable",
            scoring=scoring_coverage,  # pyright: ignore[reportArgumentType]
        ),
        observed=[
            observation(obs.id, "body", obs.body, LEAGUE_FIELDS, league).model_copy(
                update={
                    "fields": [
                        *[
                            raw_field(league, key)
                            for key in LEAGUE_FIELDS
                            if key != "roster_positions"
                        ],
                        raw_field(league, "settings"),
                        raw_field(league, "scoring_settings"),
                    ]
                }
            )
        ],
        data=LeagueData(
            league_id=str(league["league_id"]),
            sport=str(league["sport"]),
            season=str(league["season"]),
            season_type=str(league["season_type"]),
            status=league.get("status"),
            roster_positions=positions,
            roster_positions_state=raw_field(league, "roster_positions").state,
            starting_slots=starting,
            slot_rule_labels=list(dict.fromkeys(target.starting_configuration)) if mapped else None,
            mapping_basis=target.mapping_basis if mapped else None,
            original_week_configuration="not_established",
            settings=raw_settings if raw_settings is not None else league.get("settings"),
            scoring_settings=league.get("scoring_settings"),
            scoring_state=settings.scoring_state,
            rule_fields=settings.rule_fields,
        ),
        source_refs=[obs.id],
    )
