"""Read-only overview and signed same-material bridge into the existing Worker."""
from datetime import datetime, timedelta

import jwt

from sales_backend.contracts.work_report import ScopeSelection, SnapshotSelection
from sales_backend.contracts.work_report_base import PeriodSelection
from sales_backend.contracts.work_report_meeting import MeetingResolvedScope
from sales_backend.contracts.work_report_overview import (
    OverviewFacts,
    OverviewGenerationFacts,
    OverviewSelection,
    overview_reference,
)
from sales_backend.contracts.work_report_overview_http import OverviewOptions, OverviewResponse
from sales_backend.domain.work_report import SHANGHAI, WorkReportError, canonical_hash, resolve_period
from sales_backend.domain.work_report_meeting import assemble
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.work_report_facts import prepare_facts
from sales_backend.repositories.work_report_meeting_facts import assert_dependencies, collect
from sales_backend.repositories.work_report_task_activity import read_task_activity
from sales_backend.services import work_report_access as access

PURPOSE = 'work-report-overview'
TTL = timedelta(minutes=15)


def actor_hash(actor):
    value = actor.model_dump(mode='json')
    value['team_ids'] = sorted(value.get('team_ids', []))
    return canonical_hash(value)


def profiles(actor):
    return ['general'] + (['sales_week_six'] if actor.role.value in {'sales', 'supervisor'} else [])


async def options(connection, actor):
    effective = await AuthorizationRepository().effective(connection)
    effective.require('work_report.read')
    members = await access._members(connection, actor, 'work_report.read')
    teams = await access._teams(connection, actor, 'work_report.read')
    can_generate = effective.allows('work_report.write') and effective.allows('agent.work_report')
    writable = await access._members(connection, actor, 'work_report.read', write=True) if can_generate else []
    managed = await access._teams(connection, actor, 'work_report.read', write=True, managed_only=True) if can_generate else []
    return OverviewOptions(period_kinds=['day', 'week', 'two_weeks', 'month', 'quarter'],
        available_scope_kinds=(['self'] if any(r['id'] == actor.user_id for r in members) else [])
                              + (['member'] if members else []) + (['team'] if teams else []),
        members=members, teams=teams, generation=dict(
            available_scope_kinds=(['self'] if any(r['id'] == actor.user_id for r in writable) else [])
                                  + (['team'] if managed else []),
            managed_teams=managed, profiles=profiles(actor) if can_generate else []))


async def overview_material(connection, actor, selection, *, now, period=None, allow_partial_sources=False):
    scope = await access.resolve_read_scope(connection, actor, selection.scope)
    period = period or resolve_period(selection.period, now=now)
    internal = MeetingResolvedScope(kind='managed_team' if scope.kind == 'team' else 'self',
        team_id=scope.team_id, label=scope.label, member_ids=scope.member_ids)
    bundle = await collect(connection, actor, scope=internal, period=period, read_only=True,
                           allow_partial_sources=allow_partial_sources)
    meeting, dependencies = assemble(bundle, period=period, scope=internal, data_as_of=now)
    facts = OverviewFacts.model_validate({**meeting.model_dump(mode='json'),
        'contract_version': 'work_report.overview_facts.v1', 'scope': scope.model_dump(mode='json')})
    return facts, dependencies


def snapshot_selection(selection):
    # An explicitly selected own member is canonicalized to the existing self
    # generation scope only after the caller's scope eligibility is checked.
    scope = ScopeSelection(kind='team', team_id=selection.scope.team_id) if selection.scope.kind == 'team' else ScopeSelection()
    weekly = selection.generation_profile == 'sales_week_six'
    return SnapshotSelection(period=selection.period, scope=scope,
        sources=['visit', 'opportunity', 'task'] + (['actual'] if weekly else []),
        facts_contract='work_report.sales_week_facts.v1' if weekly else None)


def material_selection(selection):
    return selection.model_dump(mode='json', exclude={'overview_binding', 'supplements'})


async def generation_material(connection, actor, template, facts, dependencies):
    await access.assert_permissions(connection, actor, generate=True, selected_types=template.sources)
    scope = await access.resolve_scope(connection, actor, template.scope)
    if (scope.member_ids != facts.scope.member_ids or scope.team_id != facts.scope.team_id):
        raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '浏览范围与可生成范围不同', status=403)
    for permission in ('work_report.write', 'agent.work_report'):
        if not await access.can_act_on_sources(connection, actor, dependencies, scope, permission):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '部分概览材料不可用于生成', status=403)
    prepared = await prepare_facts(connection, template, scope, period=facts.period, data_as_of=facts.data_as_of,
                                   **({'actor': actor} if template.facts_contract else {}))
    return scope, prepared


