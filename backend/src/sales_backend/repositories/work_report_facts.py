# ruff: noqa: S608 -- Only closed server-owned source descriptors form SQL; values are bound.
"""Report material reads through the caller's actual RLS transaction.

The cursor prefetch is a transport batch size, never a semantic row limit.
No alternate connection, impersonation, permission cache or definer read is used.
"""
from __future__ import annotations

from uuid import UUID

from sales_backend.contracts.work_report import ResolvedScope, SnapshotSelection
from sales_backend.contracts.review_scope import ReviewResolvedScopeV2
from sales_backend.domain.work_report import WorkReportError
from sales_backend.domain.work_report_facts import FactAssembler
from sales_backend.repositories.opportunity_business_date import creation_date_projection


DESCRIPTORS = {
    'visit': dict(table='activity.visit', alias='v', owner='recorder_user_ref_id', team='recorder_team_id',
                  participant_table='activity.visit_participant', participant_fk='visit_id',
                  participant_user='user_ref_id', participant_team='team_id_at_event',
                  participant_active='true',
                  fields="v.interaction_at,v.follow_up_record,'跟进记录'::text AS title",
                  predicate="v.status IN ('confirmed','archived')"),
    'opportunity': dict(table='crm.opportunity', alias='o', owner='owner_user_ref_id', team='owner_team_id',
                  participant_table='crm.opportunity_participant', participant_fk='opportunity_id',
                  participant_user='user_ref_id', participant_team=None,
                  participant_active="clock_timestamp()>=p.valid_from AND clock_timestamp()<p.valid_to AND security.fde_user_is_active(p.user_ref_id)",
                  fields='o.name,o.stage_code,o.status,o.summary,o.amount,o.currency,' + creation_date_projection() + ' AS creation_date',
                  predicate='true'),
    'task': dict(table='workflow.task', alias='t', owner=None, team=None,
                  participant_table='workflow.task_assignee', participant_fk='task_id',
                  participant_user='assignee_user_ref_id', participant_team='assignee_team_id',
                  participant_active="p.responsibility='owner'",
                  fields='t.title,t.status,t.description,t.completion_note,t.completed_at',
                  predicate="t.status='completed'"),
    'risk': dict(table='insight.risk', alias='r', owner='owner_user_ref_id', team='owner_team_id',
                  participant_table=None, fields='r.title,r.status,r.description,r.opened_at,r.resolved_at,r.resolution_note',
                  predicate='true'),
}


