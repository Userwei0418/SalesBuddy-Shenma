"""Live report selectors and independent action scopes, never frontend authority."""
from uuid import UUID

from sales_backend.contracts.review_scope import ReviewResolvedScopeV2
from sales_backend.contracts.work_report import (
    ReportOptions,
    ResolvedScope,
    ScopeSelection,
    ShareAudience,
    ShareTargets,
    SourceOption,
)
from sales_backend.domain.work_report import WorkReportError, default_scheme
from sales_backend.repositories.authorization import AuthorizationRepository

SOURCE_LABELS = {'visit': '跟进记录', 'opportunity': '商机', 'task': '待办事项', 'risk': '风险', 'actual': '已确认回款'}


async def assert_permissions(connection, actor, *, generate=False, selected_types=()):
    codes = ['work_report.read', 'work_report.write', *(s + '.read' for s in selected_types)]
    if generate:
        codes.append('agent.work_report')
    if any(s not in SOURCE_LABELS for s in selected_types):
        raise WorkReportError('WORK_REPORT_INVALID_SOURCE', '来源类型无效', status=422)
    for code in codes:
        if not await connection.fetchval('SELECT security.authorization_has($1)', code):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '报告功能或所选来源已不可访问', status=403)


async def permission_version(connection, actor):
    snapshot = await AuthorizationRepository().snapshot(connection)
    version = snapshot.get('permission_version')
    if (snapshot.get('workspace_id'), snapshot.get('user_id')) != (actor.workspace_id, actor.user_id) or not isinstance(version, str) or not version:
        raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '无法确认报告权限版本', status=403)
    return version


async def _members(connection, actor, permission, *, write=False, team_id=None):
    rows = await connection.fetch("""SELECT u.id::text,u.display_name AS name
      FROM platform.user_ref u WHERE u.workspace_id=$1::uuid AND u.status='active' AND u.deleted_at IS NULL
      AND security.authorization_subject($2,'person',u.id,NULL)
      AND (NOT $3::boolean OR security.authorization_subject('work_report.write','person',u.id,NULL))
      AND ($4::uuid IS NULL OR EXISTS(SELECT 1 FROM platform.team_membership tm
        WHERE tm.workspace_id=u.workspace_id AND tm.user_ref_id=u.id AND tm.team_id=$4::uuid
        AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to))
      ORDER BY u.display_name,u.id""", actor.workspace_id, permission, write, str(team_id) if team_id else None)
    return [dict(row) for row in rows]


async def _teams(connection, actor, permission, *, write=False, managed_only=False):
    # Generation uses the same live subject grants as the selector. Managers
    # need no artificial team appointment; supervisor/FDE leads still need
    # their active appointment and role scope. Read-only selectors are unchanged.
    rows = await connection.fetch("""SELECT t.id::text,t.name FROM platform.team t
      WHERE t.workspace_id=$1::uuid AND t.status='active' AND t.deleted_at IS NULL
      AND clock_timestamp()>=t.valid_from AND clock_timestamp()<t.valid_to
      AND NOT EXISTS(SELECT 1 FROM platform.team child WHERE child.workspace_id=t.workspace_id
        AND child.parent_team_id=t.id AND child.deleted_at IS NULL)
      AND security.authorization_subject($2,'team',NULL,t.id)
      AND (NOT $3::boolean OR security.authorization_subject('work_report.write','team',NULL,t.id))
      AND (NOT $4::boolean
        OR (common.current_role_code()='manager' AND security.has_active_role('manager'))
        OR EXISTS(SELECT 1 FROM platform.team_membership appointed
        WHERE appointed.workspace_id=t.workspace_id AND appointed.team_id=t.id
          AND appointed.user_ref_id=common.current_user_ref_id()
          AND appointed.membership_role IN ('supervisor','fde_lead')
          AND appointed.membership_role=common.current_role_code()
          AND security.has_active_role(appointed.membership_role)
          AND EXISTS(SELECT 1 FROM security.account_team_scope(common.current_workspace_id(),
            common.current_user_ref_id(),common.current_role_code()) binding
            WHERE binding.team_id=t.id::text)
          AND clock_timestamp()>=appointed.valid_from AND clock_timestamp()<appointed.valid_to))
      ORDER BY t.name,t.id""", actor.workspace_id, permission, write, managed_only)
    return [dict(row) for row in rows]


