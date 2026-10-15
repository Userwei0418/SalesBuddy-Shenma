"""Scoped risk acceptance; backend saves pending advice, no human resolve or CRM writes.

Requires an operator-approved scope JSON with account, actor_id and safe_visit_ids.
Facts remain the real current user's complete application facts; no test filtering
or invented model input. A changed visit set or non-test visit aborts before sending
business input. Existing visible risks may be read, but only approved visit risks
may change. Credentials and business text are never written to evidence.
"""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import asyncpg
import httpx

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


async def run(args):
    scope = json.loads(Path(args.scope_file).read_text())
    assert scope['account'] == 'XS001'
    if not args.preflight_only:
        assert scope.get('approval_status') == 'approved', 'Live scope is not approved'
    allowed = set(scope['safe_visit_ids'])
    assert allowed and all(str(UUID(v)) == v for v in allowed)
    output = Path(args.output)
    with output.open('x') as f:
        f.write('{}\n')
    state = {'scenario': args.scenario, 'checks': [], 'ids': {}, 'preflight_only': args.preflight_only,
             'started_at': datetime.now(UTC).isoformat()}
    clients = {}
    connection = None

    def save():
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str) + '\n')

    def check(name, passed):
        state['checks'].append({'name': name, 'passed': bool(passed)})
        save()
        print(json.dumps(state['checks'][-1]), flush=True)
        if not passed:
            raise AssertionError(name)

    async def request(account, method, path, expected=200, **kwargs):
        response = await clients[account].request(method, path, **kwargs)
        check(f'{account} {method} {path} expected {expected}, actual {response.status_code}',
              response.status_code == expected)
        return response.json() if response.content else None

    try:
        connection = await asyncpg.connect(host='/tmp', database='sales_saas', user='postgres', command_timeout=10)  # noqa: S108
        await _initialize_connection(connection)
        async with connection.transaction(readonly=True):
            await connection.execute('SET LOCAL ROLE ' + args.role)
            check('restricted role without RLS bypass', not await connection.fetchval(
                'SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user',
            ))
            actors = {}
            for account in ('XS001', 'XS002'):
                actors[account] = (await IdentityRepository().find_actor_by_account(
                    connection, workspace_external_id='demo-sales-workspace', account_code=account,
                )).context
            actor = actors['XS001']
            check('configured test actor', actor.user_id == scope['actor_id'] and actor.role.value == 'sales')
            await set_request_context(connection, actor)
            facts = await AgentFactsLoader(None)._load_personal_risk_facts(connection, RunInput(
                str(uuid4()), 'preflight', '查看个人风险', 'personal_risks', None, actor,
            ))
            visits = facts['visits']
            check('exact scoped visit set still current', {v['source_visit_id'] for v in visits} == allowed)
            check('all source visits explicitly synthetic', all(
                any(marker in (v.get('customer_name') or '') for marker in ('验收', '联调', '测试', '合成', '演示'))
                for v in visits
            ))
            customers = sorted({v['customer_id'] for v in visits})
            state.update(approved_visit_count=len(allowed), existing_open_risk_count=len(facts['existing_open_risks']),
                         facts_fingerprint=fingerprint(facts['visits']))

            async def crm_state():
                values = []
                for query in (
                    'SELECT * FROM crm.customer WHERE id=ANY($1::uuid[]) ORDER BY id',
                    'SELECT * FROM crm.opportunity WHERE customer_id=ANY($1::uuid[]) ORDER BY id',
                ):
                    rows = await connection.fetch(query, customers)
                    values.extend(dict(row) for row in rows)
                return fingerprint(values), await connection.fetchval('SELECT count(*) FROM workflow.notification')

            async def risk_state():
                rows = await connection.fetch(
                    'SELECT * FROM insight.risk WHERE owner_user_ref_id=$1::uuid AND deleted_at IS NULL ORDER BY id',
                    actor.user_id,
                )
                return {str(row['id']): dict(row) for row in rows}

            before_crm, before_risks = await crm_state(), await risk_state()
            state['crm_fingerprint'] = before_crm[0]
            protected = {rid: fingerprint(row) for rid, row in before_risks.items()
                         if str(row['source_visit_id']) not in allowed or row['status'] in ('resolved', 'accepted')}
            state['protected_risk_count'] = len(protected)
            if args.preflight_only:
                state.update(preflight_passed=True, real_business_verified=False)
                return
            for account in ('XS001', 'XS002'):
                clients[account] = httpx.AsyncClient(base_url=args.base_url, timeout=15)
                session = await request(account, 'POST', '/auth/session', json={
                    'account_code': account, 'workspace': 'demo-sales-workspace',
                    'client': {'purpose': 'personal-risk-acceptance', 'label': args.scenario},
                })
                clients[account].headers['Authorization'] = 'Bearer ' + session['access_token']
                check(account + ' own sales identity', session['actor']['user_id'] == actors[account].user_id)
            conversation = await request('XS001', 'POST', '/conversations', 201, json={'mode': 'personal_risks'})
            cid = state['ids']['conversation_id'] = conversation['id']
            path = f'/conversations/{cid}/messages'
            body = {'text': '查看个人风险', 'client_message_id': 'risk-acceptance-' + uuid4().hex,
                    'input_source': 'text'}
            state['submitted_at'] = datetime.now(UTC).isoformat()
            started = monotonic()
            accepted = await request('XS001', 'POST', path, 202, json=body)
            run_id = state['ids']['run_id'] = accepted['run_id']
            save()
            replay = await request('XS001', 'POST', path, 202, json=body)
            check('message replay returns same run', replay['run_id'] == run_id)
            await request('XS001', 'POST', path, 409, json={**body, 'text': '不同内容'})
            deadline = monotonic() + 75
            while monotonic() < deadline:
                response = await clients['XS001'].get(f'/agent/runs/{run_id}')
                check('run polling readable', response.status_code == 200)
                public = response.json()
                if public['status'] in {'succeeded', 'failed', 'cancelled', 'waiting_human'}:
                    break
                await asyncio.sleep(1)
            state.update(elapsed_ms=round((monotonic() - started) * 1000), status=public['status'])
            check('risk analysis completed', public['status'] == 'succeeded')
            check('customer and opportunity facts and notifications unchanged', await crm_state() == before_crm)
            after_risks = await risk_state()
            check('no existing risk removed', set(before_risks) <= set(after_risks))
            check('closed and out-of-scope risks unchanged', all(
                rid in after_risks and fingerprint(after_risks[rid]) == value for rid, value in protected.items()
            ))
            changed = {rid: row for rid, row in after_risks.items()
                       if rid not in before_risks or fingerprint(row) != fingerprint(before_risks[rid])}
            check('bounded nonempty risk result materialized', 1 <= len(changed) <= 8)
            state['ids']['risk_ids'] = sorted(changed)
            visit_map = {v['source_visit_id']: v for v in visits}
            check('backend retains source ownership and nonterminal status', all(
                str(row['source_visit_id']) in allowed and str(row['owner_user_ref_id']) == actor.user_id
                and row['status'] == (before_risks[rid]['status'] if rid in before_risks else 'pending')
                and str(row['customer_id']) == visit_map[str(row['source_visit_id'])]['customer_id']
                and str(row['source_run_id']) == run_id
                for rid, row in changed.items()
            ))
            events = await connection.fetch(
                "SELECT risk_id::text,event_type FROM insight.risk_event WHERE payload->>'run_id'=$1", run_id,
            )
            check('one backend event per changed risk', len(events) == len(changed)
                  and {e['risk_id'] for e in events} == set(changed)
                  and all(e['event_type'] in {'detected', 'reanalyzed'} for e in events))
            result = public['result']
            open_risks = {rid: row for rid, row in after_risks.items()
                          if row['status'] in {'new', 'pending', 'in_progress', 'escalated'}}
            check('public card matches persisted risk list', {r['risk_id'] for r in result['rows']} == set(open_risks)
                  and result['metrics'][0]['value'] == str(len(open_risks)) and result['artifact_id'] is None)
            check('route stays private', all(k not in result for k in (
                'provider', 'inference_route', 'fallback_reason',
            )))
            jobs = await connection.fetch(
                'SELECT id::text,status,attempts FROM ops.job WHERE aggregate_id=$1::uuid', run_id,
            )
            for _ in range(10):
                if len(jobs) == 1 and jobs[0]['status'] == 'succeeded':
                    break
                await asyncio.sleep(.1)
                jobs = await connection.fetch(
                    'SELECT id::text,status,attempts FROM ops.job WHERE aggregate_id=$1::uuid', run_id,
                )
            check('single completed job without replay', len(jobs) == 1
                  and jobs[0]['status'] == 'succeeded' and jobs[0]['attempts'] == 1)
            state['ids']['job_id'] = jobs[0]['id']
            check('single completion receipt and answer', await connection.fetchval(
                'SELECT count(*) FROM ops.job_effect WHERE job_id=$1::uuid', jobs[0]['id'],
            ) == 1 and await connection.fetchval(
                'SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid', run_id,
            ) == 1)
            context = await connection.fetchval('SELECT business_context FROM agent.run WHERE id=$1::uuid', run_id)
            trace = state['inference_route'] = context.get('inference_route')
            attempts = await connection.fetch(
                "SELECT provider_code,status,http_status,error_code,operation_id::text, "
                "request_snapshot->>'network_dispatch_suppressed' AS dispatch_suppressed "
                "FROM agent.model_invocation WHERE run_id=$1::uuid AND record_kind='provider_attempt' "
                "ORDER BY started_at", run_id,
            )
            state['attempts'] = [dict(a) for a in attempts]
            check('one operation and final real provider succeeded', attempts
                  and len({a['operation_id'] for a in attempts}) == 1
                  and attempts[-1]['status'] == 'succeeded' and attempts[-1]['http_status'] == 200)
            if args.scenario == 'off':
                check('original route independently completes', trace is None
                      and all(a['provider_code'] == 'senseaudio' for a in attempts))
            else:
                check('risk provenance matches actual provider', trace
                      and all(r['model_ref'] == trace['model_ref'] for r in changed.values()))
                if args.scenario == 'blocked':
                    check('controlled block same-operation original fallback', trace['provider'] == 'senseaudio'
                          and trace['fallback_reason'] == 'ControlledPlatformBlock'
                          and attempts[0]['dispatch_suppressed'] == 'true' and attempts[0]['http_status'] is None)
                else:
                    check('real platform request dispatched', attempts[0]['provider_code'] == 'agent_platform'
                          and attempts[0]['dispatch_suppressed'] == 'false')
                    state['platform_accepted'] = trace['provider'] == 'agent_platform'
                    check('platform accepted or validated fallback', state['platform_accepted'] or (
                        trace['provider'] == 'senseaudio'
                        and trace['fallback_reason'] in {'TimeoutError', 'InvalidAgentResult'}
                    ))
            await request('XS002', 'GET', f'/agent/runs/{run_id}', 404)
            for rid in changed:
                await request('XS002', 'GET', f'/risks/{rid}', 404)
            check('other sales cannot read risk conversation', await request('XS002', 'GET', path) == [])
        state['passed'] = True
    except Exception as exc:
        state.update(passed=False, error_type=type(exc).__name__)
        raise
    finally:
        for account, client in clients.items():
            try:
                if 'Authorization' in client.headers:
                    check(account + ' own session revoked', (await client.post('/auth/logout')).status_code == 204)
            finally:
                await client.aclose()
        if connection:
            await connection.close()
        state['finished_at'] = datetime.now(UTC).isoformat()
        save()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=('normal', 'off', 'blocked', 'production'), required=True)
    parser.add_argument('--scope-file', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--base-url', default='https://www.ericepc.com/api/v1')
    parser.add_argument('--role', choices=('sales_app', 'sales_runtime'), default='sales_app')
    asyncio.run(run(parser.parse_args()))