def source_query(kind, *, sales_week=False):
    d = dict(DESCRIPTORS[kind])
    if sales_week:
        if kind == 'visit':
            d['fields'] += """,v.opportunity_id,v.next_action,
              ARRAY(SELECT linked.id::text FROM (
                  SELECT v.opportunity_id AS id
                  UNION
                  SELECT vo.opportunity_id FROM activity.visit_opportunity vo
                    WHERE vo.workspace_id=v.workspace_id AND vo.visit_id=v.id
                ) candidate JOIN crm.opportunity linked ON linked.id=candidate.id
                WHERE linked.workspace_id=v.workspace_id AND linked.deleted_at IS NULL
                  AND security.authorization_opportunity('opportunity.read',linked.id)
                  AND security.authorization_opportunity('work_report.read',linked.id)
                ORDER BY linked.id) AS linked_opportunity_ids"""
        elif kind == 'opportunity':
            d['fields'] += ',o.probability,o.follow_up_plan'
        elif kind == 'task':
            d['fields'] += ',t.due_at,t.opportunity_id'
            d['predicate'] = "t.status IN ('completed','pending_confirm','pending_execution','in_progress','deferred','pending_review')"
    a = d['alias']
    member = f'{a}.{d["owner"]}=ANY($2::uuid[])' if d['owner'] else 'false'
    team = f'{a}.{d["team"]}=$3::uuid' if d['team'] else 'false'
    people = []
    if d['owner']:
        people.append(f'SELECT {a}.{d["owner"]} AS user_id')
    if d.get('participant_table'):
        link = (f"FROM {d['participant_table']} p WHERE p.{d['participant_fk']}={a}.id "
                f"AND p.workspace_id={a}.workspace_id AND ({d['participant_active']})")
        people.append(f"SELECT p.{d['participant_user']} AS user_id " + link)
        member += f" OR EXISTS(SELECT 1 {link} AND p.{d['participant_user']}=ANY($2::uuid[]))"
        if d.get('participant_team'):
            team += f" OR EXISTS(SELECT 1 {link} AND p.{d['participant_team']}=$3::uuid)"
        else:
            team += f""" OR EXISTS(SELECT 1 {link} AND EXISTS(
                SELECT 1 FROM platform.team_membership tm JOIN platform.team pt
                  ON pt.id=tm.team_id AND pt.workspace_id=tm.workspace_id
                WHERE tm.user_ref_id=p.{d['participant_user']} AND tm.workspace_id=p.workspace_id
                  AND tm.team_id=$3::uuid AND pt.status='active' AND pt.deleted_at IS NULL
                  AND clock_timestamp()>=tm.valid_from AND clock_timestamp()<tm.valid_to
                  AND clock_timestamp()>=pt.valid_from AND clock_timestamp()<pt.valid_to))"""
    contributors = " UNION ".join(people)
    return f"""SELECT {a}.id,{a}.version_no,{d['fields']},
      COALESCE((SELECT jsonb_agg(jsonb_build_object('user_id',u.id,'name',u.display_name) ORDER BY u.id)
        FROM ({contributors}) person JOIN platform.user_ref u ON u.id=person.user_id
        WHERE u.workspace_id={a}.workspace_id),'[]'::jsonb) AS contributors
      FROM {d['table']} {a}
      WHERE {a}.workspace_id=common.current_workspace_id() AND {a}.deleted_at IS NULL
        AND ({d['predicate']})
        AND security.authorization_{kind}('{kind}.read',{a}.id)
        AND security.authorization_{kind}('work_report.read',{a}.id)
        AND security.authorization_{kind}('work_report.write',{a}.id)
        AND ($1::text='workspace' OR ($1::text IN ('self','member') AND ({member}))
             OR ($1::text='team' AND ({team})))
      ORDER BY {a}.id"""


