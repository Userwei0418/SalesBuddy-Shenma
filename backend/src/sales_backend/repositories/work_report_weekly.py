"""Closed actual-payment reads and rechecks through the caller's RLS connection."""
from types import SimpleNamespace

from sales_backend.contracts.work_report import ActualPaymentLineage, ActualPeriodDependency, OpportunityHistoryGuard, SalesWeekFacts
from sales_backend.domain.work_report import WorkReportError
from sales_backend.domain.work_report_weekly import actual_source_id, end_day, projection_fingerprint

ACTUAL_QUERY = """SELECT e.* FROM crm.effective_actual e
 WHERE e.workspace_id=common.current_workspace_id() AND e.kind='collection'
 AND security.authorization_customer('actual.read',e.customer_id)
 AND security.authorization_customer('work_report.read',e.customer_id)
 AND security.authorization_customer('work_report.write',e.customer_id)
 AND (e.opportunity_id IS NULL OR (
   security.authorization_opportunity('actual.read',e.opportunity_id)
   AND security.authorization_opportunity('work_report.read',e.opportunity_id)
   AND security.authorization_opportunity('work_report.write',e.opportunity_id)))
 AND ($1::text='workspace' OR ($1::text IN ('self','member') AND e.owner_user_ref_id=ANY($2::uuid[]))
      OR ($1::text='team' AND e.owner_team_id=$3::uuid))
 AND ((e.occurred_on IS NOT NULL AND e.occurred_on BETWEEN $4::date AND $5::date)
   OR (e.occurred_on IS NULL AND e.period_start<=$5::date AND e.period_end>=$4::date))
 ORDER BY e.customer_id,e.opportunity_id,e.year,e.quarter,e.id"""


async def read_actuals(connection, scope, period):
    rows = []
    async for row in connection.cursor(ACTUAL_QUERY, scope.kind, [str(i) for i in scope.member_ids],
            str(scope.team_id) if scope.team_id else None, period.start.date(), end_day(period), prefetch=128):
        rows.append(dict(row))
    return rows


def period_dependencies(sales_week_facts):
    if sales_week_facts is None:
        return []
    weekly = SalesWeekFacts.model_validate(sales_week_facts)
    return [SimpleNamespace(source_type='actual_period', record_id=d.opportunity_id or d.customer_id,
                            period_dependency=d) for d in (weekly.finance.dependencies if weekly.finance else [])]


async def assert_actual_access(connection, sources, *, permission='work_report.read', check_versions=True):
    """Actual read plus one supported report action; no arbitrary SQL/identity."""
    if permission not in ('work_report.read', 'work_report.write', 'work_report.share', 'agent.work_report', 'report.export'):
        raise ValueError('unsupported report action')
    facts = []
    for source in sources:
        value = source if isinstance(source, dict) else vars(source)
        if value['source_type'] == 'actual_period':
            line = ActualPeriodDependency.model_validate(value.get('period_dependency'))
            if str(value['record_id']) != str(line.opportunity_id or line.customer_id):
                raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '回款期间引用不一致')
            facts.append(SimpleNamespace(source_id=None, actual_lineage=line))
            continue
        if value['source_type'] != 'actual':
            continue
        line = ActualPaymentLineage.model_validate(value.get('actual_lineage'))
        source_id = value.get('source_id', line.source_ref)
        if str(value['record_id']) != str(line.record_id) or source_id != line.source_ref:
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '回款来源引用不一致')
        facts.append(SimpleNamespace(source_id=source_id, actual_lineage=line))
    if not facts:
        return
    if not await connection.fetchval('SELECT security.authorization_has($1)', 'actual.read'):
        raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '原报告回款来源已不可访问', status=403)
    rows = await connection.fetch("""SELECT e.* FROM crm.effective_actual e
      WHERE e.workspace_id=common.current_workspace_id() AND e.id=ANY($1::text[]) AND e.kind='collection'
      AND security.authorization_customer('actual.read',e.customer_id)
      AND security.authorization_customer($2,e.customer_id)
      AND (e.opportunity_id IS NULL OR (security.authorization_opportunity('actual.read',e.opportunity_id)
        AND security.authorization_opportunity($2,e.opportunity_id)))""",
      sorted({s.actual_lineage.effective_fact_id for s in facts}), permission)
    by_id = {}
    for row in rows:
        by_id.setdefault(str(row['id']), []).append(dict(row))
    for source in facts:
        line = source.actual_lineage
        candidates = [r for r in by_id.get(line.effective_fact_id, [])
            if str(r['customer_id']) == str(line.customer_id)
            and (str(r.get('opportunity_id')) if r.get('opportunity_id') else None) ==
                (str(line.opportunity_id) if line.opportunity_id else None)
            and r['year'] == line.year and r['quarter'] == line.quarter]
        if not candidates:
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '原报告回款来源已不可访问', status=403)
        if check_versions and (len(candidates) != 1 or (source.source_id is not None and actual_source_id(candidates[0]) != source.source_id)
                or projection_fingerprint(candidates[0]) != line.projection_fingerprint):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '回款采用依据或状态已变化，请重新取数')


def history_dependencies(sales_week_facts):
    if sales_week_facts is None:
        return []
    weekly = SalesWeekFacts.model_validate(sales_week_facts)
    if len(weekly.history_guards) != weekly.transition_coverage.candidate_count:
        raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '商机完整历史依赖缺失，请重新取材')
    return [SimpleNamespace(source_type='opportunity_history', record_id=g.opportunity_id, history_guard=g)
            for g in weekly.history_guards]


def private_dependencies(sales_week_facts):
    return [*period_dependencies(sales_week_facts), *history_dependencies(sales_week_facts)]


async def assert_history_access(connection, actor, sources, *, permission='work_report.read'):
    """Revalidate every candidate, even one excluded from either public cohort."""
    from sales_backend.domain.opportunity_history import OpportunityHistoryChanged
    from sales_backend.repositories.opportunity_history import revalidate_first_ge30
    guards = {}
    for source in sources:
        value = source if isinstance(source, dict) else vars(source)
        if value['source_type'] != 'opportunity_history':
            continue
        try:
            guard = OpportunityHistoryGuard.model_validate(value.get('history_guard'))
        except ValueError as error:
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '商机历史引用无效，请重新取材') from error
        if str(value['record_id']) != str(guard.opportunity_id):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '商机历史引用不一致')
        frozen = guard.model_dump(mode='json')
        key = str(guard.opportunity_id)
        if key in guards and guards[key] != frozen:
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '商机历史存在冲突引用')
        guards[key] = frozen
    if not guards:
        return
    if actor is None:
        raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '商机历史必须由当前真实报告身份复验', status=403)
    identity = await connection.fetchrow("SELECT common.current_workspace_id()::text AS workspace_id, "
        "common.current_user_ref_id()::text AS user_id,current_setting('row_security') AS rls")
    if not identity or dict(identity) != dict(workspace_id=actor.workspace_id, user_id=actor.user_id, rls='on'):
        raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '当前报告身份不可用', status=403)
    for guard in guards.values():
        try:
            await revalidate_first_ge30(connection, actor, guard, report_permission=permission)
        except PermissionError as error:
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '原报告商机历史已不可访问', status=403) from error
        except OpportunityHistoryChanged as error:
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '商机或冻结历史已变化，请重新取材') from error