async def resolve_scope(connection, actor, selection: ScopeSelection) -> ResolvedScope:
    await assert_permissions(connection, actor)
    if selection.kind == 'workspace' or (selection.kind == 'member' and str(selection.member_id) != actor.user_id):
        raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '仅可生成本人或实际管理团队的报告', status=403)
    members = await _members(connection, actor, 'work_report.read', write=True,
                             team_id=selection.team_id if selection.kind == 'team' else None)
    by_id = {row['id']: row for row in members}
    if selection.kind in ('self', 'member'):
        uid = actor.user_id if selection.kind == 'self' else str(selection.member_id)
        if uid not in by_id:
            raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '所选成员已失效或不在报告授权范围', status=403)
        members = [by_id[uid]]
        label = '本人' if selection.kind == 'self' else by_id[uid]['name']
    elif selection.kind == 'team':
        teams = await _teams(connection, actor, 'work_report.read', write=True, managed_only=True)
        selected = next((row for row in teams if row['id'] == str(selection.team_id)), None)
        if selected is None:
            raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '所选团队已失效或不在报告授权范围', status=403)
        label = selected['name']
    else:
        # This selector is explicitly the caller's complete visible set. Source
        # queries still intersect each resource's read and report read/write RLS.
        label = '当前授权范围'
    return ResolvedScope(**selection.model_dump(), label=label,
                         member_ids=sorted([UUID(row['id']) for row in members], key=str))


async def resolve_read_scope(connection, actor, selection):
    """Overview uses the existing report read grants, never generation grants."""
    from sales_backend.contracts.work_report_overview import OverviewResolvedScope, OverviewScopeSelection
    selection = OverviewScopeSelection.model_validate(selection)
    (await AuthorizationRepository().effective(connection)).require('work_report.read')
    members = await _members(connection, actor, 'work_report.read',
                             team_id=selection.team_id if selection.kind == 'team' else None)
    if selection.kind == 'team':
        teams = await _teams(connection, actor, 'work_report.read')
        chosen = next((r for r in teams if r['id'] == str(selection.team_id)), None)
        label = chosen['name'] if chosen else None
    else:
        uid = actor.user_id if selection.kind == 'self' else str(selection.member_id)
        members = [r for r in members if r['id'] == uid]
        label = ('本人' if selection.kind == 'self' else members[0]['name']) if members else None
    if label is None:
        raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '所选范围不在当前报告读取授权内', status=403)
    return OverviewResolvedScope(**selection.model_dump(), label=label,
                                 member_ids=sorted([UUID(r['id']) for r in members], key=str))


async def share_targets(connection, actor) -> ShareTargets:
    effective = await AuthorizationRepository().effective(connection)
    if not effective.allows('work_report.share'):
        return ShareTargets(allowed_kinds=[], members=[], teams=[])
    members = await _members(connection, actor, 'work_report.share')
    teams = await _teams(connection, actor, 'work_report.share')
    kinds = (['members'] if members else []) + (['team'] if teams else [])
    if any(g.scope == 'workspace' for g in effective.for_permission('work_report.share')):
        kinds.append('company')
    return ShareTargets(allowed_kinds=kinds, members=members, teams=teams)


async def assert_generation_scope(connection, actor, frozen):
    if isinstance(frozen, ReviewResolvedScopeV2) or (
        isinstance(frozen, dict) and frozen.get('contract_version') == 'review.scope.v2'
    ):
        from sales_backend.services.review_scope import assert_review_scope
        await assert_review_scope(connection, actor, frozen)
        return
    value = ResolvedScope.model_validate(frozen)
    selection = ScopeSelection(**value.model_dump(include={'kind', 'member_id', 'team_id'}))
    if await resolve_scope(connection, actor, selection) != value:
        raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '生成范围或管理关系已变化，请重新取材', status=403)


