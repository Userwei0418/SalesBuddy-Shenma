"""Offline repository tests: stateful rows, real contracts/guards, no PostgreSQL.

FakeReportConnection is deliberately limited to this repository's SQL. It is
not evidence that PostgreSQL RLS/constraints/locking execute correctly.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from sales_backend.contracts.work_report import (
    DocumentQuery, GenerationResult, PeriodSelection, ReportContent, ReportError,
    ReferenceText, ReportItem, ResolvedScope, RevisionCreate, RunCreate, SchemeCreate, SchemeUpdate,
    ShareRequest, SnapshotSelection, VersionRequest,
)
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError, canonical_hash, default_scheme, resolve_period
from sales_backend.domain.work_report_facts import FactAssembler
from sales_backend.job_context import JobLease, JobLeaseLost, current_job_lease
from sales_backend.repositories import work_report as module

NOW = datetime(2026, 10, 9, 5, tzinfo=timezone.utc)
ACTOR = ActorContext(workspace_id=str(UUID(int=1)), user_id=str(UUID(int=2)), role='administrator', data_scope='workspace')
READER = ACTOR.model_copy(update={'user_id': str(UUID(int=3))})
PERIOD = resolve_period(PeriodSelection(), now=NOW)
SCOPE = ResolvedScope(kind='self', label='本人', member_ids=[ACTOR.user_id])


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import asyncpg
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('work report repository tests cannot open connections')
    monkeypatch.setattr(asyncpg, 'connect', forbidden)
    monkeypatch.setattr(asyncpg, 'create_pool', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


class FakeReportConnection:
    """Small in-memory SQL witness reusable by the HTTP/worker offline harness."""
    def __init__(self, actor=ACTOR):
        self.actor = actor
        self.transaction_open = True
        self.rows = {key: {} for key in ('scheme', 'scheme_revision', 'snapshot', 'run', 'document', 'revision')}
        self.jobs = {}
        self.effects = set()
        self.events = []
        self.statements = []
        self.denied = set()
        self.inaccessible = set()
        self.source_checks = []
        self.lease_valid = True
        self.export_allowed = True
        self.action_denied = set()
        self.hidden_schemes = set()

    def is_in_transaction(self):
        return self.transaction_open

    def transaction(self, *args, **kwargs):
        raise AssertionError('repository must not open its own transaction')

    def query(self, sql, args):
        self.statements.append((sql, deepcopy(args)))
        return ' '.join(sql.split())

    def _base(self, workspace, owner, ident=None):
        return dict(id=str(ident or uuid4()), workspace_id=workspace, owner_user_ref_id=owner,
                    created_at=NOW, updated_at=NOW, version_no=1)

    def _store(self, kind, row):
        self.rows[kind][str(row['id'])] = row
        return deepcopy(row)

    def _visible(self, row):
        if row is None or row.get('workspace_id') != self.actor.workspace_id:
            return None
        return row

    def _document(self, row):
        if not self._visible(row):
            return None
        own = row['owner_user_ref_id'] == self.actor.user_id
        audience = row.get('audience') or {}
        shared = audience.get('kind') == 'company' or self.actor.user_id in audience.get('member_ids', [])
        if not own and not shared:
            return None
        rev = self.rows['revision'][row['latest_revision_id'] if own else row['shared_revision_id']]
        return deepcopy(row) | dict(visible_content=deepcopy(rev['content']), visible_scheme=deepcopy(rev['scheme']),
            visible_revision_id=rev['id'], visible_revision_no=rev['revision_no'], visible_origin=rev['origin'],
            revision_created_at=rev['created_at'], revision_created_by=rev['created_by_user_ref_id'], revision_run_id=rev['run_id'])

    async def execute(self, sql, *args):
        q = self.query(sql, args)
        if q == 'SELECT security.authorization_refresh()':
            return 'SELECT 1'
        if q.startswith('INSERT INTO agent.work_report_event'):
            self.events.append(deepcopy(args)); return 'INSERT 0 1'
        if q.startswith('INSERT INTO agent.work_report_scheme_revision'):
            ident, workspace, version, name, config, digest = args
            key = (str(ident), version)
            assert key not in self.rows['scheme_revision']
            self.rows['scheme_revision'][key] = dict(name=name, config=deepcopy(config), config_hash=digest)
            return 'INSERT 0 1'
        if q.startswith('INSERT INTO agent.work_report_document'):
            ident, workspace, owner, snapshot_id, digest, revision = args
            s = self.rows['snapshot'][snapshot_id]
            assert s['status'] == 'ready' and s['snapshot_hash'] == digest and s['owner_user_ref_id'] == owner
            row = self._base(workspace, owner, ident) | dict(snapshot_id=snapshot_id, snapshot_hash=digest,
                context={k: deepcopy(s['payload'][k]) for k in ('period', 'scope', 'coverage', 'metrics')},
                source_manifest=deepcopy(s['source_manifest']), selected_sources=deepcopy(s['selection']['sources']),
                latest_revision_id=revision, shared_revision_id=None, audience=None)
            self._store('document', row); return 'INSERT 0 1'
        if q.startswith('INSERT INTO ops.job_effect'):
            assert self.lease_valid
            self.effects.add(args[0]); return 'INSERT 0 1'
        raise AssertionError('unexpected execute: ' + q)

    async def fetchrow(self, sql, *args):
        q = self.query(sql, args)
        if sql == module.IDENTITY_SQL:
            return dict(workspace_id=self.actor.workspace_id, user_id=self.actor.user_id, rls='on')
        if q.startswith('SELECT status,completed_at FROM ops.job '):
            job = self.jobs.get(args[0])
            if not job or (job['workspace_id'], job['aggregate_id'], job['job_type']) != args[1:]:
                return None
            return dict(status=job.get('status', 'queued'), completed_at=job.get('completed_at'))
        if q.startswith('INSERT INTO agent.work_report_scheme '):
            w, u, name, visibility, team, config = args
            return self._store('scheme', self._base(w, u) | dict(name=name, visibility=visibility, team_id=team, config=deepcopy(config)))
        if q.startswith('INSERT INTO agent.work_report_snapshot '):
            w, u, selection, period, scope, job = args
            return self._store('snapshot', self._base(w, u) | dict(selection=deepcopy(selection), period=deepcopy(period),
                scope=deepcopy(scope), job_id=job, status='preparing', payload=None, error=None, data_as_of=None,
                snapshot_hash=None, source_manifest=[]))
        if q.startswith('INSERT INTO agent.work_report_run '):
            w, u, snapshot, request, generation_input, document, job = args
            return self._store('run', self._base(w, u) | dict(snapshot_id=snapshot, request=deepcopy(request),
                generation_input=deepcopy(generation_input), document_id=document, job_id=job, revision_id=None,
                status='queued', result=None, error=None, started_at=None, finished_at=None))
        if q.startswith('INSERT INTO agent.work_report_revision '):
            ident, w, doc, n, origin, content, scheme, u, run = args
            assert doc in self.rows['document'], 'revision RLS requires parent first'
            assert not any(r['document_id'] == doc and r['revision_no'] == n for r in self.rows['revision'].values())
            assert set(scheme) == {'scheme_id', 'version_no', 'name', 'config_hash'}
            return self._store('revision', dict(id=ident, workspace_id=w, document_id=doc, revision_no=n,
                origin=origin, content=deepcopy(content), scheme=deepcopy(scheme), created_by_user_ref_id=u,
                run_id=run, created_at=NOW))
        if q.startswith('SELECT run.generation_input FROM agent.work_report_run run'):
            doc, workspace, owner, revision_no, scheme, snapshot, digest = args
            candidates = []
            for run in self.rows['run'].values():
                revision = self.rows['revision'].get(run.get('revision_id'))
                if (not revision or run['status'] != 'succeeded' or run['document_id'] != doc
                        or run['workspace_id'] != workspace or run['owner_user_ref_id'] != owner
                        or revision['id'] != run['revision_id'] or revision['document_id'] != doc
                        or revision['workspace_id'] != workspace or revision['run_id'] != run['id']
                        or revision['revision_no'] > revision_no or revision['scheme'] != scheme
                        or run['snapshot_id'] != snapshot or run['generation_input']['snapshot_hash'] != digest):
                    continue
                frozen_scheme = run['generation_input']['scheme']
                if {key: value for key, value in frozen_scheme.items() if key != 'config'} != scheme:
                    continue
                candidates.append((revision['revision_no'], run))
            if not candidates: return None
            return dict(generation_input=deepcopy(max(candidates, key=lambda pair: pair[0])[1]['generation_input']))
        if q.startswith('SELECT run.generation_input FROM agent.work_report_revision r'):
            rev = next(r for r in self.rows['revision'].values() if r['document_id'] == args[0] and r['revision_no'] == 1)
            return dict(generation_input=deepcopy(self.rows['run'][rev['run_id']]['generation_input']))
        if q.startswith('SELECT d.*,r.content'):
            return self._document(self.rows['document'].get(args[2]))
        if q.startswith('SELECT name,config,config_hash FROM agent.work_report_scheme_revision'):
            return deepcopy(self.rows['scheme_revision'].get((args[0], args[2])))
        if q.startswith('SELECT * FROM agent.work_report_revision'):
            row = self.rows['revision'].get(args[0])
            return deepcopy(row) if row and row['document_id'] == args[1] and row['workspace_id'] == args[2] else None
        for kind in ('scheme', 'snapshot', 'run'):
            if q.startswith('SELECT * FROM agent.work_report_' + kind + ' '):
                row = self._visible(self.rows[kind].get(args[0]))
                if kind == 'scheme' and args[0] in self.hidden_schemes:
                    return None
                if row and kind != 'scheme' and row['owner_user_ref_id'] != args[2]:
                    return None
                return deepcopy(row)
            if q.startswith('UPDATE agent.work_report_' + kind + ' '):
                row = self.rows[kind].get(args[0])
                if not self._visible(row) or row['owner_user_ref_id'] != args[2]:
                    return None
                if kind == 'scheme':
                    if row['version_no'] != args[7]: return None
                    row.update(name=args[3], visibility=args[4], team_id=args[5], config=deepcopy(args[6]))
                elif 'SET job_id=' in q:
                    row['job_id'] = args[3]
                elif kind == 'snapshot':
                    assert row['status'] == 'preparing'
                    if "status='ready'" in q:
                        row.update(status='ready', payload=deepcopy(args[3]), snapshot_hash=args[4], data_as_of=args[5])
                        row['source_manifest'] = [dict(source_type=s['source_type'], record_id=s['record_id']) for s in args[3]['sources']]
                    else: row.update(status='failed', error=deepcopy(args[3]))
                elif "SET status='running'" in q:
                    assert row['status'] == 'queued'; row.update(status='running', started_at=NOW)
                elif "SET status='cancelled'" in q:
                    assert row['status'] not in module.TERMINAL and row['version_no'] == args[3]
                    row.update(status='cancelled', finished_at=NOW)
                elif "SET status='failed'" in q:
                    assert row['status'] not in module.TERMINAL; row.update(status='failed', finished_at=NOW, error=deepcopy(args[3]))
                elif "SET status='succeeded'" in q:
                    assert row['status'] == 'running' and row['version_no'] == args[6]
                    row.update(status='succeeded', result=deepcopy(args[3]), document_id=args[4], revision_id=args[5], finished_at=NOW)
                else: raise AssertionError(q)
                if kind != 'snapshot': row['version_no'] += 1
                return deepcopy(row)
        raise AssertionError('unexpected fetchrow: ' + q)

    async def fetchval(self, sql, *args):
        q = self.query(sql, args)
        if q.startswith('SELECT security.authorization_has('):
            permission = args[0] if args else q.split("'")[1]
            return permission not in self.denied
        if q.startswith('SELECT security.authorization_snapshot'):
            return dict(workspace_id=self.actor.workspace_id, user_id=self.actor.user_id, permission_version='v1', grants=[
                dict(permission_code=p, effect='allow', scope_code='workspace', team_ids=[], source_code='test', source_id='fixed')
                for p in ('work_report.read', 'work_report.write', 'work_report.share', 'report.export') if p not in self.denied])
        if q.startswith('SELECT count(*) FROM unnest('):
            permission = args[1]
            permitted = permission not in self.action_denied and (permission != 'report.export' or self.export_allowed)
            return len(args[0]) if permitted else 0
        if q.startswith('SELECT security.authorization_subject('):
            permission = args[1]
            return permission not in self.action_denied and (permission != 'report.export' or self.export_allowed)
        if q.startswith('SELECT count(*) FROM '):
            self.source_checks.append(list(args[0]))
            return len(set(args[0]) - self.inaccessible)
        if q.startswith('SELECT EXISTS(SELECT 1 FROM ops.job_effect'):
            return args[0] in self.effects
        if q.startswith('SELECT EXISTS(SELECT 1 FROM ops.job '):
            job = self.jobs.get(args[0])
            return bool(job and (job['workspace_id'], job['aggregate_id'], job['job_type'], job['payload']['user_id']) == args[1:])
        if q.startswith('SELECT 1 FROM ops.job '):
            job = self.jobs.get(args[0])
            return 1 if self.lease_valid and job and (job['workspace_id'], job['aggregate_id'], job['job_type'], job['token']) == args[1:] else None
        if q.startswith('UPDATE agent.work_report_document '):
            row = self.rows['document'][args[0]]
            assert row['owner_user_ref_id'] == args[2]
            if 'SET latest_revision_id=' in q:
                if (row['version_no'], row['latest_revision_id']) != (args[4], args[5]): return None
                row['latest_revision_id'] = args[3]
            elif 'SET shared_revision_id=latest_revision_id' in q:
                if row['version_no'] != args[4]: return None
                row.update(shared_revision_id=row['latest_revision_id'], audience=deepcopy(args[3]))
            else:
                if row['version_no'] != args[3]: return None
                row.update(shared_revision_id=None, audience=None)
            row['version_no'] += 1
            return row['version_no']
        raise AssertionError('unexpected fetchval: ' + q)

    async def fetch(self, sql, *args):
        q = self.query(sql, args)
        if q.startswith('SELECT u.id::text,u.display_name AS name'):
            return [dict(id=a.user_id, name='合成成员') for a in (ACTOR, READER)]
        if q.startswith('SELECT t.id::text,t.name'):
            return []
        if q.startswith('SELECT * FROM agent.work_report_scheme '):
            return deepcopy(list(self.rows['scheme'].values()))
        if q.startswith('SELECT * FROM agent.work_report_revision '):
            return deepcopy(sorted([r for r in self.rows['revision'].values() if r['document_id'] == args[0]
                and (args[2] or r['id'] == args[3])], key=lambda r: r['revision_no'], reverse=True))
        if q.startswith('SELECT d.*,r.content'):
            visible = [self._document(row) for row in self.rows['document'].values()]
            return [r for r in visible if r and (args[2] is None or args[2].lower() in r['visible_content']['title'].lower())
                and (args[3] is None or ('shared' if r['shared_revision_id'] else 'private') == args[3])]
        raise AssertionError('unexpected fetch: ' + q)

    def job(self, ident, kind):
        job_id, token = str(uuid4()), str(uuid4())
        self.jobs[job_id] = dict(workspace_id=self.actor.workspace_id, aggregate_id=str(ident), job_type='work_report.' + kind,
                                payload=dict(user_id=self.actor.user_id), token=token)
        return JobLease(job_id, token)


@contextmanager
def lease(value):
    token = current_job_lease.set(value)
    try:
        yield
    finally:
        current_job_lease.reset(token)


def material(count=1):
    assembler = FactAssembler(period=PERIOD, data_as_of=NOW)
    assembler.add('visit', [dict(id=str(UUID(int=100 + i)), version_no=1, interaction_at=PERIOD.start,
        follow_up_record='完整材料' + str(i), contributors=[]) for i in range(count)])
    return assembler.finish()


async def prepare(repo, connection, *, sources=1, references=False):
    config = default_scheme()
    if references:
        config.reference_texts = [ReferenceText(id=str(UUID(int=99)), version_no=1, text='PRIVATE WRITING REFERENCE')]
    scheme = await repo.create_scheme(connection, connection.actor, SchemeCreate(name='合成方案', config=config))
    selection = SnapshotSelection(sources=['visit'])
    snapshot = await repo.create_snapshot(connection, connection.actor, selection, period=PERIOD, scope=SCOPE)
    job = connection.job(snapshot.id, 'snapshot')
    await repo.attach_snapshot_job(connection, connection.actor, snapshot.id, job.job_id)
    with lease(job):
        snapshot = await repo.complete_snapshot(connection, connection.actor, snapshot.id, facts=material(sources), permission_version='v1')
    return scheme, snapshot


def result_for(request, *, title='合成汇报', insufficient=False):
    sections = request.rewrite.previous_content.sections if request.rewrite else request.scheme.config.sections
    ids = set(request.rewrite.section_ids) if request.rewrite else {s.id for s in sections if s.enabled}
    content = None if insufficient else ReportContent(title=request.rewrite.previous_content.title if request.rewrite else title,
        sections=[dict(id=s.id, title=s.title, items=[dict(kind='fact', text='有据进展',
             source_refs=[request.sources[0].source_id])]) for s in sections if s.id in ids])
    return GenerationResult(status='insufficient_data' if insufficient else 'generated', provider='none' if insufficient else 'direct',
        snapshot_id=request.snapshot_id, snapshot_hash=request.snapshot_hash, scheme_id=request.scheme.scheme_id,
        scheme_version=request.scheme.version_no, coverage_status='complete', content=content,
        trace=dict(prompt_version='work_report.v1', elapsed_ms=1, chunks_total=0 if insufficient else 1, chunks_completed=0 if insufficient else 1))


async def generate(repo, connection, scheme, snapshot, *, body=None, title='合成汇报'):
    body = body or RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
                            scheme=dict(scheme_id=scheme.id, version_no=scheme.version_no))
    run = await repo.create_run(connection, connection.actor, body, rules_version='work_report.v1')
    job = connection.job(run.id, 'generate')
    run = await repo.attach_run_job(connection, connection.actor, run.id, job.job_id)
    with lease(job):
        run = await repo.start_run(connection, connection.actor, run.id)
        request = await repo.generation_input(connection, connection.actor, run.id)
        run = await repo.complete_run(connection, connection.actor, run.id, version_no=run.version_no,
                                      request=request, result=result_for(request, title=title))
    return run, job, request


@pytest.fixture
def store():
    return module.WorkReportRepository(), FakeReportConnection()


@pytest.mark.asyncio
async def test_complete_private_manual_share_then_edit_keeps_published_revision_and_thin_scheme(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c, references=True)
    run, job, request = await generate(repo, c, scheme, snapshot)
    document = await repo.get_document(c, ACTOR, run.document_id)
    assert document.status == 'private' and job.job_id in c.effects
    assert 'config' not in document.scheme.model_dump() and 'PRIVATE WRITING' not in document.model_dump_json()
    content = document.revision.content.model_copy(deep=True)
    content.title = '已分享标题%_'
    content.sections[0].items = [ReportItem(kind='manual', text='本人确认的编辑')]
    document = await repo.create_revision(c, ACTOR, document.id, RevisionCreate(version_no=document.version_no, content=content))
    shared = await repo.share(c, ACTOR, document.id, ShareRequest(version_no=document.version_no, confirm=True,
                                                               audience=dict(kind='members', member_ids=[READER.user_id])))
    published_id = shared.shared_revision_id
    content = content.model_copy(update={'title': '作者尚未分享新标题'})
    edited = await repo.create_revision(c, ACTOR, document.id, RevisionCreate(version_no=shared.version_no, content=content))
    assert edited.shared_revision_id == published_id and edited.latest_revision_id != published_id
    c.actor = READER
    reader = await repo.get_document(c, READER, document.id)
    assert reader.title == '已分享标题%_' and reader.latest_revision_id == published_id and reader.audience is None
    assert not reader.can_edit and reader.scheme == shared.scheme
    assert (await repo.list_documents(c, READER, DocumentQuery(q='%_'))).total == 1
    assert (await repo.list_documents(c, READER, DocumentQuery(q='尚未分享'))).total == 0
    revisions = await repo.list_revisions(c, READER, document.id)
    assert [r.id for r in revisions.items] == [published_id]
    with pytest.raises(WorkReportError) as denied:
        await repo.get_revision(c, READER, document.id, edited.latest_revision_id)
    assert denied.value.status == 404
    exported = await repo.export(c, READER, document.id, format='text')
    assert '人工编辑：本人确认的编辑' in exported.text and exported.revision_id == published_id
    assert not any('FROM agent.work_report_snapshot' in sql for sql, _ in c.statements[-10:])


@pytest.mark.asyncio
async def test_scheme_cas_and_reference_text_versions_are_immutable(store):
    repo, c = store
    scheme, _ = await prepare(repo, c, references=True)
    config = scheme.config.model_copy(deep=True)
    config.reference_texts[0].text = 'changed'
    with pytest.raises(WorkReportError, match='范文版本'):
        await repo.update_scheme(c, ACTOR, scheme.id, SchemeUpdate(version_no=1, name='新方案', config=config))
    config.reference_texts[0].version_no = 2
    changed = await repo.update_scheme(c, ACTOR, scheme.id, SchemeUpdate(version_no=1, name='新方案', config=config))
    assert changed.version_no == 2 and c.rows['scheme_revision'][(str(scheme.id), 1)]['config']['reference_texts'][0]['text'] == 'PRIVATE WRITING REFERENCE'
    with pytest.raises(WorkReportError) as stale:
        await repo.update_scheme(c, ACTOR, scheme.id, SchemeUpdate(version_no=1, name='旧版本', config=config))
    assert stale.value.current_version == 2


@pytest.mark.asyncio
async def test_source_revocation_outside_requested_page_rejects_snapshot_and_document(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c, sources=101)
    run, _, _ = await generate(repo, c, scheme, snapshot)
    c.inaccessible.add(str(UUID(int=200)))
    for operation in (repo.list_sources(c, ACTOR, snapshot.id, limit=1), repo.get_document(c, ACTOR, run.document_id)):
        with pytest.raises(WorkReportError) as denied:
            await operation
        assert denied.value.code == 'WORK_REPORT_PERMISSION_CHANGED'
    assert any(len(ids) == 101 for ids in c.source_checks)


@pytest.mark.asyncio
async def test_visible_set_filter_before_total_and_offset_has_no_hidden_300_or_500_cap(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    run, _, _ = await generate(repo, c, scheme, snapshot)
    original = c.rows['document'][str(run.document_id)]
    for _ in range(505):
        clone = deepcopy(original); clone['id'] = str(uuid4()); c.rows['document'][clone['id']] = clone
    values = await repo.list_documents(c, ACTOR, DocumentQuery(limit=3, offset=501))
    assert values.total == 506 and len(values.items) == 3 and values.next_offset == 504
    c.inaccessible.add(str(UUID(int=100)))
    empty = await repo.list_documents(c, ACTOR, DocumentQuery(limit=1))
    assert empty.total == 0 and not empty.has_more
    statement = next(sql for sql, _ in c.statements if sql.startswith('SELECT d.*,r.content') and 'strpos' in sql)
    assert 'LIKE' not in statement and 'LIMIT' not in statement


@pytest.mark.asyncio
async def test_export_scope_and_share_audience_use_current_access_service(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    run, _, _ = await generate(repo, c, scheme, snapshot)
    c.export_allowed = False
    assert not (await repo.get_document(c, ACTOR, run.document_id)).can_export
    with pytest.raises(WorkReportError, match='导出范围'):
        await repo.export(c, ACTOR, run.document_id)
    with pytest.raises(WorkReportError, match='共享对象'):
        await repo.share(c, ACTOR, run.document_id, ShareRequest(version_no=1, confirm=True,
            audience=dict(kind='members', member_ids=[str(UUID(int=9999))])))


@pytest.mark.asyncio
async def test_cancel_and_lost_lease_reject_late_generation_without_revision_or_effect(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    run = await repo.create_run(c, ACTOR, RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
        scheme=dict(scheme_id=scheme.id, version_no=1)), rules_version='v1')
    job = c.job(run.id, 'generate')
    run = await repo.attach_run_job(c, ACTOR, run.id, job.job_id)
    with lease(job):
        active = await repo.start_run(c, ACTOR, run.id)
        request = await repo.generation_input(c, ACTOR, run.id)
        c.lease_valid = False
        with pytest.raises(JobLeaseLost):
            await repo.complete_run(c, ACTOR, run.id, version_no=active.version_no, request=request, result=result_for(request))
        c.lease_valid = True
    await repo.cancel_run(c, ACTOR, run.id, VersionRequest(version_no=active.version_no))
    with lease(job), pytest.raises(WorkReportError) as terminal:
        await repo.complete_run(c, ACTOR, run.id, version_no=active.version_no, request=request, result=result_for(request))
    assert terminal.value.code == 'WORK_REPORT_RUN_TERMINAL'
    assert not c.rows['document'] and not c.rows['revision'] and job.job_id not in c.effects


@pytest.mark.asyncio
async def test_rewrite_preserves_unselected_manual_sections_and_rejects_cas_race(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    initial, _, _ = await generate(repo, c, scheme, snapshot)
    document = await repo.get_document(c, ACTOR, initial.document_id)
    content = document.revision.content.model_copy(deep=True)
    content.sections[-1].items = [ReportItem(kind='manual', text='KEEP 原字节 & 空格')]
    document = await repo.create_revision(c, ACTOR, document.id, RevisionCreate(version_no=1, content=content))
    body = RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
        scheme=dict(scheme_id=scheme.id, version_no=1), document_id=document.id, version_no=document.version_no,
        base_revision_id=document.latest_revision_id, section_ids=['overview'])
    rewritten, _, _ = await generate(repo, c, scheme, snapshot, body=body)
    after = await repo.get_document(c, ACTOR, rewritten.document_id)
    assert after.revision.content.sections[-1] == document.revision.content.sections[-1]
    with pytest.raises(WorkReportError) as conflict:
        await repo.create_run(c, ACTOR, body, rules_version='v1')
    assert conflict.value.code == 'WORK_REPORT_VERSION_CONFLICT'


@pytest.mark.asyncio
async def test_terminal_replay_requires_existing_effect_and_never_inserts_one(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    run, job, _ = await generate(repo, c, scheme, snapshot)
    with lease(job):
        replay = await repo.get_completed_job(c, ACTOR, 'generate', run.id)
        assert replay.status == 'succeeded'
        c.effects.remove(job.job_id)
        with pytest.raises(WorkReportError, match='完成回执'):
            await repo.get_completed_job(c, ACTOR, 'generate', run.id)
    assert job.job_id not in c.effects


@pytest.mark.asyncio
async def test_empty_selected_source_permission_and_snapshot_hash_are_rechecked(store):
    repo, c = store
    _, snapshot = await prepare(repo, c, sources=0)
    c.denied.add('visit.read')
    with pytest.raises(WorkReportError):
        await repo.get_snapshot(c, ACTOR, snapshot.id)
    c.denied.clear()
    c.rows['snapshot'][str(snapshot.id)]['payload']['permission_version'] = 'forged'
    with pytest.raises(WorkReportError) as changed:
        await repo.get_snapshot(c, ACTOR, snapshot.id)
    assert changed.value.code == 'WORK_REPORT_SNAPSHOT_CHANGED'


@pytest.mark.asyncio
async def test_identity_or_transaction_mismatch_stops_before_any_persistence(store):
    repo, c = store
    c.transaction_open = False
    with pytest.raises(RuntimeError, match='调用方事务'):
        await repo.list_schemes(c, ACTOR)
    c.transaction_open = True
    with pytest.raises(WorkReportError):
        await repo.list_schemes(c, READER)
    assert not c.events


@pytest.mark.asyncio
async def test_failed_snapshot_is_terminal_and_sanitized_error_is_contract_owned(store):
    repo, c = store
    snapshot = await repo.create_snapshot(c, ACTOR, SnapshotSelection(sources=['visit']), period=PERIOD, scope=SCOPE)
    job = c.job(snapshot.id, 'snapshot')
    await repo.attach_snapshot_job(c, ACTOR, snapshot.id, job.job_id)
    with lease(job):
        failed = await repo.fail_snapshot(c, ACTOR, snapshot.id,
            ReportError(code='WORK_REPORT_SOURCE_FAILED', detail='来源读取失败，请重新取数'))
        with pytest.raises(WorkReportError, match='快照已结束'):
            await repo.complete_snapshot(c, ACTOR, snapshot.id, facts=material(), permission_version='v1')
    assert failed.status == 'failed' and job.job_id in c.effects and failed.snapshot_hash is None


@pytest.mark.asyncio
async def test_override_is_only_run_input_and_manual_edit_cannot_forge_automatic_evidence(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    override = scheme.config.model_copy(deep=True)
    override.style.tone = 'narrative'
    body = RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
        scheme=dict(scheme_id=scheme.id, version_no=1), scheme_override=override)
    run, _, request = await generate(repo, c, scheme, snapshot, body=body)
    assert request.scheme.config_hash == canonical_hash(override)
    assert (await repo.get_scheme(c, ACTOR, scheme.id)).config.style.tone == 'concise'
    document = await repo.get_document(c, ACTOR, run.document_id)
    forged = document.revision.content.model_copy(deep=True)
    forged.sections[0].items[0].text = '把核定条目改成别的成果'
    with pytest.raises(WorkReportError, match='人工编辑'):
        await repo.create_revision(c, ACTOR, document.id, RevisionCreate(version_no=1, content=forged))
    assert len(c.rows['revision']) == 1


@pytest.mark.parametrize('permission', ['work_report.write', 'agent.work_report'])
@pytest.mark.asyncio
async def test_narrow_mutation_permission_cannot_borrow_wider_read_scope(store, permission):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    c.action_denied.add(permission)
    with pytest.raises(WorkReportError, match='操作范围'):
        await repo.create_run(c, ACTOR, RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
            scheme=dict(scheme_id=scheme.id, version_no=1)), rules_version='v1')
    assert not c.rows['run']


@pytest.mark.asyncio
async def test_share_scope_denial_does_not_prevent_owner_withdrawal(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    run, _, _ = await generate(repo, c, scheme, snapshot)
    document = await repo.share(c, ACTOR, run.document_id,
        ShareRequest(version_no=1, confirm=True, audience=dict(kind='company')))
    c.action_denied.add('work_report.share')
    assert not (await repo.get_document(c, ACTOR, document.id)).can_share
    with pytest.raises(WorkReportError, match='操作范围'):
        await repo.share(c, ACTOR, document.id,
            ShareRequest(version_no=document.version_no, confirm=True, audience=dict(kind='company')))
    withdrawn = await repo.unshare(c, ACTOR, document.id, VersionRequest(version_no=document.version_no))
    assert withdrawn.status == 'private' and withdrawn.shared_revision_id is None
    c.actor = READER
    with pytest.raises(WorkReportError) as missing:
        await repo.get_document(c, READER, document.id)
    assert missing.value.status == 404


@pytest.mark.parametrize('fault', ['document_version', 'source_revoked', 'input_changed', 'scheme_hash_changed'])
@pytest.mark.asyncio
async def test_final_worker_recheck_prevents_rewrite_overwrite_or_unbound_input(store, fault):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    prior, _, _ = await generate(repo, c, scheme, snapshot)
    document = await repo.get_document(c, ACTOR, prior.document_id)
    body = RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
        scheme=dict(scheme_id=scheme.id, version_no=1), document_id=document.id,
        version_no=document.version_no, base_revision_id=document.latest_revision_id, section_ids=['overview'])
    run = await repo.create_run(c, ACTOR, body, rules_version='v1')
    job = c.job(run.id, 'generate')
    await repo.attach_run_job(c, ACTOR, run.id, job.job_id)
    with lease(job):
        active = await repo.start_run(c, ACTOR, run.id)
        request = await repo.generation_input(c, ACTOR, run.id)
        if fault == 'document_version': c.rows['document'][str(document.id)]['version_no'] += 1
        if fault == 'source_revoked': c.inaccessible.add(str(UUID(int=100)))
        if fault == 'input_changed': c.rows['run'][str(run.id)]['generation_input']['sources'][0]['text'] = 'forged source text'
        if fault == 'scheme_hash_changed': c.rows['run'][str(run.id)]['generation_input']['scheme']['config']['style']['tone'] = 'narrative'
        with pytest.raises(WorkReportError):
            await repo.complete_run(c, ACTOR, run.id, version_no=active.version_no, request=request, result=result_for(request))
    assert len(c.rows['revision']) == 1 and job.job_id not in c.effects


@pytest.mark.asyncio
async def test_insufficient_result_has_no_document_and_terminal_failure_never_overwrites_it(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c, sources=0)
    run = await repo.create_run(c, ACTOR, RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
        scheme=dict(scheme_id=scheme.id, version_no=1)), rules_version='v1')
    job = c.job(run.id, 'generate')
    await repo.attach_run_job(c, ACTOR, run.id, job.job_id)
    with lease(job):
        active = await repo.start_run(c, ACTOR, run.id)
        request = await repo.generation_input(c, ACTOR, run.id)
        done = await repo.complete_run(c, ACTOR, run.id, version_no=active.version_no,
                                       request=request, result=result_for(request, insufficient=True))
        with pytest.raises(WorkReportError, match='已经结束'):
            await repo.fail_run(c, ACTOR, run.id, ReportError(code='WORK_REPORT_GENERATION_UNAVAILABLE', detail='生成暂不可用'))
    assert done.result_status == 'insufficient_data' and done.document_id is None and not c.rows['revision']


@pytest.mark.asyncio
async def test_job_binding_requires_same_owner_aggregate_and_queue_type(store):
    repo, c = store
    snapshot = await repo.create_snapshot(c, ACTOR, SnapshotSelection(sources=['visit']), period=PERIOD, scope=SCOPE)
    job = c.job(uuid4(), 'generate')
    with pytest.raises(WorkReportError, match='作业绑定'):
        await repo.attach_snapshot_job(c, ACTOR, snapshot.id, job.job_id)
    assert c.rows['snapshot'][str(snapshot.id)]['job_id'] is None


@pytest.mark.asyncio
async def test_failed_run_records_effect_once_and_rejects_late_success(store):
    repo, c = store
    scheme, snapshot = await prepare(repo, c)
    run = await repo.create_run(c, ACTOR, RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
        scheme=dict(scheme_id=scheme.id, version_no=1)), rules_version='v1')
    job = c.job(run.id, 'generate')
    await repo.attach_run_job(c, ACTOR, run.id, job.job_id)
    with lease(job):
        active = await repo.start_run(c, ACTOR, run.id)
        request = await repo.generation_input(c, ACTOR, run.id)
        error = ReportError(code='WORK_REPORT_GENERATION_UNAVAILABLE', detail='生成暂不可用')
        failed = await repo.fail_run(c, ACTOR, run.id, error)
        with pytest.raises(WorkReportError) as terminal:
            await repo.complete_run(c, ACTOR, run.id, version_no=active.version_no, request=request, result=result_for(request))
        assert terminal.value.code == 'WORK_REPORT_RUN_TERMINAL'
    assert failed.status == 'failed' and failed.error == error and job.job_id in c.effects
    assert not c.rows['document']
    effects = [(sql, args) for sql, args in c.statements if sql.startswith('INSERT INTO ops.job_effect') and args[0] == job.job_id]
    assert len(effects) == 1


@pytest.mark.asyncio
async def test_ready_snapshot_is_immutable_and_lease_required_for_completion(store):
    repo, c = store
    _, snapshot = await prepare(repo, c)
    frozen = deepcopy(c.rows['snapshot'][str(snapshot.id)])
    with pytest.raises(WorkReportError, match='快照已结束'):
        await repo.complete_snapshot(c, ACTOR, snapshot.id, facts=material(2), permission_version='v2')
    assert c.rows['snapshot'][str(snapshot.id)] == frozen
    pending = await repo.create_snapshot(c, ACTOR, SnapshotSelection(sources=['visit']), period=PERIOD, scope=SCOPE)
    job = c.job(pending.id, 'snapshot')
    await repo.attach_snapshot_job(c, ACTOR, pending.id, job.job_id)
    with pytest.raises(JobLeaseLost):
        await repo.complete_snapshot(c, ACTOR, pending.id, facts=material(), permission_version='v1')
    assert c.rows['snapshot'][str(pending.id)]['status'] == 'preparing'


async def pending_job(repo, c, kind):
    if kind == 'snapshot':
        value = await repo.create_snapshot(c, ACTOR, SnapshotSelection(sources=['visit']), period=PERIOD, scope=SCOPE)
        job = c.job(value.id, 'snapshot')
        value = await repo.attach_snapshot_job(c, ACTOR, value.id, job.job_id)
    else:
        scheme, snapshot = await prepare(repo, c)
        value = await repo.create_run(c, ACTOR, RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
            scheme=dict(scheme_id=scheme.id, version_no=1)), rules_version='v1')
        job = c.job(value.id, 'generate')
        value = await repo.attach_run_job(c, ACTOR, value.id, job.job_id)
    return value, job


@pytest.mark.parametrize('kind', ['snapshot', 'run'])
@pytest.mark.asyncio
async def test_get_derives_safe_failure_only_after_permission_restored_without_db_update(store, kind):
    repo, c = store
    value, job = await pending_job(repo, c, kind)
    getter = repo.get_snapshot if kind == 'snapshot' else repo.get_run
    c.jobs[job.job_id].update(status='dead_letter', completed_at=NOW, last_error_detail='PRIVATE SQL OR PROVIDER TEXT')
    frozen = deepcopy(c.rows[kind][str(value.id)])
    c.denied.add('visit.read')
    before = len(c.statements)
    with pytest.raises(WorkReportError):
        await getter(c, ACTOR, value.id)
    assert not any(sql.startswith('SELECT status,completed_at FROM ops.job') for sql, _ in c.statements[before:])
    c.denied.clear()
    before = len(c.statements)
    actual = await getter(c, ACTOR, value.id)
    assert actual.status == 'failed' and actual.error.code == 'WORK_REPORT_JOB_FAILED'
    assert 'PRIVATE' not in actual.model_dump_json() and actual.error.retryable is False
    assert c.rows[kind][str(value.id)] == frozen
    assert all(not sql.lstrip().startswith(('INSERT', 'UPDATE', 'DELETE')) for sql, _ in c.statements[before:])
    if kind == 'run':
        assert actual.version_no == value.version_no and actual.finished_at == NOW
    projection_sql, args = next((sql, args) for sql, args in c.statements[before:]
                               if sql.startswith('SELECT status,completed_at FROM ops.job'))
    assert 'last_error' not in projection_sql and args == (job.job_id, ACTOR.workspace_id, str(value.id),
                                                        'work_report.snapshot' if kind == 'snapshot' else 'work_report.generate')


@pytest.mark.parametrize('kind', ['snapshot', 'run'])
@pytest.mark.parametrize('mismatch', ['workspace_id', 'aggregate_id', 'job_type', 'missing', 'running', 'failed', 'queued', 'succeeded'])
@pytest.mark.asyncio
async def test_get_does_not_adopt_other_job_or_infer_failure_from_nonterminal_queue(store, kind, mismatch):
    repo, c = store
    value, job = await pending_job(repo, c, kind)
    bound = c.jobs[job.job_id]
    bound.update(status='dead_letter', completed_at=NOW)
    if mismatch in ('workspace_id', 'aggregate_id'): bound[mismatch] = str(uuid4())
    elif mismatch == 'job_type': bound['job_type'] = 'work_report.other'
    elif mismatch == 'missing': del c.jobs[job.job_id]
    else: bound['status'] = mismatch
    actual = await (repo.get_snapshot if kind == 'snapshot' else repo.get_run)(c, ACTOR, value.id)
    assert actual.status == value.status and actual.error is None


@pytest.mark.parametrize('status', ['succeeded', 'cancelled', 'failed'])
@pytest.mark.asyncio
async def test_get_preserves_stored_run_terminal_even_when_queue_is_dead_letter(store, status):
    repo, c = store
    value, job = await pending_job(repo, c, 'run')
    c.rows['run'][str(value.id)]['status'] = status
    c.jobs[job.job_id]['status'] = 'dead_letter'
    before = len(c.statements)
    actual = await repo.get_run(c, ACTOR, value.id)
    assert actual.status == status and actual.version_no == value.version_no
    assert not any(sql.startswith('SELECT status,completed_at FROM ops.job') for sql, _ in c.statements[before:])


@pytest.mark.asyncio
async def test_get_preserves_ready_and_failed_snapshots_and_running_view_version(store):
    repo, c = store
    _, ready = await prepare(repo, c)
    ready_row = c.rows['snapshot'][str(ready.id)]
    c.jobs[ready_row['job_id']]['status'] = 'dead_letter'
    assert (await repo.get_snapshot(c, ACTOR, ready.id)).status == 'ready'
    pending, job = await pending_job(repo, c, 'snapshot')
    with lease(job):
        failed = await repo.fail_snapshot(c, ACTOR, pending.id, ReportError(code='WORK_REPORT_SOURCE_FAILED', detail='原始安全文案'))
    c.jobs[job.job_id]['status'] = 'dead_letter'
    assert (await repo.get_snapshot(c, ACTOR, failed.id)).error == failed.error
    run, job = await pending_job(repo, c, 'run')
    with lease(job): running = await repo.start_run(c, ACTOR, run.id)
    c.jobs[job.job_id].update(status='dead_letter', completed_at=NOW)
    derived = await repo.get_run(c, ACTOR, run.id)
    assert derived.status == 'failed' and derived.version_no == running.version_no
    assert c.rows['run'][str(run.id)]['status'] == 'running'


@pytest.mark.asyncio
async def test_action_scope_guard_refreshes_server_authorization_before_every_check(store):
    repo, c = store
    await repo._actions(c, ACTOR, [], SCOPE, 'work_report.write')
    assert c.statements[0][0] == 'SELECT security.authorization_refresh()'
    c.action_denied.add('work_report.write')
    before = len(c.statements)
    with pytest.raises(WorkReportError):
        await repo._actions(c, ACTOR, [], SCOPE, 'work_report.write')
    assert c.statements[before][0] == 'SELECT security.authorization_refresh()'


async def historical_scheme_document(repo, c, *, override=False, manual=False):
    scheme, snapshot = await prepare(repo, c, references=True)
    config = scheme.config.model_copy(deep=True)
    if override:
        config.style.tone = 'narrative'
        config.reference_texts[0].text = 'PRIVATE OVERRIDE ONLY'
        config.reference_texts[0].version_no = 2
    body = RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
        scheme=dict(scheme_id=scheme.id, version_no=1), scheme_override=config if override else None)
    original, _, frozen = await generate(repo, c, scheme, snapshot, body=body)
    document = await repo.get_document(c, ACTOR, original.document_id)
    if manual:
        content = document.revision.content.model_copy(update={'title': '人工修订仍使用原方案'})
        document = await repo.create_revision(c, ACTOR, document.id,
            RevisionCreate(version_no=document.version_no, content=content))
    latest = scheme.config.model_copy(deep=True)
    latest.style.tone = 'formal'
    latest.reference_texts[0].text = 'PRIVATE CATALOG VERSION TWO'
    latest.reference_texts[0].version_no = 2
    updated = await repo.update_scheme(c, ACTOR, scheme.id,
        SchemeUpdate(version_no=1, name='目录现在为V2', config=latest))
    rewrite = RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
        scheme=dict(scheme_id=document.scheme.scheme_id, version_no=document.scheme.version_no),
        document_id=document.id, version_no=document.version_no,
        base_revision_id=document.latest_revision_id, section_ids=['overview'])
    return scheme, snapshot, original, frozen, document, updated, rewrite


@pytest.mark.parametrize('override,manual', [(False, False), (True, False), (True, True)])
@pytest.mark.asyncio
async def test_rewrite_after_catalog_version_update_uses_document_frozen_scheme_including_override(store, override, manual):
    repo, c = store
    scheme, snapshot, _, frozen, document, updated, body = await historical_scheme_document(repo, c, override=override, manual=manual)
    completed, _, rewritten_input = await generate(repo, c, scheme, snapshot, body=body)
    assert rewritten_input.scheme == frozen.scheme
    assert rewritten_input.scheme.version_no == 1 and updated.version_no == 2
    assert rewritten_input.scheme.config.reference_texts[0].text == ('PRIVATE OVERRIDE ONLY' if override else 'PRIVATE WRITING REFERENCE')
    after = await repo.get_document(c, ACTOR, completed.document_id)
    assert after.scheme == document.scheme and after.revision.origin == 'rewrite'
    with pytest.raises(WorkReportError) as stale_new:
        await repo.create_run(c, ACTOR, RunCreate(snapshot_id=snapshot.id, snapshot_hash=snapshot.snapshot_hash,
            scheme=dict(scheme_id=scheme.id, version_no=1)), rules_version='v1')
    assert stale_new.value.code == 'WORK_REPORT_VERSION_CONFLICT' and stale_new.value.current_version == 2


@pytest.mark.parametrize('fault', ['scheme_id', 'scheme_version', 'missing_config', 'config_hash', 'name',
                                  'run_owner', 'run_workspace', 'run_document', 'run_status', 'revision_link'])
@pytest.mark.asyncio
async def test_rewrite_rejects_wrong_document_scheme_or_missing_bound_private_config(store, fault):
    repo, c = store
    _, _, original, _, _, _, body = await historical_scheme_document(repo, c, override=True)
    row = c.rows['run'][str(original.id)]
    if fault == 'scheme_id': body.scheme = body.scheme.model_copy(update={'scheme_id': uuid4()})
    if fault == 'scheme_version': body.scheme = body.scheme.model_copy(update={'version_no': 2})
    if fault == 'missing_config': del row['generation_input']['scheme']['config']
    if fault == 'config_hash': row['generation_input']['scheme']['config']['style']['tone'] = 'concise'
    if fault == 'name': row['generation_input']['scheme']['name'] = 'another frozen name'
    if fault == 'run_owner': row['owner_user_ref_id'] = READER.user_id
    if fault == 'run_workspace': row['workspace_id'] = str(uuid4())
    if fault == 'run_document': row['document_id'] = str(uuid4())
    if fault == 'run_status': row['status'] = 'running'
    if fault == 'revision_link': c.rows['revision'][row['revision_id']]['run_id'] = str(uuid4())
    before = len(c.statements)
    with pytest.raises(WorkReportError) as rejected:
        await repo.create_run(c, ACTOR, body, rules_version='v1')
    assert rejected.value.code == 'WORK_REPORT_VERSION_CONFLICT'
    assert len(c.rows['run']) == 1
    assert not any(sql.startswith('SELECT name,config,config_hash') for sql, _ in c.statements[before:])


@pytest.mark.parametrize('fault', ['scheme_hidden', 'source_revoked', 'version_changed'])
@pytest.mark.asyncio
async def test_historical_rewrite_does_not_bypass_current_visibility_sources_or_document_cas(store, fault):
    repo, c = store
    scheme, _, _, _, document, _, body = await historical_scheme_document(repo, c, override=True)
    if fault == 'scheme_hidden': c.hidden_schemes.add(str(scheme.id))
    if fault == 'source_revoked': c.inaccessible.add(str(UUID(int=100)))
    if fault == 'version_changed': c.rows['document'][str(document.id)]['version_no'] += 1
    with pytest.raises(WorkReportError):
        await repo.create_run(c, ACTOR, body, rules_version='v1')
    assert len(c.rows['run']) == 1


@pytest.mark.asyncio
async def test_explicit_rewrite_override_versions_reference_text_against_original_override_not_catalog(store):
    repo, c = store
    _, _, _, frozen, _, _, body = await historical_scheme_document(repo, c, override=True)
    next_config = frozen.scheme.config.model_copy(deep=True)
    next_config.reference_texts[0].text = 'PRIVATE THIRD TEXT'
    body.scheme_override = next_config
    with pytest.raises(WorkReportError, match='范文版本'):
        await repo.create_run(c, ACTOR, body, rules_version='v1')
    next_config.reference_texts[0].version_no = 3
    run = await repo.create_run(c, ACTOR, body, rules_version='v1')
    request = await repo.generation_input(c, ACTOR, run.id)
    assert request.scheme.config == next_config and request.scheme.version_no == 1
    assert request.scheme.config_hash == canonical_hash(next_config)


def test_repository_has_no_connection_transaction_or_idempotency_factory():
    import ast
    source = Path(module.__file__).read_text()
    tree = ast.parse(source)
    forbidden = {'connect', 'create_pool', 'transaction', 'execute_mutation', 'commit', 'rollback'}
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden]