async def prepare_facts(connection, selection: SnapshotSelection, scope: ResolvedScope, *, period, data_as_of, actor=None):
    if not connection.is_in_transaction():
        raise RuntimeError('报告取数必须在调用方同一只读可重复读事务中执行')
    settings = await connection.fetchrow("SELECT current_setting('transaction_isolation') AS isolation, "
                                         "current_setting('transaction_read_only') AS readonly, "
                                         "current_setting('row_security') AS rls")
    if dict(settings) != {'isolation': 'repeatable read', 'readonly': 'on', 'rls': 'on'}:
        raise RuntimeError('报告取数必须使用只读可重复读且启用RLS的事务')
    if selection.facts_contract:
        if actor is None:
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '销售周报取材需要当前真实身份', status=403)
        identity = await connection.fetchrow("SELECT common.current_workspace_id()::text AS workspace_id, "
            "common.current_user_ref_id()::text AS user_id,current_setting('row_security') AS rls")
        if not identity or dict(identity) != dict(workspace_id=actor.workspace_id, user_id=actor.user_id, rls='on'):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '当前报告身份不可用', status=403)
    for permission in ('work_report.read', 'work_report.write', *(s + '.read' for s in selection.sources)):
        if not await connection.fetchval('SELECT security.authorization_has($1)', permission):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '所选来源或报告功能未获授权', status=403)
    scopes = (scope,)
    if isinstance(scope, ReviewResolvedScopeV2):
        if actor is None:
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '多范围取材需要当前真实身份', status=403)
        from sales_backend.services.review_scope import resolve_review_scope
        resolution = await resolve_review_scope(connection, actor, selection.scope)
        if resolution.scope != scope:
            raise WorkReportError('WORK_REPORT_SCOPE_CHANGED', '多范围取材成员已变化', status=409)
        scopes = resolution.legacy_scopes
    assembler = FactAssembler(period=period, data_as_of=data_as_of)
    rows_by_type = {}
    for kind in selection.sources:
        by_id = {}
        for subject in scopes:
            if kind == 'actual':
                from sales_backend.repositories.work_report_weekly import read_actuals
                batch = await read_actuals(connection, subject, period)
            else:
                batch = [dict(row) async for row in connection.cursor(
                    source_query(kind, sales_week=bool(selection.facts_contract)), subject.kind,
                    [str(uid) for uid in subject.member_ids],
                    str(subject.team_id) if subject.team_id else None, prefetch=128)]
            for row in batch:
                key = str(row['id'])
                if key in by_id and by_id[key] != row:
                    raise WorkReportError('WORK_REPORT_SOURCE_FAILED', '同一业务事实在多范围取材中不一致', status=503)
                by_id[key] = row
        rows = [by_id[key] for key in sorted(by_id)]
        if kind == 'actual':
            rows_by_type[kind] = rows
            continue
        assembler.add(kind, rows)
        rows_by_type[kind] = rows
    result = assembler.finish()
    if selection.facts_contract:
        from sales_backend.domain.work_report_weekly import assemble_weekly
        from sales_backend.repositories.opportunity_history import collect_first_ge30
        histories = {}
        if 'opportunity' in selection.sources:
            ids = [str(r['id']) for r in rows_by_type['opportunity']]
            try:
                histories = await collect_first_ge30(connection, actor, ids, period_start=period.start, cutoff=period.cutoff)
            except PermissionError as error:
                raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '商机历史已不可读取，请重新取材', status=403) from error
            if set(histories) != set(ids):
                raise WorkReportError('WORK_REPORT_SOURCE_FAILED', '商机历史读取未覆盖完整授权集合', status=503)
        result = assemble_weekly(result, rows_by_type=rows_by_type, selection=selection, period=period, histories=histories)
    return result


async def assert_source_access(connection, *, sources, selected_types, sales_week_facts=None, actor=None):
    """Check every original contributing record, including off-page material.

    Used before any snapshot, saved content, share or export is returned. Counts
    are against fresh caller RLS and record-level report.read, never row IDs from
    a client. A deleted/withdrawn source invalidates the derived historical text.
    """
    from sales_backend.repositories.work_report_weekly import assert_history_access, history_dependencies
    await assert_history_access(connection, actor, [*sources, *history_dependencies(sales_week_facts)])
    for kind in selected_types:
        if not await connection.fetchval('SELECT security.authorization_has($1)', kind + '.read'):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '来源权限已变化，请重新取数', status=403)
        if kind == 'actual':
            from sales_backend.repositories.work_report_weekly import assert_actual_access, period_dependencies
            await assert_actual_access(connection, [*sources, *period_dependencies(sales_week_facts)])
            continue
        ids = sorted({str(UUID(str(s.record_id))) for s in sources if s.source_type == kind})
        if not ids:
            continue
        d = DESCRIPTORS[kind]
        status = "AND s.status IN ('confirmed','archived')" if kind == 'visit' else ''
        count = await connection.fetchval(f"""SELECT count(*) FROM {d['table']} s
          WHERE s.workspace_id=common.current_workspace_id() AND s.deleted_at IS NULL
            AND s.id=ANY($1::uuid[]) AND security.authorization_{kind}('{kind}.read',s.id)
            AND security.authorization_{kind}('work_report.read',s.id) {status}""", ids)
        if count != len(ids):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '原报告部分来源已不可访问，请重新取数', status=403)
