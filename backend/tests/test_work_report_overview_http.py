"""ASGI/Worker with real orchestration and synthetic SQL; no DB/RLS/model proof."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sales_backend.api import work_reports as api
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.contracts.work_report import GenerationResult
from sales_backend.contracts.work_report_overview import overview_missing_reasons
from sales_backend.domain.work_report import WorkReportError, default_scheme
from sales_backend.domain.work_report_facts import FactAssembler
from sales_backend.job_context import JobLease, current_job_lease
from sales_backend.services import work_report_overview as subject
from sales_backend.services.work_report_jobs import WorkReportSnapshotHandler, WorkReportGenerationHandler
from tests.test_work_report_http import DB, post
from tests.test_work_report_meeting_http import MeetingSQL
from tests.test_work_report_meeting_facts import TEAM
from tests.test_work_report_repository import ACTOR, READER

CLOCK = datetime.now(timezone.utc)


class OverviewSQL(MeetingSQL):
    def __init__(self):
        super().__init__()
        self.permission_version = 'v1'
        self.overview_queries = []

    async def execute(self, sql, *args):
        result = await super().execute(sql, *args)
        if sql.strip().startswith('INSERT INTO agent.work_report_document'):
            # Explicit V236 trigger double. PostgreSQL is separately required.
            parent = self.rows['snapshot'][args[3]]
            if parent['payload'].get('overview_facts'):
                self.rows['document'][args[0]]['context']['overview_facts'] = deepcopy(
                    parent['payload']['overview_facts']['facts'])
        return result

    async def fetchval(self, sql, *args):
        if sql == 'SELECT transaction_timestamp()':
            return CLOCK
        if "current_setting('transaction_read_only')" in sql:
            return 'on' if self.readonly else 'off'
        if "WHERE version='V236'" in sql:
            return True
        if 'authorization_snapshot' in sql:
            data = await super().fetchval(sql, *args)
            data['permission_version'] = self.permission_version
            if 'agent.work_report' not in self.denied:
                data['grants'].append(dict(permission_code='agent.work_report', effect='allow', scope_code='workspace',
                                           team_ids=[], source_code='test', source_id='fixed'))
            return data
        return await super().fetchval(sql, *args)

    async def fetch(self, sql, *args):
        self.overview_queries.append((sql, args))
        if '/* work_report_task_activity_v1 */' in sql:
            return []
        if 'FROM platform.team t' in sql:
            return [dict(id=TEAM, name='可见团队')] if not args[3] or self.managed else []
        result = await super().fetch(sql, *args)
        if 'membership_state' in sql and args[0] == 'self':
            result = [r for r in result if r['id'] == args[1]]
        return result

    async def fetchrow(self, sql, *args):
        result = await super().fetchrow(sql, *args)
        if sql.strip().startswith('UPDATE agent.work_report_snapshot') and "status='ready'" in sql:
            if args[3].get('overview_facts'):
                result['source_manifest'].extend(deepcopy(args[3]['overview_dependencies']))
                self.rows['snapshot'][args[0]]['source_manifest'] = deepcopy(result['source_manifest'])
        return result


@pytest_asyncio.fixture
async def overview_http(monkeypatch):
    import socket
    import asyncpg
    def blocked(*a, **kw):
        raise AssertionError('offline overview test opened network')
    monkeypatch.setattr(asyncpg, 'connect', blocked)
    monkeypatch.setattr(asyncpg, 'create_pool', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    db = DB()
    db.connection = OverviewSQL()
    db.settings = SimpleNamespace(access_token_secret='synthetic-overview-signing-key-0123456789',
                                   access_token_issuer='test', access_token_audience='test-client')
    state = SimpleNamespace(actor=ACTOR)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_identity] = lambda: state
    @app.exception_handler(WorkReportError)
    async def error(_, exc):
        return JSONResponse(status_code=exc.status, content=dict(code=exc.code, detail=exc.detail))
    async def standard_facts(connection, selection, scope, *, period, data_as_of, **kw):
        # The ordinary fact collector has its own tests. This explicit double
        # isolates the bridge and creates actual typed coverage for this profile.
        assert connection.readonly and connection.isolation == 'repeatable_read'
        assembler = FactAssembler(period=period, data_as_of=data_as_of)
        for kind in selection.sources:
            if kind != 'actual':
                assembler.add(kind, [])
        prepared = assembler.finish()
        if selection.facts_contract:
            from sales_backend.domain.work_report_weekly import assemble_weekly
            prepared = assemble_weekly(prepared, rows_by_type={k: [] for k in selection.sources},
                                      selection=selection, period=period, histories={})
        return prepared
    monkeypatch.setattr(subject, 'prepare_facts', standard_facts)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://offline.invalid') as http:
        yield http, db, state


async def get(http, **selection):
    return await http.get('/api/v1/work-reports/overview', params={**dict(request_id=str(uuid4()),
        period_kind='week', anchor_date='2026-10-09', scope_kind='self'), **selection})


@pytest.mark.asyncio
async def test_read_only_member_and_team_do_not_require_write_or_create_anything(overview_http):
    http, db, _ = overview_http
    c = db.connection
    c.denied.update({'work_report.write', 'agent.work_report'})
    c.managed = False
    options = (await http.get('/api/v1/work-reports/overview/options')).json()
    assert options['available_scope_kinds'] == ['self', 'member', 'team']
    assert options['generation']['available_scope_kinds'] == []
    for selection in [dict(scope_kind='member', member_id=READER.user_id), dict(scope_kind='team', team_id=TEAM)]:
        response = await get(http, **selection)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['scope']['kind'] == selection['scope_kind']
        assert data['capabilities']['generation_unavailable_reason'] == 'READ_ONLY'
        assert data['generation_binding'] is None
    assert not c.jobs and all(not rows for rows in c.rows.values())
    for sql, args in c.overview_queries:
        if any(marker in sql for marker in ('membership_state', 'AS forecast_events', 'FROM crm.customer_ownership_event e',
                                            'FROM activity.visit v JOIN crm.customer', 'FROM workflow.task t WHERE')):
            assert args[-1] is False and 'work_report.read' in sql
    member_query = next(args for sql, args in c.overview_queries if 'membership_state' in sql)
    assert member_query[1] == READER.user_id  # selected member, never impersonate actor


@pytest.mark.asyncio
async def test_response_binds_request_selection_and_nonmanaged_team_cannot_generate(overview_http):
    http, db, _ = overview_http
    db.connection.managed = False
    request_id = str(uuid4())
    query = dict(request_id=request_id, period_kind='week', anchor_date='2026-10-09', scope_kind='team', team_id=TEAM)
    response = await http.get('/api/v1/work-reports/overview', params=query)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['request_id'] == request_id and data['selection']['scope']['team_id'] == TEAM
    assert data['capabilities']['generation_unavailable_reason'] == 'SCOPE_NOT_GENERATABLE'
    query['request_id'] = str(uuid4())
    repeated = (await http.get('/api/v1/work-reports/overview', params=query)).json()
    assert repeated['selection_hash'] == data['selection_hash'] and repeated['request_id'] != request_id


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [dict(scope_kind='member'), dict(scope_kind='self', team_id=TEAM),
                                dict(generation_profile='sales_week_six', period_kind='day')])
async def test_selector_errors_are_422_without_writes(overview_http, bad):
    http, db, _ = overview_http
    query = dict(request_id=str(uuid4()), period_kind='week', scope_kind='self')
    query.update(bad)
    assert (await http.get('/api/v1/work-reports/overview', params=query)).status_code == 422
    assert not db.connection.jobs


async def bound_snapshot(http):
    response = await get(http)
    assert response.status_code == 200, response.text
    data = response.json()
    binding = data['generation_binding']
    assert binding is not None, data
    body = {**binding['snapshot_selection'], 'overview_binding': binding['token']}
    return data, body


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['token', 'actor', 'selection', 'expired', 'permission'])
async def test_invalid_or_stale_binding_does_not_create_job(overview_http, change):
    http, db, state = overview_http
    _, body = await bound_snapshot(http)
    expected = 422
    if change == 'token':
        body['overview_binding'] = 'invalid'
    elif change == 'actor':
        state.actor = READER
    elif change == 'selection':
        body['period']['anchor_date'] = '2026-10-08'
    elif change == 'permission':
        db.connection.permission_version = 'v2'
        expected = 409
    else:
        claims = jwt.decode(body['overview_binding'], options={'verify_signature': False})
        claims['exp'] = int(CLOCK.timestamp()) - 1
        body['overview_binding'] = jwt.encode(claims, db.settings.access_token_secret, algorithm='HS256')
        expected = 409
    result = await http.post('/api/v1/work-reports/snapshots', json=body, headers={'Idempotency-Key': str(uuid4())})
    assert result.status_code == expected, result.text
    assert not db.connection.jobs and not db.connection.rows['snapshot']


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [False, True])
async def test_worker_freezes_original_materials_or_stops_before_completion(overview_http, changed):
    http, db, _ = overview_http
    data, body = await bound_snapshot(http)
    response = await http.post('/api/v1/work-reports/snapshots', json=body, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 202, response.text
    ident = response.json()['id']
    job_id = next(iter(db.connection.jobs))
    if changed:
        db.connection.material['opportunities'][0]['name'] += ' changed'
    lease = current_job_lease.set(JobLease(job_id, db.connection.jobs[job_id]['token']))
    try:
        if changed:
            with pytest.raises(WorkReportError, match='概览已过期'):
                await WorkReportSnapshotHandler(db).handle(ident, ACTOR)
            assert job_id not in db.connection.effects
            assert db.connection.rows['snapshot'][ident]['status'] == 'preparing'
        else:
            await WorkReportSnapshotHandler(db).handle(ident, ACTOR)
            row = db.connection.rows['snapshot'][ident]
            assert row['status'] == 'ready' and job_id in db.connection.effects
            assert row['payload']['overview_facts']['facts'] == data['facts']
            assert row['payload']['data_as_of'] == data['data_as_of']
            assert row['payload']['overview_dependencies']
            assert 'overview_binding' not in row['payload']
    finally:
        current_job_lease.reset(lease)


async def handle(db, state, kind, ident, handler):
    c = db.connection
    job_id = next(k for k, v in c.jobs.items() if v['job_type'] == 'work_report.' + kind and v['aggregate_id'] == ident)
    token = current_job_lease.set(JobLease(job_id, c.jobs[job_id]['token']))
    try:
        await handler.handle(ident, state.actor)
    finally:
        current_job_lease.reset(token)
    assert job_id in c.effects


async def saved_overview(http, db, state, *, weekly=False):
    response = await get(http, generation_profile='sales_week_six' if weekly else 'general')
    assert response.status_code == 200, response.text
    overview = response.json()
    binding = overview['generation_binding']
    assert binding, overview
    snapshot = await post(http, '/snapshots', {**binding['snapshot_selection'], 'overview_binding': binding['token']}, expected=202)
    await handle(db, state, 'snapshot', snapshot['id'], WorkReportSnapshotHandler(db))
    snapshot = (await http.get('/api/v1/work-reports/snapshots/' + snapshot['id'])).json()
    scheme = await post(http, '/schemes', dict(name='同材料报告', config=default_scheme(sales_week=weekly).model_dump(mode='json')), expected=201)
    run = await post(http, '/runs', dict(snapshot_id=snapshot['id'], snapshot_hash=snapshot['snapshot_hash'],
        scheme=dict(scheme_id=scheme['id'], version_no=scheme['version_no'])), expected=202)
    async def generate(request, *, assert_active, **kwargs):
        await assert_active()
        raw = request.model_dump(mode='json')
        assert 'overview_binding' not in str(raw) and 'overview_dependencies' not in raw
        assert request.overview_facts.facts.model_dump(mode='json') == overview['facts']
        missing = set(overview_missing_reasons(request.overview_facts))
        missing.update(r for c in request.coverage for r in c.missing_reasons)
        if request.sales_week_facts:
            missing.update(r for s in request.sales_week_facts.sections for r in s.missing_reasons)
        # Synthetic generator output isolates persistence. No provider is called.
        content = dict(title='冻结概览报告', sections=[dict(id=s.id, title=s.title, items=[])
            for s in request.scheme.config.sections if s.enabled], missing_data=sorted(missing))
        content['sections'][0]['items'] = [dict(kind='fact', text='同一份冻结变化事实',
            source_refs=[next(iter(request.overview_facts.references.values()))])]
        return GenerationResult(status='generated', provider='direct', snapshot_id=request.snapshot_id,
            snapshot_hash=request.snapshot_hash, scheme_id=request.scheme.scheme_id, scheme_version=request.scheme.version_no,
            coverage_status='partial' if any(c.status == 'partial' for c in request.coverage) else 'complete',
            content=content, trace=dict(prompt_version='synthetic.output', elapsed_ms=0, chunks_total=1, chunks_completed=1))
    await handle(db, state, 'generate', run['id'], WorkReportGenerationHandler(db, generate=generate))
    run = (await http.get('/api/v1/work-reports/runs/' + run['id'])).json()
    response = await http.get('/api/v1/work-reports/documents/' + run['document_id'])
    assert response.status_code == 200, response.text
    return overview, snapshot, run, response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('weekly', [False, True])
async def test_frozen_document_edit_share_export_and_revoked_dependency(overview_http, weekly):
    http, db, state = overview_http
    if weekly:
        state.actor = type(ACTOR).model_validate({**ACTOR.model_dump(), 'role': 'sales'})
    owner = state.actor
    overview, snapshot, run, document = await saved_overview(http, db, state, weekly=weekly)
    assert document['overview_facts'] == overview['facts']
    assert len(document['revision']['content']['sections']) == (6 if weekly else 4)
    db.connection.material['opportunities'][0]['name'] = '后续实时变更'
    edit = deepcopy(document['revision']['content'])
    edit['sections'][0]['items'] = [dict(kind='manual', text='手工保存补充')]
    changed = await post(http, f"/documents/{document['id']}/revisions", dict(version_no=document['version_no'], content=edit))
    assert changed['overview_facts'] == overview['facts']
    shared = await post(http, f"/documents/{document['id']}/share", dict(version_no=changed['version_no'], confirm=True,
        audience=dict(kind='members', member_ids=[READER.user_id])))
    state.actor = READER
    received = await http.get('/api/v1/work-reports/documents/' + document['id'])
    assert received.status_code == 200, received.text
    assert received.json()['overview_facts'] == overview['facts']
    assert received.json()['shared_revision_id'] == shared['shared_revision_id']
    exported = await http.get(f"/api/v1/work-reports/documents/{document['id']}/export")
    assert exported.status_code == 200 and '手工保存补充' in exported.json()['text']
    # Source exists only in private overview dependencies, not ordinary sources.
    db.connection.inaccessible.add(owner.user_id)
    for path in [f"/documents/{document['id']}", f"/documents/{document['id']}/export"]:
        assert (await http.get('/api/v1/work-reports' + path)).status_code == 403
    state.actor = owner
    for path in [f"/snapshots/{snapshot['id']}", f"/runs/{run['id']}"]:
        assert (await http.get('/api/v1/work-reports' + path)).status_code == 403


def test_all_report_handlers_have_real_application_permission_routes():
    from sales_backend.domain.route_permissions import route_permission
    for route in api.router.routes:
        permission = route_permission('work_reports', route.endpoint.__name__)
        if route.endpoint.__name__ in {'overview_options', 'overview_read', 'meeting_options', 'meeting_snapshot'}:
            assert permission == 'work_report.read'
        else:
            assert permission


@pytest.mark.asyncio
async def test_bound_post_requires_current_agent_permission_even_with_same_permission_version(overview_http):
    http, db, _ = overview_http
    _, body = await bound_snapshot(http)
    db.connection.denied.add('agent.work_report')
    response = await http.post('/api/v1/work-reports/snapshots', json=body, headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 403 and not db.connection.jobs
