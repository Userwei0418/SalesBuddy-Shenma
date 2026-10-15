"""Narrow normal -> recovery compatibility; synthetic SQL, no model/network.

Run with exact normal source in PYTHONPATH and REPORT_RECOVERY_SOURCE pointing
to the independently frozen recovery source tree. All shared module bytes must
match; only API/Worker and the fixed recovery helper may differ.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
import importlib.util
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.domain.work_report import WorkReportError
from tests.test_work_report_http import client, pipeline  # noqa: F401 fixture
from tests.test_work_report_overview_http import overview_http, saved_overview  # noqa: F401 fixture


def load(name, relative):
    root = Path(os.environ['REPORT_RECOVERY_SOURCE']) / 'backend/src/sales_backend'
    spec = importlib.util.spec_from_file_location(name, root / relative)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recovery():
    if 'REPORT_RECOVERY_SOURCE' not in os.environ:
        pytest.skip('Requires an explicitly frozen REPORT_RECOVERY_SOURCE for normal-to-recovery compatibility')
    import sales_backend.api.work_reports as normal
    normal_root = Path(normal.__file__).resolve().parents[1]
    root = Path(os.environ['REPORT_RECOVERY_SOURCE']) / 'backend/src/sales_backend'
    files = {p.relative_to(normal_root).as_posix():p.read_bytes() for p in normal_root.rglob('*.py')}
    others = {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*.py')}
    assert {p for p in set(files)|set(others) if files.get(p)!=others.get(p)} == {
        'api/work_reports.py', 'worker.py', 'services/work_report_recovery.py'}
    helper = load('sales_backend.services.work_report_recovery', 'services/work_report_recovery.py')
    return load('_tested_recovery_api', 'api/work_reports.py'), load('_tested_recovery_worker', 'worker.py'), helper


def app_for(router, db, state):
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[get_database] = lambda: db
    app.dependency_overrides[get_identity] = lambda: state
    @app.exception_handler(WorkReportError)
    async def error(_, exc):
        return JSONResponse(status_code=exc.status, content={'code':exc.code, 'detail':exc.detail})
    @app.post('/other-business')
    async def other():
        return {'unchanged':True}
    return app


@pytest.mark.asyncio
async def test_recovery_reads_legacy_report(client, recovery):
    http, db, state = client
    _, _, _, document = await pipeline(http, db)
    assert not document.get('overview_facts')
    before = deepcopy((db.connection.rows, db.connection.jobs, db.connection.events))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(recovery[0].router,db,state)), base_url='http://offline.invalid') as recovered:
        read = await recovered.get('/api/v1/work-reports/documents/' + document['id'])
        assert read.status_code == 200 and read.json()['revision'] == document['revision']
        assert (await recovered.get('/api/v1/work-reports/documents/' + document['id'] + '/export')).status_code == 200
    assert (db.connection.rows, db.connection.jobs, db.connection.events) == before


@pytest.mark.asyncio
async def test_recovery_new_overview_refs_read_and_all_report_writes_paused(overview_http, recovery):
    http, db, state = overview_http
    _, snapshot, run, document = await saved_overview(http, db, state)
    refs = document['revision']['content']['sections'][0]['items'][0]['source_refs']
    assert any(ref.startswith('overview:') for ref in refs)
    before = deepcopy((db.connection.rows, db.connection.jobs, db.connection.events))
    app = app_for(recovery[0].router, db, state)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://offline.invalid') as recovered:
        for path in ('documents/'+document['id'], 'snapshots/'+snapshot['id'], 'runs/'+run['id']):
            read = await recovered.get('/api/v1/work-reports/'+path)
            assert read.status_code == 200, read.text
            if path.startswith('documents/'):
                assert read.json()['revision'] == document['revision']
                assert read.json()['overview_facts'] == document['overview_facts']
        # Every actual mutation route is fenced before business code/queue writes.
        for route in recovery[0].router.routes:
            for method in route.methods - {'GET','HEAD','OPTIONS'}:
                path = re.sub(r'\{[^}]+\}', str(uuid4()), route.path)
                response = await recovered.request(method,path,json={},headers={'Idempotency-Key':str(uuid4())})
                assert response.status_code == 503 and response.json()['code'] == 'WORK_REPORT_RECOVERY_READONLY'
        assert (await recovered.post('/other-business')).json() == {'unchanged':True}
        assert (db.connection.rows,db.connection.jobs,db.connection.events) == before
        db.connection.inaccessible.add(state.actor.user_id)
        assert (await recovered.get('/api/v1/work-reports/documents/'+document['id'])).status_code == 403


@pytest.mark.asyncio
async def test_recovery_worker_retains_report_jobs_and_serves_other_lanes(recovery):
    pending = ['work_report.snapshot','work_report.generate','visit.import','agent.run','business.advice','sales_competency.review']
    handled = []
    class DB:
        settings = SimpleNamespace(worker_id='synthetic',worker_lock_seconds=60)
        @asynccontextmanager
        async def connection(self):
            yield object()
    worker = recovery[1].Worker(DB())
    async def claim(connection, *, job_types, exclude_types, **kwargs):
        for kind in pending:
            if (kind not in job_types) if exclude_types else (kind in job_types):
                pending.remove(kind); return SimpleNamespace(job_type=kind)
    async def execute(job):
        handled.append(job.job_type); return True
    worker.jobs = SimpleNamespace(claim=claim)
    worker._execute_claimed_job = execute
    assert not await worker.run_once(job_types=('work_report.snapshot','work_report.generate'))
    for lane in worker.lanes():
        await worker.run_once(job_types=lane.job_types,exclude_types=lane.exclude_types)
    await worker.run_once()  # Unfiltered callers are fenced too.
    assert set(handled) == {'visit.import','agent.run','business.advice','sales_competency.review'}
    assert pending == ['work_report.snapshot','work_report.generate']