async def resolve_review_generation_scope(connection, actor, selection):
    """Resolve a v2 selector and require write access for every subject."""
    from sales_backend.services.review_scope import resolve_review_scope

    resolved = await resolve_review_scope(connection, actor, selection, facts_snapshot=False)
    for subject in resolved.scope.subjects:
        allowed = await connection.fetchval(
            "SELECT security.authorization_subject($1,$2,$3::uuid,$4::uuid)",
            'work_report.write', subject.scope_type,
            str(subject.id) if subject.scope_type == 'person' else None,
            str(subject.id) if subject.scope_type == 'team' else None,
        )
        if not allowed:
            raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '所选范围不具备报告写入授权', status=403)
        if subject.scope_type == 'team':
            managed = await _teams(connection, actor, 'work_report.read', write=True, managed_only=True)
            if str(subject.id) not in {row['id'] for row in managed}:
                raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '所选团队不在当前报告生成范围', status=403)
    return resolved


async def validate_audience(connection, actor, audience: ShareAudience):
    targets = await share_targets(connection, actor)
    if (audience.kind not in targets.allowed_kinds
            or (audience.kind == 'members' and not set(audience.member_ids) <= {m.id for m in targets.members})
            or (audience.kind == 'team' and audience.team_id not in {t.id for t in targets.teams})):
        raise WorkReportError('WORK_REPORT_SHARE_SCOPE_CHANGED', '共享对象已失效或超出当前分享授权', status=403)


async def can_act_on_frozen_subjects(connection, scope, permission) -> bool:
    """Authorize every v2 subject even when it contributed no source record."""
    value = scope.model_dump(mode='json') if hasattr(scope, 'model_dump') else scope
    if value.get('contract_version') != 'review.scope.v2':
        return True
    frozen = ReviewResolvedScopeV2.model_validate(value)
    for subject in frozen.subjects:
        if not await connection.fetchval(
            "SELECT security.authorization_subject($1,$2,$3::uuid,$4::uuid)",
            permission, subject.scope_type,
            str(subject.id) if subject.scope_type == 'person' else None,
            str(subject.id) if subject.scope_type == 'team' else None,
        ):
            return False
    return True