async def read(connection, actor, selection, request_id, settings):
    now = await connection.fetchval('SELECT transaction_timestamp()')
    selection = selection.model_copy(update={'period': PeriodSelection(**{
        **selection.period.model_dump(), 'anchor_date': selection.period.anchor_date or now.astimezone(SHANGHAI).date()})})
    facts, dependencies = await overview_material(connection, actor, selection, now=now)
    task_activity = await read_task_activity(connection, actor, period=facts.period, scope=facts.scope, data_as_of=now)
    reason, binding = None, None
    effective = await AuthorizationRepository().effective(connection)
    if not effective.allows('work_report.write') or not effective.allows('agent.work_report'):
        reason = 'READ_ONLY'
    elif selection.scope.kind == 'member' and str(selection.scope.member_id) != actor.user_id:
        reason = 'SCOPE_NOT_GENERATABLE'
    elif selection.generation_profile not in profiles(actor):
        reason = 'PROFILE_UNAVAILABLE'
    else:
        template = snapshot_selection(selection)
        try:
            scope, prepared = await generation_material(connection, actor, template, facts, dependencies)
        except WorkReportError as exc:
            if exc.status != 403:
                raise
            reason = 'SCOPE_NOT_GENERATABLE' if exc.code == 'WORK_REPORT_SCOPE_CHANGED' else 'SOURCE_NOT_GENERATABLE'
        else:
            # The document trigger must understand frozen overview context; do
            # not generate an apparently complete report on the old schema.
            if not await connection.fetchval("SELECT EXISTS(SELECT 1 FROM ops.schema_migration WHERE version='V236')"):
                raise WorkReportError('WORK_REPORT_INVALID_INPUT', '统一概览冻结数据库版本尚未就绪', status=409)
            version = await access.permission_version(connection, actor)
            expiry = now + TTL
            claims = dict(iss=settings.access_token_issuer, aud=settings.access_token_audience + ':' + PURPOSE,
                purpose=PURPOSE, actor=actor_hash(actor), iat=now, nbf=now, exp=expiry,
                selection=selection.model_dump(mode='json'), selection_hash=canonical_hash(selection),
                scope_hash=canonical_hash(scope), period=facts.period.model_dump(mode='json'),
                data_as_of=facts.data_as_of.isoformat(), overview_hash=canonical_hash(facts),
                materials_hash=canonical_hash(prepared), dependencies_hash=canonical_hash(dependencies),
                permission_version=version, template_hash=canonical_hash(material_selection(template)))
            binding = dict(token=jwt.encode(claims, settings.access_token_secret, algorithm='HS256'),
                           expires_at=expiry, snapshot_selection=template)
    return OverviewResponse(request_id=request_id, selection=selection, selection_hash=canonical_hash(selection),
        data_as_of=now, period=facts.period, scope=facts.scope, facts=facts, overview_hash=canonical_hash(facts),
        capabilities=dict(can_generate=binding is not None, generation_unavailable_reason=reason),
        generation_binding=binding, task_activity=task_activity)


def stale():
    return WorkReportError('WORK_REPORT_OVERVIEW_STALE', '概览已过期或材料已变化，请刷新后重新生成', status=409)


def decode_binding(settings, actor, body):
    try:
        claims = jwt.decode(body.overview_binding, settings.access_token_secret, algorithms=['HS256'],
            issuer=settings.access_token_issuer, audience=settings.access_token_audience + ':' + PURPOSE,
            options={'require': ['exp', 'iat', 'nbf', 'purpose', 'actor', 'selection', 'selection_hash', 'scope_hash',
                'period', 'data_as_of', 'overview_hash', 'materials_hash', 'dependencies_hash', 'permission_version', 'template_hash']})
        selection = OverviewSelection.model_validate(claims['selection'])
        if (claims['purpose'] != PURPOSE or claims['actor'] != actor_hash(actor)
                or claims['selection_hash'] != canonical_hash(selection)
                or claims['template_hash'] != canonical_hash(material_selection(body))
                or claims['template_hash'] != canonical_hash(material_selection(snapshot_selection(selection)))
                or claims['exp'] - claims['iat'] > TTL.total_seconds()):
            raise ValueError('binding identity mismatch')
        return claims, selection
    except jwt.ExpiredSignatureError:
        raise stale() from None
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise WorkReportError('WORK_REPORT_OVERVIEW_BINDING_INVALID', '概览绑定无效或选择已变化', status=422) from None


async def validate_request(connection, actor, body, settings):
    claims, selection = decode_binding(settings, actor, body)
    scope = await access.resolve_scope(connection, actor, body.scope)
    if (claims['scope_hash'] != canonical_hash(scope)
            or claims['permission_version'] != await access.permission_version(connection, actor)):
        raise stale()
    now = datetime.fromisoformat(claims['data_as_of'])
    period = resolve_period(selection.period, now=now)
    if period.model_dump(mode='json') != claims['period']:
        raise stale()
    return claims, selection, period


async def prepare_bound(connection, actor, body, settings, *, scope, period):
    claims, selection, original_period = await validate_request(connection, actor, body, settings)
    if original_period != period or canonical_hash(scope) != claims['scope_hash']:
        raise stale()
    now = datetime.fromisoformat(claims['data_as_of'])
    facts, dependencies = await overview_material(connection, actor, selection, now=now, period=period)
    _, prepared = await generation_material(connection, actor, snapshot_selection(selection), facts, dependencies)
    if (canonical_hash(facts) != claims['overview_hash'] or canonical_hash(prepared) != claims['materials_hash']
            or canonical_hash(dependencies) != claims['dependencies_hash']):
        raise stale()
    await assert_dependencies(connection, dependencies)
    prepared.overview_facts = OverviewGenerationFacts(facts=facts,
        references={s.source_id: overview_reference(s) for s in facts.sources})
    prepared.overview_dependencies = dependencies
    return prepared
