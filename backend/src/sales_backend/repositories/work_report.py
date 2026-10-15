"""Work-report persistence in the caller's actor/RLS transaction.

No connection, transaction, queue, provider or idempotency owner lives here.
Private snapshots/inputs keep writing examples; shared revisions carry only a
thin scheme reference. Worker finalization requires the current fenced lease.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import get_args
from uuid import UUID, uuid4

from sales_backend.contracts.review_scope import ReviewResolvedScopeV2, WorkReportScopeSelectionV2
from sales_backend.contracts.work_report import (
    DocumentQuery,
    DocumentSchemeReference,
    DocumentSummary,
    DocumentView,
    ExportView,
    FactSource,
    GenerationInput,
    GenerationResult,
    Page,
    ReportContent,
    ReportError,
    ResolvedPeriod,
    ResolvedScope,
    RevisionCreate,
    RevisionView,
    RewriteContext,
    RunCreate,
    RunView,
    SchemeConfig,
    SchemeCreate,
    SchemeSnapshot,
    SchemeUpdate,
    SchemeView,
    ShareAudience,
    ShareRequest,
    SnapshotSelection,
    SnapshotView,
    SourceType,
    UserSupplement,
    VersionRequest,
)
from sales_backend.db import json_value
from sales_backend.domain.work_report import (
    WorkReportError,
    canonical_hash,
    default_scheme,
    merge_rewrite,
    render_report_text,
    validate_content,
    validate_generation_result,
    validate_source_note_revision,
)
from sales_backend.domain.work_report_facts import PreparedFacts
from sales_backend.job_context import JobLeaseLost, current_job_lease
from sales_backend.repositories.jobs import record_job_effect
from sales_backend.repositories.work_report_facts import assert_source_access
from sales_backend.repositories.work_report_weekly import private_dependencies
from sales_backend.services import work_report_access as access

TERMINAL = frozenset(('succeeded', 'failed', 'cancelled'))
IDENTITY_SQL = """SELECT common.current_workspace_id()::text AS workspace_id,
 common.current_user_ref_id()::text AS user_id,current_setting('row_security') AS rls"""
DOCUMENT_SELECT = """SELECT d.*,r.content AS visible_content,r.scheme AS visible_scheme,
 r.id AS visible_revision_id,r.revision_no AS visible_revision_no,r.origin AS visible_origin,
 r.created_at AS revision_created_at,r.created_by_user_ref_id AS revision_created_by,r.run_id AS revision_run_id
 FROM agent.work_report_document d JOIN agent.work_report_revision r
 ON r.document_id=d.id AND r.workspace_id=d.workspace_id
 AND r.id=CASE WHEN d.owner_user_ref_id=$2::uuid THEN d.latest_revision_id ELSE d.shared_revision_id END
 WHERE d.workspace_id=$1::uuid AND (d.owner_user_ref_id=$2::uuid OR
 (d.shared_revision_id IS NOT NULL AND security.work_report_audience_matches(d.audience)))
 AND security.work_report_source_access(d.source_manifest,d.selected_sources)"""


def dump(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def parse_scope(value):
    raw = dump(value)
    if isinstance(raw, dict) and raw.get('contract_version') == 'review.scope.v2':
        return ReviewResolvedScopeV2.model_validate(raw)
    return ResolvedScope.model_validate(raw)


def decoded(row):
    if row is None:
        raise WorkReportError('WORK_REPORT_NOT_FOUND', '工作汇报对象不存在或不可见', status=404)
    result = dict(row)
    for key in ('config', 'selection', 'period', 'scope', 'payload', 'error', 'context', 'source_manifest',
                'selected_sources', 'request', 'generation_input', 'result', 'content', 'scheme',
                'audience', 'visible_content', 'visible_scheme'):
        if result.get(key) is not None:
            result[key] = json_value(result[key])
    return result


def page(items, limit, offset):
    if not 1 <= limit <= 100 or offset < 0:
        raise WorkReportError('WORK_REPORT_INVALID_INPUT', '分页参数无效', status=422)
    total = len(items)
    end = offset + limit
    return Page(items=items[offset:end], total=total, limit=limit, offset=offset,
                has_more=end < total, next_offset=end if end < total else None)


def check_version(row, version):
    if row['version_no'] != version:
        raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '内容已更新，请刷新后重试',
                              current_version=row['version_no'])


def check_references(config, previous=None):
    old = {str(r.id): r for r in previous.reference_texts} if previous else {}
    for ref in config.reference_texts:
        prior = old.get(str(ref.id))
        expected = 1 if prior is None else prior.version_no + (prior.text != ref.text)
        if ref.version_no != expected:
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '参考范文版本与原文不一致')


def snapshot_view(row):
    payload = row.get('payload') or {}
    return SnapshotView(id=row['id'], status=row['status'], selection=row['selection'],
        period=row['period'], scope=row['scope'], created_at=row['created_at'],
        data_as_of=row.get('data_as_of'), snapshot_hash=row.get('snapshot_hash'),
        coverage=payload.get('coverage', []), metrics=payload.get('metrics', []), error=row.get('error'),
        sales_week_facts=payload.get('sales_week_facts'), review_facts=payload.get('review_facts'))


async def review_payload_access(connection, actor, payload, *, permission='work_report.read'):
    """Review references stay server-owned and are rechecked with every use."""
    if not await access.can_act_on_frozen_subjects(connection, payload.get('scope', {}), permission):
        raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '报告完整范围已不在当前授权内', status=403)
    if payload.get('review_facts') is not None:
        from sales_backend.services.work_report_review_facts import assert_review_access
        await assert_review_access(connection, actor, payload['review_facts'],
            dependencies=payload.get('review_dependencies'), permission=permission)


def run_view(row):
    result = row.get('result') or {}
    return RunView(**{key: row.get(key) for key in ('id', 'snapshot_id', 'status', 'version_no',
        'created_at', 'started_at', 'finished_at', 'document_id', 'revision_id', 'error')},
        result_status=result.get('status'), provider=result.get('provider'),
        coverage_status=result.get('coverage_status'), trace=result.get('trace'))


def revision_view(row):
    return RevisionView(**{key: row.get(key) for key in ('id', 'document_id', 'revision_no', 'origin',
        'created_at', 'run_id', 'content')}, created_by=row['created_by_user_ref_id'])


def thin_scheme(scheme):
    value = dump(scheme)
    return DocumentSchemeReference(**{key: value[key] for key in ('scheme_id', 'version_no', 'name', 'config_hash')})


class WorkReportRepository:
    async def _enter(self, connection, actor, *permissions):
        if not connection.is_in_transaction():
            raise RuntimeError('工作汇报仓库必须使用调用方事务')
        identity = await connection.fetchrow(IDENTITY_SQL)
        if dict(identity) != dict(workspace_id=actor.workspace_id, user_id=actor.user_id, rls='on'):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '当前报告身份不可用', status=403)
        # Refresh the server-owned authorization context, never trust request flags.
        await connection.execute('SELECT security.authorization_refresh()')
        for permission in dict.fromkeys(('work_report.read', *permissions)):
            if not await connection.fetchval('SELECT security.authorization_has($1)', permission):
                raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '当前报告操作未获授权', status=403)

    async def _capabilities(self, connection, actor, document):
        own = str(document['owner_user_ref_id']) == actor.user_id
        sources, scope = document['source_manifest'], document['context']['scope']
        writable = own and await access.can_act_on_sources(connection, actor, sources, scope, 'work_report.write')
        return dict(can_edit=writable,
            can_share=writable and await access.can_act_on_sources(connection, actor, sources, scope, 'work_report.share'),
            can_export=await access.can_export(connection, actor, sources, scope))

    async def _actions(self, connection, actor, sources, scope, *permissions):
        await connection.execute('SELECT security.authorization_refresh()')
        for permission in permissions:
            if not await access.can_act_on_sources(connection, actor, sources, scope, permission):
                raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '当前操作范围未覆盖全部报告来源', status=403)

    async def _request_actions(self, connection, actor, request, *permissions):
        sources = [*request.sources, *private_dependencies(request.sales_week_facts)]
        if request.overview_facts is not None or request.review_facts is not None:
            snapshot = await self._snapshot(connection, actor, request.snapshot_id)
            sources.extend(snapshot['payload'].get('overview_dependencies') or [])
            sources.extend(snapshot['payload'].get('review_dependencies') or [])
            for permission in permissions:
                await review_payload_access(connection, actor, snapshot['payload'], permission=permission)
        await self._actions(connection, actor, sources, request.scope, *permissions)

    async def _event(self, connection, actor, kind, object_id, action, **metadata):
        await connection.execute("""INSERT INTO agent.work_report_event
          (workspace_id,actor_user_ref_id,object_type,object_id,action,metadata)
          VALUES($1::uuid,$2::uuid,$3,$4::uuid,$5,$6::jsonb)""",
          actor.workspace_id, actor.user_id, kind, str(object_id), action, metadata)

    async def _scheme(self, connection, actor, scheme_id, *, lock=False):
        row = decoded(await connection.fetchrow("""SELECT * FROM agent.work_report_scheme
          WHERE id=$1::uuid AND workspace_id=$2::uuid""" + (' FOR UPDATE' if lock else ''),
          str(scheme_id), actor.workspace_id))
        if lock and str(row['owner_user_ref_id']) != actor.user_id:
            raise WorkReportError('WORK_REPORT_NOT_FOUND', '方案不存在或不可编辑', status=404)
        return row

    async def _scheme_view(self, connection, actor, row):
        can_edit = str(row['owner_user_ref_id']) == actor.user_id and bool(await connection.fetchval(
            "SELECT security.authorization_has('work_report.write')"))
        return SchemeView(**{key: row[key] for key in ('id', 'name', 'config', 'visibility', 'team_id',
            'version_no', 'created_at', 'updated_at')}, owner_id=row['owner_user_ref_id'], can_edit=can_edit)

    async def list_schemes(self, connection, actor, *, limit=40, offset=0):
        await self._enter(connection, actor)
        rows = await connection.fetch("""SELECT * FROM agent.work_report_scheme
          WHERE workspace_id=$1::uuid ORDER BY updated_at DESC,id DESC""", actor.workspace_id)
        return page([await self._scheme_view(connection, actor, decoded(row)) for row in rows], limit, offset)

    async def get_scheme(self, connection, actor, scheme_id):
        await self._enter(connection, actor)
        return await self._scheme_view(connection, actor, await self._scheme(connection, actor, scheme_id))

    async def _scheme_scope(self, connection, actor, body):
        if body.visibility != 'private':
            await self._enter(connection, actor, 'work_report.write', 'work_report.share')
            await access.validate_audience(connection, actor,
                ShareAudience(kind=body.visibility, team_id=body.team_id))

    async def _scheme_revision(self, connection, actor, row):
        await connection.execute("""INSERT INTO agent.work_report_scheme_revision
          (scheme_id,workspace_id,version_no,name,config,config_hash)
          VALUES($1::uuid,$2::uuid,$3,$4,$5::jsonb,$6)""", str(row['id']), actor.workspace_id,
          row['version_no'], row['name'], row['config'], canonical_hash(row['config']))

    async def create_scheme(self, connection, actor, body: SchemeCreate):
        await self._enter(connection, actor, 'work_report.write')
        await self._scheme_scope(connection, actor, body)
        check_references(body.config)
        row = decoded(await connection.fetchrow("""INSERT INTO agent.work_report_scheme
          (workspace_id,owner_user_ref_id,name,visibility,team_id,config)
          VALUES($1::uuid,$2::uuid,$3,$4,$5::uuid,$6::jsonb) RETURNING *""",
          actor.workspace_id, actor.user_id, body.name, body.visibility,
          str(body.team_id) if body.team_id else None, dump(body.config)))
        await self._scheme_revision(connection, actor, row)
        await self._event(connection, actor, 'scheme', row['id'], 'created', version_no=row['version_no'])
        return await self._scheme_view(connection, actor, row)

    async def update_scheme(self, connection, actor, scheme_id, body: SchemeUpdate):
        await self._enter(connection, actor, 'work_report.write')
        old = await self._scheme(connection, actor, scheme_id, lock=True)
        check_version(old, body.version_no)
        await self._scheme_scope(connection, actor, body)
        check_references(body.config, SchemeConfig.model_validate(old['config']))
        row = decoded(await connection.fetchrow("""UPDATE agent.work_report_scheme SET
          name=$4,visibility=$5,team_id=$6::uuid,config=$7::jsonb
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid AND version_no=$8 RETURNING *""",
          str(scheme_id), actor.workspace_id, actor.user_id, body.name, body.visibility,
          str(body.team_id) if body.team_id else None, dump(body.config), body.version_no))
        await self._scheme_revision(connection, actor, row)
        await self._event(connection, actor, 'scheme', row['id'], 'updated', version_no=row['version_no'])
        return await self._scheme_view(connection, actor, row)

    async def _snapshot(self, connection, actor, snapshot_id, *, lock=False):
        row = decoded(await connection.fetchrow("""SELECT * FROM agent.work_report_snapshot
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid""" +
          (' FOR UPDATE' if lock else ''), str(snapshot_id), actor.workspace_id, actor.user_id))
        payload = row.get('payload') or {}
        if row['selection'].get('facts_contract') == 'work_report.meeting_facts.v1':
            raise WorkReportError('WORK_REPORT_NOT_FOUND', '会议快照须使用会议报告入口', status=404)
        await assert_source_access(connection, sources=[FactSource.model_validate(s) for s in payload.get('sources', [])],
                                   selected_types=row['selection']['sources'],
                                   **({'sales_week_facts': payload['sales_week_facts'], 'actor': actor} if 'sales_week_facts' in payload else {}))
        if payload.get('overview_facts') is not None:
            from sales_backend.repositories.work_report_meeting_facts import assert_dependencies
            await assert_dependencies(connection, payload['overview_dependencies'])
        await review_payload_access(connection, actor, payload)
        if row['status'] == 'ready' and canonical_hash(payload) != row['snapshot_hash']:
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '事实快照摘要不一致，请重新取数')
        return row

    async def create_snapshot(self, connection, actor, selection: SnapshotSelection, *,
                              period: ResolvedPeriod, scope: ResolvedScope | ReviewResolvedScopeV2, job_id=None):
        await self._enter(connection, actor, 'work_report.write')
        if isinstance(scope, ReviewResolvedScopeV2):
            if not isinstance(selection.scope, WorkReportScopeSelectionV2):
                raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '多范围快照必须使用scope v2选择', status=403)
            selected = selection.scope
            expected_ids = [] if selected.kind == 'self' else (selected.team_ids if selected.kind == 'team' else selected.member_ids)
            if (selected.kind != scope.kind or tuple(expected_ids) != scope.subject_ids
                    or (selected.expected_scope_revision is not None
                        and selected.expected_scope_revision != scope.scope_revision)):
                raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '已解析取数范围与请求不一致', status=403)
        elif (selection.scope.model_dump() != scope.model_dump(include={'kind', 'member_id', 'team_id'})
              or (scope.kind == 'self' and [str(i) for i in scope.member_ids] != [actor.user_id])):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '已解析取数范围与请求不一致', status=403)
        await assert_source_access(connection, sources=[], selected_types=selection.sources)
        row = decoded(await connection.fetchrow("""INSERT INTO agent.work_report_snapshot
          (workspace_id,owner_user_ref_id,selection,period,scope,job_id)
          VALUES($1::uuid,$2::uuid,$3::jsonb,$4::jsonb,$5::jsonb,$6::uuid) RETURNING *""",
          actor.workspace_id, actor.user_id, dump(selection), dump(period), dump(scope), str(job_id) if job_id else None))
        await self._event(connection, actor, 'snapshot', row['id'], 'created', status='preparing')
        return snapshot_view(row)

    async def get_snapshot(self, connection, actor, snapshot_id):
        await self._enter(connection, actor)
        row = await self._snapshot(connection, actor, snapshot_id)
        return snapshot_view(await self._read_job_outcome(connection, actor, row, snapshot=True))

    async def _read_job_outcome(self, connection, actor, row, *, snapshot):
        """GET-only recovery after a worker lost projection permission.

        Call only after owner/RLS/all-source guards. Never persist a projection,
        read queue diagnostics, guess missing jobs, or change a stored version.
        """
        active = ('preparing',) if snapshot else ('queued', 'running')
        if row['status'] not in active or row.get('job_id') is None:
            return row
        job = await connection.fetchrow("""SELECT status,completed_at FROM ops.job
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND aggregate_id=$3::uuid AND job_type=$4""",
          str(row['job_id']), actor.workspace_id, str(row['id']),
          'work_report.snapshot' if snapshot else 'work_report.generate')
        if job is None or job['status'] != 'dead_letter':
            return row
        derived = dict(row, status='failed', error=dump(ReportError(
            code='WORK_REPORT_JOB_FAILED', detail='工作汇报处理已结束但未能保存结果，请重新发起')))
        if not snapshot:
            derived['finished_at'] = job['completed_at']
        return derived

    async def snapshot_for_job(self, connection, actor, snapshot_id, *, lock=False):
        await self._enter(connection, actor, 'work_report.write')
        row = await self._snapshot(connection, actor, snapshot_id, lock=lock)
        if row['status'] != 'preparing':
            raise WorkReportError('WORK_REPORT_RUN_TERMINAL', '快照已结束，不能再次取数')
        await self._lease(connection, actor, row, 'work_report.snapshot', lock=lock)
        return row

    async def snapshot_input(self, connection, actor, snapshot_id):
        row = await self.snapshot_for_job(connection, actor, snapshot_id)
        return dict(selection=SnapshotSelection.model_validate(row['selection']),
                    period=ResolvedPeriod.model_validate(row['period']), scope=parse_scope(row['scope']),
                    job_id=row.get('job_id'))

    async def list_sources(self, connection, actor, snapshot_id, *, source_type=None, limit=40, offset=0):
        await self._enter(connection, actor)
        row = await self._snapshot(connection, actor, snapshot_id)  # every original source before pagination
        if source_type is not None and source_type not in get_args(SourceType):
            raise WorkReportError('WORK_REPORT_INVALID_INPUT', '来源类型无效', status=422)
        sources = [FactSource.model_validate(s) for s in (row.get('payload') or {}).get('sources', [])
                   if source_type is None or s['source_type'] == source_type]
        return page(sources, limit, offset)

    async def _attach_job(self, connection, actor, object_id, job_id, *, snapshot):
        await self._enter(connection, actor, 'work_report.write')
        row = (await self._snapshot(connection, actor, object_id, lock=True) if snapshot
               else await self._run(connection, actor, object_id, lock=True))
        expected = 'preparing' if snapshot else 'queued'
        if row['status'] != expected or row.get('job_id') is not None:
            raise WorkReportError('WORK_REPORT_RUN_TERMINAL', '对象已绑定作业或已结束')
        kind = 'work_report.snapshot' if snapshot else 'work_report.generate'
        valid = await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM ops.job
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND aggregate_id=$3::uuid
          AND job_type=$4 AND status='queued' AND payload->>'user_id'=$5)""",
          str(job_id), actor.workspace_id, str(object_id), kind, actor.user_id)
        if not valid:
            raise WorkReportError('WORK_REPORT_INVALID_INPUT', '报告作业绑定不一致', status=422)
        sql = ("""UPDATE agent.work_report_snapshot SET job_id=$4::uuid
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid RETURNING *""" if snapshot else
          """UPDATE agent.work_report_run SET job_id=$4::uuid
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid RETURNING *""")
        attached = decoded(await connection.fetchrow(sql, str(object_id), actor.workspace_id, actor.user_id, str(job_id)))
        return snapshot_view(attached) if snapshot else run_view(attached)

    async def attach_snapshot_job(self, connection, actor, snapshot_id, job_id):
        return await self._attach_job(connection, actor, snapshot_id, job_id, snapshot=True)

    async def attach_run_job(self, connection, actor, run_id, job_id):
        return await self._attach_job(connection, actor, run_id, job_id, snapshot=False)

    async def _lease(self, connection, actor, row, job_type, *, lock=True):
        lease = current_job_lease.get()
        if lease is None or str(row.get('job_id')) != str(lease.job_id):
            raise JobLeaseLost('report result does not belong to the current job lease')
        valid = await connection.fetchval("""SELECT 1 FROM ops.job WHERE id=$1::uuid
          AND workspace_id=$2::uuid AND aggregate_id=$3::uuid AND job_type=$4
          AND lease_token=$5::uuid AND status='running' AND locked_until>clock_timestamp()
          AND security.workspace_job_enabled(workspace_id,job_type)""" + (' FOR UPDATE' if lock else ''),
          str(lease.job_id), actor.workspace_id, str(row['id']), job_type, str(lease.token))
        if not valid:
            raise JobLeaseLost('report job lease expired or superseded')

    async def get_completed_job(self, connection, actor, kind, object_id):
        """Only replay a committed result with its existing same-transaction effect.

        A late callback still uses complete_* and is rejected. This entry point
        is only the worker's new attempt admission after queue ACK loss.
        """
        await self._enter(connection, actor, 'work_report.write')
        if kind not in ('snapshot', 'generate'):
            raise ValueError('unknown report job kind')
        # Terminal projections are immutable. FOR UPDATE would also apply the
        # snapshot's preparing-only update policy and hide an already ready row.
        # Mutation paths take their own projection locks; keep the lease fence
        # and committed-effect check here even when replaying a completed result.
        row = (await self._snapshot(connection, actor, object_id) if kind == 'snapshot'
               else await self._run(connection, actor, object_id))
        await self._lease(connection, actor, row, 'work_report.' + kind)
        complete = row['status'] == ('ready' if kind == 'snapshot' else 'succeeded')
        if not complete:
            return None
        exists = await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM ops.job_effect
          WHERE job_id=$1::uuid AND workspace_id=$2::uuid)""", str(row['job_id']), actor.workspace_id)
        if not exists:
            raise WorkReportError('WORK_REPORT_RUN_TERMINAL', '已结束作业缺少完成回执，禁止自动重跑')
        return snapshot_view(row) if kind == 'snapshot' else run_view(row)

    async def complete_snapshot(self, connection, actor, snapshot_id, *, facts: PreparedFacts,
                                permission_version: str, data_as_of=None, job_id=None):
        row = await self.snapshot_for_job(connection, actor, snapshot_id, lock=True)
        if job_id is not None and str(job_id) != str(row['job_id']):
            raise JobLeaseLost('snapshot job binding changed')
        facts = PreparedFacts.model_validate(facts)
        if data_as_of is not None and data_as_of != facts.data_as_of:
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '事实时点不一致')
        supplements = [UserSupplement(source_id='user:' + str(s['id']), author_id=actor.user_id,
            text=s['text'], declared_kind=s['declared_kind'], section_id=s.get('section_id')) for s in row['selection']['supplements']]
        if bool(row['selection'].get('facts_contract')) != bool(facts.sales_week_facts):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '事实版本与取数选择不一致')
        if bool(row['selection'].get('overview_binding')) != bool(facts.overview_facts) or (facts.overview_facts is not None and facts.overview_dependencies is None):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '绑定概览与冻结事实不一致')
        if (bool(row['selection'].get('review_mode')) != bool(facts.review_facts)
                or (facts.review_facts is not None and not facts.review_dependencies)
                or (facts.review_facts is None and facts.review_dependencies is not None)):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', 'Review选择与授权事实依赖不一致')
        payload = dict(**dump(facts), period=row['period'], scope=row['scope'],
            permission_version=permission_version, supplements=[dump(s) for s in supplements])
        if row['selection'].get('review_mode'):
            payload['review_mode'] = row['selection']['review_mode']
        # Reuse the contract's complete coverage/reference validation; dummy scheme
        # is validation-only and is neither persisted in the facts nor published.
        validation_scheme = default_scheme(sales_week=facts.sales_week_facts is not None)
        self._input(row, payload, SchemeSnapshot(scheme_id=UUID(int=1), version_no=1,
            name='快照校验', config=validation_scheme, config_hash=canonical_hash(validation_scheme)),
            facts.review_facts.rules_version if facts.review_facts is not None else 'snapshot.v1', [])
        selected = {c.source_type for c in facts.coverage if c.selected}
        if selected != set(row['selection']['sources']):
            raise WorkReportError('WORK_REPORT_SOURCE_FAILED', '来源覆盖与取数选择不一致', status=503)
        await assert_source_access(connection, sources=facts.sources, selected_types=selected,
                                   **({'sales_week_facts': facts.sales_week_facts, 'actor': actor} if facts.sales_week_facts else {}))
        await review_payload_access(connection, actor, payload, permission='work_report.write')
        await self._actions(connection, actor, [*facts.sources, *private_dependencies(facts.sales_week_facts),
            *(facts.overview_dependencies or []), *(facts.review_dependencies or [])], row['scope'], 'work_report.write')
        digest = canonical_hash(payload)
        saved = decoded(await connection.fetchrow("""UPDATE agent.work_report_snapshot SET status='ready',
          payload=$4::jsonb,snapshot_hash=$5,data_as_of=$6,error=NULL
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid AND status='preparing' RETURNING *""",
          str(snapshot_id), actor.workspace_id, actor.user_id, payload, digest, facts.data_as_of))
        await self._event(connection, actor, 'snapshot', snapshot_id, 'ready', snapshot_hash=digest)
        await self._lease(connection, actor, saved, 'work_report.snapshot')
        await record_job_effect(connection, actor.workspace_id)
        return snapshot_view(saved)

    async def fail_snapshot(self, connection, actor, snapshot_id, error: ReportError):
        row = await self.snapshot_for_job(connection, actor, snapshot_id, lock=True)
        saved = decoded(await connection.fetchrow("""UPDATE agent.work_report_snapshot SET status='failed',error=$4::jsonb
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid AND status='preparing' RETURNING *""",
          str(snapshot_id), actor.workspace_id, actor.user_id, dump(error)))
        await self._event(connection, actor, 'snapshot', snapshot_id, 'failed', status='failed')
        await self._lease(connection, actor, row, 'work_report.snapshot')
        await record_job_effect(connection, actor.workspace_id)
        return snapshot_view(saved)

    def _input(self, snapshot, payload, scheme, rules_version, business_rules, rewrite=None):
        return GenerationInput(snapshot_id=snapshot['id'], snapshot_hash=canonical_hash(payload),
            period=payload['period'], scope=payload['scope'], permission_version=payload['permission_version'],
            data_as_of=payload['data_as_of'], coverage=payload['coverage'], sources=payload['sources'],
            metrics=payload['metrics'], supplements=payload['supplements'], scheme=scheme,
            rules_version=rules_version, business_rules=list(business_rules), rewrite=rewrite,
            sales_week_facts=payload.get('sales_week_facts'), overview_facts=payload.get('overview_facts'),
            review_facts=payload.get('review_facts'), review_mode=payload.get('review_mode'))

    async def _saved_scheme(self, connection, actor, reference):
        from sales_backend.domain.work_report_builtins import builtin_scheme
        builtin = builtin_scheme(reference)
        if builtin is not None:
            return builtin
        current = await self._scheme(connection, actor, reference.scheme_id)
        check_version(current, reference.version_no)
        row = decoded(await connection.fetchrow("""SELECT name,config,config_hash FROM agent.work_report_scheme_revision
          WHERE scheme_id=$1::uuid AND workspace_id=$2::uuid AND version_no=$3""",
          str(reference.scheme_id), actor.workspace_id, reference.version_no))
        if row['config_hash'] != canonical_hash(row['config']):
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '方案修订摘要不一致')
        return SchemeSnapshot(**dump(reference), **row)

    async def _rewrite_scheme(self, connection, actor, document):
        reference = DocumentSchemeReference.model_validate(document['visible_scheme'])
        # A historical config does not bypass today's scheme visibility/RLS.
        from sales_backend.domain.work_report_builtins import builtin_scheme
        if builtin_scheme(reference) is None:
            await self._scheme(connection, actor, reference.scheme_id)
        row = await connection.fetchrow("""SELECT run.generation_input FROM agent.work_report_run run
          JOIN agent.work_report_revision produced ON produced.id=run.revision_id
          AND produced.document_id=run.document_id AND produced.workspace_id=run.workspace_id
          AND produced.run_id=run.id
          WHERE run.document_id=$1::uuid AND run.workspace_id=$2::uuid AND run.owner_user_ref_id=$3::uuid
          AND run.status='succeeded' AND produced.revision_no<=$4 AND produced.scheme=$5::jsonb
          AND (run.generation_input->'scheme')-'config'=$5::jsonb
          AND run.snapshot_id=$6::uuid AND run.generation_input->>'snapshot_hash'=$7
          ORDER BY produced.revision_no DESC LIMIT 1""", str(document['id']), actor.workspace_id, actor.user_id,
          document['visible_revision_no'], dump(reference), str(document['snapshot_id']), document['snapshot_hash'])
        if row is None:
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '找不到与文档修订一致的冻结方案')
        try:
            frozen = GenerationInput.model_validate(decoded(row)['generation_input'])
        except (KeyError, ValueError, TypeError):
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '文档冻结方案缺失或无效') from None
        if (thin_scheme(frozen.scheme) != reference or canonical_hash(frozen.scheme.config) != reference.config_hash
                or str(frozen.snapshot_id) != str(document['snapshot_id']) or frozen.snapshot_hash != document['snapshot_hash']):
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '文档冻结方案摘要不一致')
        return frozen.scheme

    async def create_run(self, connection, actor, body: RunCreate, *, rules_version, business_rules=(), job_id=None):
        await self._enter(connection, actor, 'work_report.write', 'agent.work_report')
        snapshot = await self._snapshot(connection, actor, body.snapshot_id)
        await access.assert_generation_scope(connection, actor, snapshot['scope'])
        if snapshot['status'] != 'ready' or snapshot['snapshot_hash'] != body.snapshot_hash:
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '事实快照未就绪或已变化')
        await review_payload_access(connection, actor, snapshot['payload'], permission='work_report.write')
        await review_payload_access(connection, actor, snapshot['payload'], permission='agent.work_report')
        await self._actions(connection, actor, [*snapshot['payload']['sources'], *private_dependencies(snapshot['payload'].get('sales_week_facts')), *snapshot['payload'].get('overview_dependencies', []), *snapshot['payload'].get('review_dependencies', [])], snapshot['scope'],
                            'work_report.write', 'agent.work_report')
        rewrite = None
        if body.document_id:
            document = await self._document(connection, actor, body.document_id, lock=True)
            self._rewrite_match(document, body, snapshot)
            scheme = await self._rewrite_scheme(connection, actor, document)
            rewrite = RewriteContext(document_id=body.document_id, base_revision_id=body.base_revision_id,
                base_document_version=body.version_no, section_ids=body.section_ids,
                previous_content=document['visible_content'])
        else:
            scheme = await self._saved_scheme(connection, actor, body.scheme)
        if body.scheme_override:
            check_references(body.scheme_override, scheme.config)
            scheme = scheme.model_copy(update=dict(config=body.scheme_override, config_hash=canonical_hash(body.scheme_override)))
        from sales_backend.contracts.work_report import ResolvedPeriod
        from sales_backend.domain.work_report_builtins import check_builtin_period
        check_builtin_period(body.scheme, ResolvedPeriod.model_validate(snapshot['period']), scheme.config,
            facts_contract=snapshot['selection'].get('facts_contract'))
        request = self._input(snapshot, snapshot['payload'], scheme, rules_version, business_rules, rewrite)
        row = decoded(await connection.fetchrow("""INSERT INTO agent.work_report_run
          (workspace_id,owner_user_ref_id,snapshot_id,request,generation_input,document_id,job_id)
          VALUES($1::uuid,$2::uuid,$3::uuid,$4::jsonb,$5::jsonb,$6::uuid,$7::uuid) RETURNING *""",
          actor.workspace_id, actor.user_id, str(body.snapshot_id), dump(body), dump(request),
          str(body.document_id) if body.document_id else None, str(job_id) if job_id else None))
        await self._event(connection, actor, 'run', row['id'], 'queued', status='queued')
        return run_view(row)

    def _rewrite_match(self, document, body, snapshot):
        check_version(document, body.version_no)
        scheme = DocumentSchemeReference.model_validate(document['visible_scheme'])
        if (body.scheme.scheme_id, body.scheme.version_no) != (scheme.scheme_id, scheme.version_no):
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '局部重写必须使用当前文档修订的方案版本')
        if (str(document['latest_revision_id']) != str(body.base_revision_id)
                or str(document['snapshot_id']) != str(snapshot['id'])
                or document['snapshot_hash'] != body.snapshot_hash):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '局部重写必须使用原文档快照与当前修订')

    async def _run(self, connection, actor, run_id, *, lock=False):
        row = decoded(await connection.fetchrow("""SELECT * FROM agent.work_report_run WHERE
          id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid""" + (' FOR UPDATE' if lock else ''),
          str(run_id), actor.workspace_id, actor.user_id))
        snapshot = await self._snapshot(connection, actor, row['snapshot_id'])
        request = GenerationInput.model_validate(row['generation_input'])
        if (str(request.snapshot_id) != str(snapshot['id']) or request.snapshot_hash != snapshot['snapshot_hash']
                or request.snapshot_hash != canonical_hash(snapshot['payload'])):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '作业事实快照已变化')
        material_keys = ('period', 'scope', 'permission_version', 'data_as_of', 'coverage', 'sources', 'metrics', 'supplements')
        supplied = dump(request)
        if (any(supplied[key] != snapshot['payload'][key] for key in material_keys)
                or supplied.get('sales_week_facts') != snapshot['payload'].get('sales_week_facts')
                or supplied.get('overview_facts') != snapshot['payload'].get('overview_facts')
                or supplied.get('review_facts') != snapshot['payload'].get('review_facts')
                or supplied.get('review_mode') != snapshot['payload'].get('review_mode')
                or canonical_hash(request.scheme.config) != request.scheme.config_hash):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '生成输入事实或方案摘要与冻结内容不一致')
        return row

    async def get_run(self, connection, actor, run_id):
        await self._enter(connection, actor)
        row = await self._run(connection, actor, run_id)
        return run_view(await self._read_job_outcome(connection, actor, row, snapshot=False))

    async def generation_input(self, connection, actor, run_id):
        await self._enter(connection, actor, 'work_report.write', 'agent.work_report')
        return GenerationInput.model_validate((await self._run(connection, actor, run_id))['generation_input'])

    async def assert_run_active(self, connection, actor, run_id, *, version_no=None, request=None, lock=False):
        await self._enter(connection, actor, 'work_report.write', 'agent.work_report')
        row = await self._run(connection, actor, run_id, lock=lock)
        if row['status'] in TERMINAL:
            raise WorkReportError('WORK_REPORT_RUN_TERMINAL', '报告作业已结束，不能接受迟到结果')
        if version_no is not None:
            check_version(row, version_no)
        frozen = GenerationInput.model_validate(row['generation_input'])
        if request is not None and canonical_hash(request) != canonical_hash(frozen):
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '生成输入与冻结作业不一致')
        await self._request_actions(connection, actor, frozen, 'work_report.write', 'agent.work_report')
        await self._lease(connection, actor, row, 'work_report.generate', lock=lock)
        if frozen.rewrite:
            document = await self._document(connection, actor, frozen.rewrite.document_id, lock=lock)
            body = RunCreate.model_validate(row['request'])
            self._rewrite_match(document, body, dict(id=frozen.snapshot_id))
            if document['visible_content'] != dump(frozen.rewrite.previous_content):
                raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '原修订内容已变化')
        return row

    async def start_run(self, connection, actor, run_id):
        row = await self.assert_run_active(connection, actor, run_id, lock=True)
        if row['status'] == 'queued':
            row = decoded(await connection.fetchrow("""UPDATE agent.work_report_run SET status='running',
              started_at=clock_timestamp() WHERE id=$1::uuid AND workspace_id=$2::uuid
              AND owner_user_ref_id=$3::uuid AND status='queued' RETURNING *""",
              str(run_id), actor.workspace_id, actor.user_id))
            await self._event(connection, actor, 'run', run_id, 'running', status='running')
        return run_view(row)

    async def cancel_run(self, connection, actor, run_id, body: VersionRequest):
        await self._enter(connection, actor, 'work_report.write')
        row = await self._run(connection, actor, run_id, lock=True)
        check_version(row, body.version_no)
        if row['status'] in TERMINAL:
            raise WorkReportError('WORK_REPORT_RUN_TERMINAL', '报告作业已经结束')
        row = decoded(await connection.fetchrow("""UPDATE agent.work_report_run SET status='cancelled',
          finished_at=clock_timestamp() WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid
          AND version_no=$4 AND status IN ('queued','running') RETURNING *""",
          str(run_id), actor.workspace_id, actor.user_id, body.version_no))
        await self._event(connection, actor, 'run', run_id, 'cancelled', status='cancelled')
        return run_view(row)

    async def fail_run(self, connection, actor, run_id, error: ReportError):
        # An error may itself be a revoked source/agent permission; SQL RLS still
        # decides whether the owner can persist a safe terminal error in this tx.
        await self._enter(connection, actor, 'work_report.write')
        row = await self._run(connection, actor, run_id, lock=True)
        if row['status'] in TERMINAL:
            raise WorkReportError('WORK_REPORT_RUN_TERMINAL', '报告作业已经结束')
        await self._lease(connection, actor, row, 'work_report.generate')
        saved = decoded(await connection.fetchrow("""UPDATE agent.work_report_run SET status='failed',
          error=$4::jsonb,finished_at=clock_timestamp() WHERE id=$1::uuid AND workspace_id=$2::uuid
          AND owner_user_ref_id=$3::uuid AND status IN ('queued','running') RETURNING *""",
          str(run_id), actor.workspace_id, actor.user_id, dump(error)))
        await self._event(connection, actor, 'run', run_id, 'failed', status='failed')
        await self._lease(connection, actor, saved, 'work_report.generate')
        await record_job_effect(connection, actor.workspace_id)
        return run_view(saved)

    async def _document(self, connection, actor, document_id, *, lock=False):
        row = decoded(await connection.fetchrow(DOCUMENT_SELECT + ' AND d.id=$3::uuid' +
          (' FOR UPDATE OF d' if lock else ''), actor.workspace_id, actor.user_id, str(document_id)))
        if lock and str(row['owner_user_ref_id']) != actor.user_id:
            raise WorkReportError('WORK_REPORT_NOT_FOUND', '文档不存在或不可编辑', status=404)
        await self._document_sources(connection, actor, row)
        return row

    async def _document_sources(self, connection, actor, row):
        await review_payload_access(connection, actor, row['context'])
        if row['context'].get('meeting_facts') is not None:
            from sales_backend.repositories.work_report_meeting_facts import assert_dependencies
            await assert_dependencies(connection, row['source_manifest'])
            return
        if row['context'].get('overview_facts') is not None:
            from sales_backend.repositories.work_report_meeting_facts import assert_dependencies
            await assert_dependencies(connection, row['source_manifest'])
        await assert_source_access(connection,
            sources=[SimpleNamespace(**s) for s in row['source_manifest']], selected_types=row['selected_sources'],
            **({'actor': actor} if any(s['source_type'] == 'opportunity_history' for s in row['source_manifest']) else {}))

    async def _document_view(self, connection, actor, row, *, summary=False):
        caps = await self._capabilities(connection, actor, row)
        own = str(row['owner_user_ref_id']) == actor.user_id
        # Readers receive no unshared revision ID, config or raw supplement.
        common = dict(id=row['id'], owner_id=row['owner_user_ref_id'], title=row['visible_content']['title'],
            period=row['context']['period'], scope=row['context']['scope'], snapshot_id=row['snapshot_id'],
            snapshot_hash=row['snapshot_hash'], scheme=thin_scheme(row['visible_scheme']),
            status='shared' if row['shared_revision_id'] else 'private', version_no=row['version_no'],
            latest_revision_id=row['latest_revision_id'] if own else row['shared_revision_id'],
            shared_revision_id=row['shared_revision_id'], created_at=row['created_at'], updated_at=row['updated_at'], **caps)
        if summary:
            return DocumentSummary(**common)
        revision = RevisionView(id=row['visible_revision_id'], document_id=row['id'],
            revision_no=row['visible_revision_no'], origin=row['visible_origin'], created_at=row['revision_created_at'],
            created_by=row['revision_created_by'], run_id=row['revision_run_id'], content=row['visible_content'])
        return DocumentView(**common, revision=revision, coverage=row['context']['coverage'],
                            metrics=row['context']['metrics'], audience=row['audience'] if own else None,
                            meeting_facts=row['context'].get('meeting_facts'), overview_facts=row['context'].get('overview_facts'),
                            review_facts=row['context'].get('review_facts'))

    async def list_documents(self, connection, actor, query: DocumentQuery):
        await self._enter(connection, actor)
        # Literal strpos, not LIKE. Author sees latest; reader searches shared title.
        rows = await connection.fetch(DOCUMENT_SELECT + """ AND ($3::text IS NULL OR
          strpos(lower(r.content->>'title'),lower($3))>0) AND ($4::text IS NULL OR
          CASE WHEN d.shared_revision_id IS NULL THEN 'private' ELSE 'shared' END=$4)
          ORDER BY d.updated_at DESC,d.id DESC""", actor.workspace_id, actor.user_id, query.q, query.status)
        visible = []
        for raw in rows:
            row = decoded(raw)
            try:
                await self._document_sources(connection, actor, row)
            except WorkReportError as error:
                if error.code != 'WORK_REPORT_PERMISSION_CHANGED':
                    raise
                continue
            visible.append(await self._document_view(connection, actor, row, summary=True))
        return page(visible, query.limit, query.offset)

    async def assert_document_readable(self, connection, actor, document_id):
        """Mutation preflight needs fresh visibility, not discarded UI capabilities."""
        await self._enter(connection, actor)
        await self._document(connection, actor, document_id)

    async def get_document(self, connection, actor, document_id):
        await self._enter(connection, actor)
        return await self._document_view(connection, actor, await self._document(connection, actor, document_id))

    async def _revision(self, connection, actor, document, revision_id):
        if str(document['owner_user_ref_id']) != actor.user_id and str(document['shared_revision_id']) != str(revision_id):
            raise WorkReportError('WORK_REPORT_NOT_FOUND', '修订不存在或未分享', status=404)
        return decoded(await connection.fetchrow("""SELECT * FROM agent.work_report_revision
          WHERE id=$1::uuid AND document_id=$2::uuid AND workspace_id=$3::uuid""",
          str(revision_id), str(document['id']), actor.workspace_id))

    async def get_revision(self, connection, actor, document_id, revision_id):
        await self._enter(connection, actor)
        document = await self._document(connection, actor, document_id)
        return revision_view(await self._revision(connection, actor, document, revision_id))

    async def list_revisions(self, connection, actor, document_id, *, limit=40, offset=0):
        await self._enter(connection, actor)
        document = await self._document(connection, actor, document_id)
        rows = await connection.fetch("""SELECT * FROM agent.work_report_revision
          WHERE document_id=$1::uuid AND workspace_id=$2::uuid AND ($3::boolean OR id=$4::uuid)
          ORDER BY revision_no DESC""", str(document_id), actor.workspace_id,
          str(document['owner_user_ref_id']) == actor.user_id, str(document['shared_revision_id']) if document['shared_revision_id'] else None)
        return page([revision_view(decoded(row)) for row in rows], limit, offset)

    async def _document_input(self, connection, actor, document):
        # First generation's private run is immutable and remains the reference
        # contract even if the saved scheme later changes or disappears from view.
        row = decoded(await connection.fetchrow("""SELECT run.generation_input FROM agent.work_report_revision r
          JOIN agent.work_report_run run ON run.id=r.run_id AND run.workspace_id=r.workspace_id
          WHERE r.document_id=$1::uuid AND r.workspace_id=$2::uuid AND r.revision_no=1
          AND run.owner_user_ref_id=$3::uuid""", str(document['id']), actor.workspace_id, actor.user_id))
        request = GenerationInput.model_validate(row['generation_input'])
        if str(request.snapshot_id) != str(document['snapshot_id']) or request.snapshot_hash != document['snapshot_hash']:
            raise WorkReportError('WORK_REPORT_SNAPSHOT_CHANGED', '文档与原始输入不一致')
        await self._snapshot(connection, actor, request.snapshot_id)
        return request

    async def _insert_revision(self, connection, actor, document_id, revision_id, revision_no, content, scheme, origin, run_id=None):
        return decoded(await connection.fetchrow("""INSERT INTO agent.work_report_revision
          (id,workspace_id,document_id,revision_no,origin,content,scheme,created_by_user_ref_id,run_id)
          VALUES($1::uuid,$2::uuid,$3::uuid,$4,$5,$6::jsonb,$7::jsonb,$8::uuid,$9::uuid) RETURNING *""",
          str(revision_id), actor.workspace_id, str(document_id), revision_no, origin, dump(content),
          dump(thin_scheme(scheme)), actor.user_id, str(run_id) if run_id else None))

    async def _advance(self, connection, actor, document, revision_id):
        # Deliberately do not update shared_revision_id or audience.
        changed = await connection.fetchval("""UPDATE agent.work_report_document SET latest_revision_id=$4::uuid
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid
          AND version_no=$5 AND latest_revision_id=$6::uuid RETURNING version_no""",
          str(document['id']), actor.workspace_id, actor.user_id, str(revision_id), document['version_no'],
          str(document['latest_revision_id']))
        if changed is None:
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '文档已更新，不能覆盖')

    async def create_revision(self, connection, actor, document_id, body: RevisionCreate):
        await self._enter(connection, actor, 'work_report.write')
        document = await self._document(connection, actor, document_id, lock=True)
        check_version(document, body.version_no)
        previous = ReportContent.model_validate(document['visible_content'])
        validate_source_note_revision(body.content, previous)
        if document['context'].get('meeting_facts') is not None:
            from sales_backend.domain.work_report_meeting_document import validate_revision
            await self._actions(connection, actor, document['source_manifest'], document['context']['scope'], 'work_report.write')
            validate_revision(body.content, ReportContent.model_validate(document['visible_content']))
            revision_id = uuid4()
            await self._insert_revision(connection, actor, document_id, revision_id, document['visible_revision_no'] + 1,
                body.content, document['visible_scheme'], 'manual')
            await self._document_sources(connection, actor, document)
            await self._advance(connection, actor, document, revision_id)
            await self._event(connection, actor, 'document', document_id, 'manual_revision',
                revision_id=str(revision_id), content_hash=canonical_hash(body.content))
            return await self.get_document(connection, actor, document_id)
        request = await self._document_input(connection, actor, document)
        await self._request_actions(connection, actor, request, 'work_report.write')
        validate_content(body.content, request, generated=False)
        # New/edited prose must be explicitly manual; unchanged structured items
        # may retain their original references and trusted metric placeholders.
        retained = {canonical_hash(item) for section in previous.sections for item in section.items}
        if any(item.kind != 'manual' and canonical_hash(item) not in retained
               for section in body.content.sections for item in section.items):
            raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '新增或修改的段落必须标识为人工编辑', status=422)
        revision_id = uuid4()
        await self._insert_revision(connection, actor, document_id, revision_id, document['visible_revision_no'] + 1,
            body.content, document['visible_scheme'], 'manual')
        await self._document_sources(connection, actor, document)
        await self._request_actions(connection, actor, request, 'work_report.write')
        await self._advance(connection, actor, document, revision_id)
        await self._event(connection, actor, 'document', document_id, 'manual_revision',
                          revision_id=str(revision_id), content_hash=canonical_hash(body.content))
        return await self.get_document(connection, actor, document_id)

    async def share(self, connection, actor, document_id, body: ShareRequest):
        await self._enter(connection, actor, 'work_report.write', 'work_report.share')
        document = await self._document(connection, actor, document_id, lock=True)
        check_version(document, body.version_no)
        await self._actions(connection, actor, document['source_manifest'], document['context']['scope'],
                            'work_report.write', 'work_report.share')
        await access.validate_audience(connection, actor, body.audience)
        changed = await connection.fetchval("""UPDATE agent.work_report_document SET
          shared_revision_id=latest_revision_id,audience=$4::jsonb WHERE id=$1::uuid
          AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid AND version_no=$5 RETURNING version_no""",
          str(document_id), actor.workspace_id, actor.user_id, dump(body.audience), body.version_no)
        if changed is None:
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '文档已更新，不能分享旧版本')
        await self._event(connection, actor, 'document', document_id, 'shared',
                          revision_id=str(document['latest_revision_id']), version_no=changed)
        return await self.get_document(connection, actor, document_id)

    async def unshare(self, connection, actor, document_id, body: VersionRequest):
        await self._enter(connection, actor, 'work_report.write', 'work_report.share')
        document = await self._document(connection, actor, document_id, lock=True)
        check_version(document, body.version_no)
        changed = await connection.fetchval("""UPDATE agent.work_report_document SET shared_revision_id=NULL,audience=NULL
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid AND version_no=$4 RETURNING version_no""",
          str(document_id), actor.workspace_id, actor.user_id, body.version_no)
        if changed is None:
            raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '文档已更新，不能撤回旧版本')
        await self._event(connection, actor, 'document', document_id, 'unshared', version_no=changed)
        return await self.get_document(connection, actor, document_id)

    async def export(self, connection, actor, document_id, *, format='markdown', revision_id=None):
        await self._enter(connection, actor, 'report.export')
        if format not in ('markdown', 'text'):
            raise WorkReportError('WORK_REPORT_INVALID_INPUT', '导出格式无效', status=422)
        document = await self._document(connection, actor, document_id)
        if not await access.can_export(connection, actor, document['source_manifest'], document['context']['scope']):
            raise WorkReportError('WORK_REPORT_PERMISSION_CHANGED', '当前导出范围未覆盖全部报告来源', status=403)
        await review_payload_access(connection, actor, document['context'], permission='report.export')
        revision = await self._revision(connection, actor, document, revision_id or document['visible_revision_id'])
        content = ReportContent.model_validate(revision['content'])
        metrics = {m['metric_id']: m['display_value'] for m in document['context']['metrics']}
        return ExportView(document_id=document_id, revision_id=revision['id'], format=format,
            text=render_report_text(content, metrics, markdown=format == 'markdown',
                **({'review_facts': document['context']['review_facts']} if document['context'].get('review_facts') is not None else {})),
            filename='work-report-' + str(document_id) + ('.md' if format == 'markdown' else '.txt'),
            mime_type='text/markdown; charset=utf-8' if format == 'markdown' else 'text/plain; charset=utf-8')

    async def save_result(self, connection, actor, run_id, result: GenerationResult, *, version_no=None, request=None):
        row = await self.assert_run_active(connection, actor, run_id, version_no=version_no, request=request, lock=True)
        if row['status'] != 'running':
            raise WorkReportError('WORK_REPORT_RUN_TERMINAL', '作业尚未开始，不能保存结果')
        frozen = GenerationInput.model_validate(row['generation_input'])
        result = GenerationResult.model_validate(result)
        validate_generation_result(result, frozen)
        document_id = revision_id = None
        if result.content is not None:
            revision_id = uuid4()
            if frozen.rewrite:
                content = merge_rewrite(result, frozen)
                document_id = frozen.rewrite.document_id
                document = await self._document(connection, actor, document_id, lock=True)
                self._rewrite_match(document, RunCreate.model_validate(row['request']), dict(id=frozen.snapshot_id))
                await self._insert_revision(connection, actor, document_id, revision_id,
                    document['visible_revision_no'] + 1, content, frozen.scheme, 'rewrite', run_id)
                await self._advance(connection, actor, document, revision_id)
            else:
                content = result.content
                document_id = uuid4()
                # FK is deferred; parent first is required by revision INSERT RLS.
                await connection.execute("""INSERT INTO agent.work_report_document
                  (id,workspace_id,owner_user_ref_id,snapshot_id,snapshot_hash,context,source_manifest,
                   selected_sources,latest_revision_id)
                  VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5,'{}'::jsonb,'[]'::jsonb,'[]'::jsonb,$6::uuid)""",
                  str(document_id), actor.workspace_id, actor.user_id, str(frozen.snapshot_id), frozen.snapshot_hash, str(revision_id))
                await self._insert_revision(connection, actor, document_id, revision_id, 1, content,
                                             frozen.scheme, result.status, run_id)
            await self._event(connection, actor, 'document', document_id, 'generated_revision',
                              revision_id=str(revision_id), content_hash=canonical_hash(content))
        # Recheck current source visibility and lease immediately before final
        # transition; the same tx retains run/document locks against cancellation.
        await assert_source_access(connection, sources=frozen.sources,
                                   selected_types=[c.source_type for c in frozen.coverage if c.selected],
                                   **({'sales_week_facts': frozen.sales_week_facts, 'actor': actor} if frozen.sales_week_facts else {}))
        await self._request_actions(connection, actor, frozen, 'work_report.write', 'agent.work_report')
        await self._lease(connection, actor, row, 'work_report.generate')
        saved = decoded(await connection.fetchrow("""UPDATE agent.work_report_run SET status='succeeded',
          result=$4::jsonb,document_id=$5::uuid,revision_id=$6::uuid,finished_at=clock_timestamp(),error=NULL
          WHERE id=$1::uuid AND workspace_id=$2::uuid AND owner_user_ref_id=$3::uuid
          AND status='running' AND version_no=$7 RETURNING *""", str(run_id), actor.workspace_id, actor.user_id,
          dump(result), str(document_id) if document_id else None, str(revision_id) if revision_id else None, row['version_no']))
        await self._event(connection, actor, 'run', run_id, 'succeeded', status='succeeded')
        await record_job_effect(connection, actor.workspace_id)
        return run_view(saved)

    async def complete_run(self, connection, actor, run_id, *, version_no, request, result):
        return await self.save_result(connection, actor, run_id, result, version_no=version_no, request=request)