async def can_act_on_sources(connection, actor, sources, scope, permission) -> bool:
    if permission not in {'work_report.write', 'work_report.share', 'agent.work_report', 'report.export'}:
        raise ValueError('unsupported report action')
    if not await connection.fetchval('SELECT security.authorization_has($1)', permission):
        return False
    if not await can_act_on_frozen_subjects(connection, scope, permission):
        return False
    def fields(source):
        return (source['source_type'], source['record_id']) if isinstance(source, dict) else (source.source_type, source.record_id)
    grouped = {}
    actual_sources = []
    history_sources = []
    meeting_sources = []
    review_sources = []
    for source in sources:
        kind, ident = fields(source)
        if kind.startswith('review_'):
            review_sources.append(source if isinstance(source, dict) else vars(source))
            continue
        if kind not in SOURCE_LABELS and kind not in ('actual_period', 'opportunity_history', 'meeting_claim',
                                                     'meeting_state', 'meeting_forecast', 'meeting_member', 'meeting_customer'):
            return False
        if kind.startswith('meeting_'):
            meeting_sources.append(source)
        elif kind in ('actual', 'actual_period'):
            actual_sources.append(source)
        elif kind == 'opportunity_history':
            history_sources.append(source)
        else:
            grouped.setdefault(kind, set()).add(str(ident))
    if actual_sources:
        from sales_backend.repositories.work_report_weekly import assert_actual_access
        try:
            await assert_actual_access(connection, actual_sources, permission=permission, check_versions=True)
        except WorkReportError:
            return False
    if history_sources:
        from sales_backend.repositories.work_report_weekly import assert_history_access
        try:
            await assert_history_access(connection, actor, history_sources, permission=permission)
        except WorkReportError:
            return False
    if meeting_sources:
        from sales_backend.repositories.work_report_meeting_facts import assert_dependencies
        try:
            await assert_dependencies(connection, [s if isinstance(s, dict) else vars(s) for s in meeting_sources], permission=permission)
        except WorkReportError:
            return False
    if review_sources:
        from sales_backend.services.work_report_review_facts import assert_review_dependencies
        try:
            await assert_review_dependencies(connection, actor, review_sources, permission=permission)
        except WorkReportError:
            return False
    for kind, ids in grouped.items():
        # kind is one of the four constant resource names above; IDs are bound.
        allowed = await connection.fetchval(
            f"SELECT count(*) FROM unnest($1::uuid[]) ident WHERE security.authorization_{kind}($2,ident)",
            sorted(ids), permission)
        if allowed != len(ids):
            return False
    if grouped or actual_sources or history_sources or meeting_sources or review_sources:
        return True
    value = scope.model_dump(mode='json') if hasattr(scope, 'model_dump') else scope
    if value.get('contract_version') == 'review.scope.v2':
        subject_type = 'team' if value['kind'] == 'team' else 'person'
        ids = value['subject_ids']
        return all([bool(await connection.fetchval(
            "SELECT security.authorization_subject($1,$2,$3::uuid,$4::uuid)",
            permission, subject_type,
            ident if subject_type == 'person' else None,
            ident if subject_type == 'team' else None,
        )) for ident in ids])
    if value['kind'] == 'workspace':
        effective = await AuthorizationRepository().effective(connection)
        return any(g.scope == 'workspace' for g in effective.for_permission(permission))
    if value['kind'] == 'team':
        return bool(await connection.fetchval("SELECT security.authorization_subject($2,'team',NULL,$1::uuid)", str(value['team_id']), permission))
    return all([bool(await connection.fetchval("SELECT security.authorization_subject($2,'person',$1::uuid,NULL)", str(uid), permission))
                for uid in value['member_ids']]) and bool(value['member_ids'])


async def can_export(connection, actor, sources, scope) -> bool:
    return await can_act_on_sources(connection, actor, sources, scope, 'report.export')

async def report_options(connection, actor) -> ReportOptions:
    from sales_backend.domain.work_report_builtins import builtin_schemes
    effective = await AuthorizationRepository().effective(connection)
    effective.require('work_report.read')
    writable = effective.allows('work_report.write')
    members = await _members(connection, actor, 'work_report.read', write=True) if writable else []
    teams = await _teams(connection, actor, 'work_report.read', write=True, managed_only=True) if writable else []
    kinds = []
    if any(row['id'] == actor.user_id for row in members):
        kinds.append('self')
    # Managers may generate a Review for several authorized people. The
    # snapshot route still resolves the selection and freezes its revision;
    # this directory only advertises the subjects available to choose.
    if len(members) > 1:
        kinds.append('member')
    if teams:
        kinds.append('team')
    role = getattr(actor, 'role', None)
    sales_week = getattr(role, 'value', role) in {'sales', 'supervisor'}
    return ReportOptions(available_scope_kinds=kinds, members=members, teams=teams,
        share_targets=await share_targets(connection, actor),
        sources=[SourceOption(id=key, label=label, available=effective.allows(key + '.read'),
            unavailable_reason=None if effective.allows(key + '.read') else '当前没有该来源的读取授权')
            for key, label in SOURCE_LABELS.items()],
        default_scheme=default_scheme(sales_week=sales_week),
        general_scheme=default_scheme() if sales_week else None,
        builtin_schemes=builtin_schemes(),
        can_create=writable, can_share=effective.allows('work_report.share'),
        can_export=effective.allows('report.export'),
        generation_available=writable and effective.allows('agent.work_report'))
