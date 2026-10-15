"""Meeting orchestration: current generation scope, deterministic facts, private save."""

from sales_backend.contracts.work_report import ScopeSelection
from sales_backend.contracts.work_report_meeting import MeetingOptions, MeetingResolvedScope, MeetingScopeSelection
from sales_backend.domain.work_report import WorkReportError, resolve_period
from sales_backend.domain.work_report_meeting import assemble, schemes
from sales_backend.repositories.work_report_meeting import MeetingRepository
from sales_backend.repositories.work_report_meeting_facts import collect
from sales_backend.services.work_report_access import report_options, resolve_scope

repository = MeetingRepository()


async def resolve(connection, actor, selection):
    original = await resolve_scope(
        connection,
        actor,
        ScopeSelection(kind="team" if selection.kind == "managed_team" else "self", team_id=selection.team_id),
    )
    return MeetingResolvedScope(**selection.model_dump(), label=original.label, member_ids=original.member_ids)


async def options(connection, actor):
    original = await report_options(connection, actor)
    teams = []
    for team in original.teams:
        scope = await resolve(connection, actor, MeetingScopeSelection(kind="managed_team", team_id=team.id))
        teams.append(dict(team_id=team.id, name=team.name, member_ids=scope.member_ids))
    return MeetingOptions(
        available_scope_kinds=(["self"] if "self" in original.available_scope_kinds else [])
        + (["managed_team"] if teams else []),
        teams=teams,
        default_schemes=schemes(),
        can_prepare=original.can_create,
        can_save_draft=original.can_create,
        can_share=original.can_share,
    )


async def prepare(connection, actor, selection):
    scope = await resolve(connection, actor, selection.scope)
    now = await repository.clock(connection)
    period = resolve_period(selection.period, now=now)
    bundle = await collect(connection, actor, scope=scope, period=period)
    facts, dependencies = assemble(bundle, period=period, scope=scope, data_as_of=now)
    return await repository.prepare(connection, actor, selection, facts, dependencies)


async def read(connection, actor, snapshot_id):
    _, snapshot = await repository.snapshot(connection, actor, snapshot_id)
    return snapshot


async def save(connection, actor, body):
    await validate_save(connection, actor, body)
    return await repository.save(connection, actor, body)


async def validate_save(connection, actor, body):
    _, snapshot = await repository.snapshot(connection, actor, body.snapshot_id)
    scope = await resolve(connection, actor, snapshot.selection.scope)
    if scope != snapshot.scope:
        raise WorkReportError("WORK_REPORT_SCOPE_CHANGED", "管理关系或成员已变化，请重新取材", status=403)
    if snapshot.status != "ready" or snapshot.snapshot_hash != body.snapshot_hash:
        raise WorkReportError("WORK_REPORT_SNAPSHOT_CHANGED", "会议快照未就绪或已经变化")
