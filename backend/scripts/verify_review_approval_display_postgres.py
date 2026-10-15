# ruff: noqa: E402, E501, S108 -- Explicit isolated Unix socket and synthetic fixture.
"""Narrow V240 approval display proof; exact text and legacy six-kind compatibility."""

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import traceback
from contextlib import asynccontextmanager, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'database/tests'), str(ROOT / 'database/scripts'), str(ROOT / 'backend/scripts')]
from verify_review_targets_postgres import grants
from verify_runtime_grants_postgres import migrate_through

import sales_backend
from sales_backend.api import operations_targets, review, targets
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories import targets as target_repository
from sales_backend.services import targets as target_service
from sales_backend.services.operations import OperationsError

LEGACY = {'collection', 'recognized', 'acv', 'opportunity_count', 'visit_count', 'demo_count'}
TEXT_FIELDS = {'previous_amount_text', 'proposed_amount_text'}


async def verify(args, result):
    imported = {'sales_backend': sales_backend, 'operations_targets': operations_targets,
                'review': review, 'targets_api': targets, 'target_repository': target_repository,
                'target_service': target_service}
    origins = {key: str(Path(module.__file__).resolve()) for key, module in imported.items()}
    source_root = ROOT / 'backend/src'
    assert source_root in [Path(part).resolve() for part in os.environ.get('PYTHONPATH', '').split(os.pathsep) if part], 'Explicit worktree PYTHONPATH required'
    assert all(Path(path).is_relative_to(source_root) for path in origins.values()), origins
    result['runtime_import'] = {'cwd': str(Path.cwd()), 'python_executable': sys.executable,
        'PYTHONPATH': os.environ['PYTHONPATH'], 'origins': origins,
        'source_sha256': {key: hashlib.sha256(Path(path).read_bytes()).hexdigest() for key, path in origins.items()}}
    result['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    socket = Path(args.socket).resolve()
    assert str(socket).startswith(('/private/tmp/sb-review-pg.', '/tmp/sb-review-pg.'))
    config = dict(host=str(socket), port=args.port, user='postgres', password='', passfile='/dev/null', ssl=False)
    admin = await asyncpg.connect(database='postgres', **config)
    name, role = 'sb_approval_display_' + uuid4().hex[:12], 'sb_approval_api_' + uuid4().hex[:12]
    connection = None
    try:
        result['stage'] = 'V240 migration and limited API fixture'
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(connection, 240)
        await connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grants(connection, role)
        for typ in ('json', 'jsonb'):
            await connection.set_type_codec(typ, schema='pg_catalog', encoder=json.dumps, decoder=json.loads, format='text')
        await connection.execute('SET row_security=on')
        ws, team, owner, operator = [uuid4() for _ in range(4)]
        await connection.execute('INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)', ws, 'approval-' + str(ws))
        await connection.execute('UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1', ws)
        await connection.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'approval','Synthetic approval')", team, ws)
        actors = {}
        for user, role_code, scope in ((owner, 'sales', 'self'), (operator, 'operations', 'workspace')):
            await connection.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)', user, ws, role_code + user.hex[:8])
            await connection.execute('INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)', ws, user, role_code, scope, team if role_code == 'sales' else None)
            if role_code == 'sales':
                await connection.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, user, team)
            actors[role_code] = ActorContext(workspace_id=str(ws), user_id=str(user), role=role_code, data_scope=scope, team_ids=[str(team)] if role_code == 'sales' else [])
        anchor = await connection.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        start = date(anchor.year, (anchor.month - 1) // 3 * 3 + 1, 1)
        active = actors['operations']

        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False):
                async with connection.transaction(readonly=readonly):
                    await connection.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(connection, actor)
                    assert not await connection.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    assert await connection.fetchval('SELECT current_user') == role
                    yield connection

        db = DB()
        app = FastAPI()
        for router in (operations_targets.router, targets.router, review.router):
            app.include_router(router)
        app.dependency_overrides[get_database] = lambda: db
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=active)
        app.dependency_overrides[get_management_identity] = lambda: SimpleNamespace(actor=active)

        @app.exception_handler(OperationsError)
        async def operation_error(request, exc):
            return JSONResponse({'detail': str(exc)}, status_code=exc.status)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://synthetic') as client:
            async def request(method, path, **kwargs):
                response = await client.request(method, '/api/v1' + path, **kwargs)
                result['http_calls'].append({'method': method, 'path': path, 'status': response.status_code})
                assert response.status_code == 200, (path, response.status_code, response.text)
                return response.json()

            custom = await request('POST', '/review/metrics', json={'code': 'custom_x_count', 'name': '合成转化计数',
                'unit': '次', 'decimal_places': 2, 'definition': '合成审批精度验证，允许两位小数'})
            assert custom['decimal_places'] == 2 and custom['unit'] == '次' and custom['source_kind'] == 'manual'
            targets_by_kind = {}
            for kind in sorted(LEGACY | {'custom_x_count'}):
                targets_by_kind[kind] = await connection.fetchval("""INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end)
                  VALUES($1,$2,'person',$3,$4,100.00,$5,'quarter',$6,($6::date+interval '3 months - 1 day')::date) RETURNING id""", ws, anchor.year, owner, kind, operator, start)
            # SQL constructs numeric JSONB directly: Python float conversion is
            # deliberately downstream, matching production JSON decoding.
            batch = await connection.fetchval("""INSERT INTO crm.sales_target_batch_request(workspace_id,scope_type,user_ref_id,period_type,period_start,period_end,applicant_user_ref_id,reason,items)
              VALUES($1,'person',$2,'quarter',$3,($3::date+interval '3 months - 1 day')::date,$2,'Synthetic exact display',
                jsonb_build_array(jsonb_build_object('kind','collection','target_id',$4::text,'base_version',1,
                  'previous_amount',9999999999999999.99::numeric,'proposed_amount',9999999999999999.98::numeric),
                jsonb_build_object('kind','custom_x_count','target_id',$5::text,'base_version',1,
                  'previous_amount',4.50::numeric,'proposed_amount',12.30::numeric))) RETURNING id""", ws, owner, start, str(targets_by_kind['collection']), str(targets_by_kind['custom_x_count']))
            hidden_batch = await connection.fetchval("""INSERT INTO crm.sales_target_batch_request(workspace_id,scope_type,user_ref_id,period_type,period_start,period_end,applicant_user_ref_id,reason,items,status)
              VALUES($1,'person',$2,'quarter',$3,($3::date+interval '3 months - 1 day')::date,$2,'Synthetic custom-only history',
                jsonb_build_array(jsonb_build_object('kind','custom_x_count','previous_amount',1.20::numeric,'proposed_amount',2.40::numeric)),'rejected') RETURNING id""", ws, owner, start)
            for kind, previous, proposed in (('collection', '9999999999999999.99', '9999999999999999.98'), ('custom_x_count', '5.40', '9.70')):
                await connection.execute("""INSERT INTO crm.sales_target_change_request(workspace_id,target_id,applicant_user_ref_id,base_version,previous_amount,proposed_amount)
                  VALUES($1,$2,$3,1,$4,$5)""", ws, targets_by_kind[kind], owner, Decimal(previous), Decimal(proposed))
            stored_items = await connection.fetchval('SELECT items FROM crm.sales_target_batch_request WHERE id=$1', batch)
            sql_text = [dict(row) for row in await connection.fetch("SELECT i->>'kind' AS kind,i->>'previous_amount' AS previous_amount_text,i->>'proposed_amount' AS proposed_amount_text FROM crm.sales_target_batch_request r CROSS JOIN LATERAL jsonb_array_elements(r.items) i WHERE r.id=$1", batch)]
            expected = {r['kind']: r for r in sql_text}

            result['stage'] = 'console batch exact text from SQL JSONB'
            batches = await request('GET', '/console/target-batches')
            response_batch = next(row for row in batches['items'] if row['id'] == str(batch))
            for original, item in zip(stored_items, response_batch['items'], strict=True):
                assert set(item) == set(original) | TEXT_FIELDS
                assert {k: v for k, v in item.items() if k not in TEXT_FIELDS} == original
                assert all(item[field] == expected[item['kind']][field] for field in TEXT_FIELDS)
            by_kind = {i['kind']: i for i in response_batch['items']}
            assert by_kind['collection']['previous_amount_text'] == '9999999999999999.99'
            assert by_kind['collection']['proposed_amount_text'] == '9999999999999999.98'
            assert by_kind['custom_x_count']['previous_amount_text'] == '4.50'
            assert by_kind['custom_x_count']['proposed_amount_text'] == '12.30'
            assert any(row['id'] == str(hidden_batch) for row in batches['items'])
            result['checks'].append('console batches preserve every original item field and add SQL-extracted exact amount text, including max numeric cents and custom count scale 2')

            result['stage'] = 'console legacy request numeric text'
            requests = await request('GET', '/console/target-requests')
            requests_by_kind = {r['kind']: r for r in requests['items']}
            assert requests_by_kind['collection']['previous_amount_text'] == '9999999999999999.99'
            assert requests_by_kind['collection']['proposed_amount_text'] == '9999999999999999.98'
            assert requests_by_kind['custom_x_count']['previous_amount_text'] == '5.40'
            assert requests_by_kind['custom_x_count']['proposed_amount_text'] == '9.70'
            assert all({'previous_amount', 'proposed_amount', *TEXT_FIELDS} <= r.keys() for r in requests['items'])
            result['checks'].append('console requests retain original numeric fields and add exact numeric::text fields')

            dictionary = await request('GET', '/review/metrics')
            metric = next(m for m in dictionary['items'] if m['code'] == 'custom_x_count')
            assert metric['unit'] == '次' and metric['decimal_places'] == 2
            active = actors['sales']
            legacy = await request('GET', '/targets', params={'scope': 'self', 'period_type': 'quarter', 'anchor_date': str(anchor)})
            assert {r['kind'] for r in legacy['items']} == LEGACY
            assert [r['kind'] for r in legacy['pending_requests']] == ['collection']
            assert legacy['pending_total'] == 1 and legacy['pending_batch_total'] == 1
            assert len(legacy['pending_batches']) == len(legacy['recent_batches']) == 1
            for row in legacy['pending_requests']:
                assert not TEXT_FIELDS & row.keys()
            for rows in (legacy['pending_batches'], legacy['recent_batches']):
                assert [r['id'] for r in rows] == [str(batch)]
                assert [i['kind'] for i in rows[0]['items']] == ['collection']
                assert not TEXT_FIELDS & rows[0]['items'][0].keys()
                assert rows[0]['items'][0] == stored_items[0]
            result['checks'].append('legacy /targets preserves all six kinds, excludes custom-only requests/history, filters mixed batch items and adds no approval text fields')
            result['response_samples'] = {'console_batches': batches, 'console_requests': requests,
                'custom_dictionary_entry': metric, 'legacy_targets': legacy, 'sql_exact_text': sql_text}
        result['source_drift'] = [key for key, path in origins.items() if hashlib.sha256(Path(path).read_bytes()).hexdigest() != result['runtime_import']['source_sha256'][key]]
        assert not result['source_drift']
        result['sql_sha256'] = hashlib.sha256((ROOT / 'database/migrations/V240__review_metric_dictionary.sql').read_bytes()).hexdigest()
        result['boundary'] = 'Private synthetic PostgreSQL V240, exact API runtime grants and actual ASGI routes; synthetic trusted identity, no external model, browser UI or production claim.'
        result['status'], result['stage'] = 'passed', 'complete'
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
        result['own_database_and_role_cleaned'] = True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket', required=True)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = {'status': 'running', 'stage': 'import provenance', 'checks': [], 'http_calls': []}
    try:
        asyncio.run(verify(args, result))
    except Exception as exc:
        result['status'] = 'failed'
        result['failure'] = {'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()}
        raise
    finally:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n')
        print(json.dumps({key: result[key] for key in ('status', 'stage', 'checks', 'http_calls')}, ensure_ascii=False))
