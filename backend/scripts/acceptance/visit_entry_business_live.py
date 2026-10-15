"""Scoped visit extraction/review acceptance; never confirms or archives a visit.

The approved scope identifies the sales actor and synthetic customer. Preflight
uses read-only database access and opens no application sessions. Normal/off/
blocked/production routing is controlled separately by the operator. Raw text,
model answers and credentials are not written to evidence.
"""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.visit_contract import PROMPT_VERSION
from sales_backend.domain.visit_review import review_text
from sales_backend.repositories.identity import IdentityRepository


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def sample(case, customer_name):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    due = (today + timedelta(days=3)).isoformat()
    fields = {
        "customer_name": customer_name, "contact_name": "王经理", "interaction_at": today.isoformat(),
        "created_date": today.isoformat(), "is_first_visit": case == "first",
        "follow_up_record": "客户现场测试5条退款问题，4条准确，1条缺少最新规则；同意补齐资料后小范围试点，尚未采购。",
        "next_action": f"销售于{due}前提供试点方案，王经理同日前提供20条脱敏样本。",
    }
    if case == "poor":
        fields.update(follow_up_record="聊了聊，客户暂时没有明确反馈。", next_action="继续跟进。")
    if case == "first":
        fields.update(customer_main_business="跨境电商客服服务", customer_needs="统一退款规则查询",
                      customer_budget="客户预算尚未确定", contact_role="使用者")
    if case == "review":
        return review_text(fields), fields
    text = (f"以下为拜访验收的合成测试素材。\n录入类型：{'首次拜访' if case == 'first' else '再次拜访'}\n"
            f"客户名称：{customer_name}\n对接人：王经理\n跟进日期：{today}\n创建时间：{today}\n"
            f"沟通内容：{fields['follow_up_record']}\n下一步计划：{fields['next_action']}")
    if case == "first":
        text += ("\n客户主营业务：跨境电商客服服务\n客户需求：统一退款规则查询\n"
                 "客户预算：客户预算尚未确定\n联系人角色：使用者")
    return text, fields


async def run(args):
    scope = json.loads(Path(args.scope_file).read_text())
    assert scope['account'] == 'XS001'
    if not args.preflight_only:
        assert scope.get('approval_status') == 'approved', 'Live scope is not approved'
    customer_id = str(UUID(scope['customer_id']))
    output = Path(args.output)
    with output.open('x') as stream:
        stream.write('{}\n')
    state = {'scenario': args.scenario, 'case': args.case, 'checks': [], 'ids': {},
             'preflight_only': args.preflight_only, 'started_at': datetime.now(UTC).isoformat()}
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
            check('scoped sales identity', actor.user_id == scope['actor_id'] and actor.role.value == 'sales')
            await set_request_context(connection, actor)
            customer = await connection.fetchrow('SELECT name FROM crm.customer WHERE id=$1::uuid', customer_id)
            check('scoped synthetic customer', customer and customer['name'] == scope['customer_name']
                  and any(m in customer['name'] for m in ('演示', '验收', '测试', '联调')))

            async def formal_state():
                rows = []
                for query in (
                    'SELECT * FROM crm.customer WHERE id=$1::uuid ORDER BY id',
                    'SELECT * FROM crm.opportunity WHERE customer_id=$1::uuid ORDER BY id',
                    'SELECT * FROM activity.visit WHERE customer_id=$1::uuid ORDER BY id',
                ):
                    rows.extend(dict(row) for row in await connection.fetch(query, customer_id))
                return fingerprint(rows), await connection.fetchval('SELECT count(*) FROM workflow.notification')

            before = await formal_state()
            text, source_fields = sample(args.case, customer['name'])
            state.update(formal_before_fingerprint=before[0], input_fingerprint=fingerprint(text))
            if args.preflight_only:
                state.update(preflight_passed=True, real_business_verified=False)
                return
            for account in actors:
                clients[account] = httpx.AsyncClient(base_url=args.base_url, timeout=15)
                session = await request(account, 'POST', '/auth/session', json={
                    'account_code': account, 'workspace': 'demo-sales-workspace',
                    'client': {'purpose': 'visit-entry-acceptance', 'label': args.scenario + ':' + args.case},
                })
                clients[account].headers['Authorization'] = 'Bearer ' + session['access_token']
                check(account + ' session belongs to own actor', session['actor']['user_id'] == actors[account].user_id)
            conversation = await request('XS001', 'POST', '/conversations', 201, json={'mode': 'visit_entry'})
            cid = state['ids']['conversation_id'] = conversation['id']
            path = f'/conversations/{cid}/messages'
            body = {'text': text, 'client_message_id': 'visit-acceptance-' + uuid4().hex, 'input_source': 'text'}
            started = monotonic()
            state['submitted_at'] = datetime.now(UTC).isoformat()
            accepted = await request('XS001', 'POST', path, 202, json=body)
            run_id = state['ids']['run_id'] = accepted['run_id']
            save()
            replay = await request('XS001', 'POST', path, 202, json=body)
            check('same message returns one run', replay['run_id'] == run_id)
            await request('XS001', 'POST', path, 409, json={**body, 'text': text + '不同正文'})
            deadline = monotonic() + 75
            while monotonic() < deadline:
                response = await clients['XS001'].get(f'/agent/runs/{run_id}')
                check('run polling readable', response.status_code == 200)
                public = response.json()
                if public['status'] in {'succeeded', 'failed', 'cancelled', 'waiting_human'}:
                    break
                await asyncio.sleep(1)
            state.update(elapsed_ms=round((monotonic() - started) * 1000), status=public['status'])
            check('visit remains awaiting human confirmation', public['status'] == 'waiting_human')
            result = public['result']
            fields, quality = result['fields'], result['quality_review']
            check('current structured review contract', result['prompt_version'] == PROMPT_VERSION
                  and type(quality.get('follow_up_score')) is int and 0 <= quality['follow_up_score'] <= 100
                  and type(quality.get('next_action', {}).get('passed')) is bool
                  and 'dimensions' not in quality)
            if args.case == 'poor':
                check('vague input cannot pass review', quality['follow_up_score'] <= 60
                      and quality['next_action']['passed'] is False and bool(quality['suggestions']))
            else:
                check('concrete visit has usable review', quality['follow_up_score'] > 60
                      and quality['next_action']['passed'] is True
                      and '退款' in fields['follow_up_record'] and '样本' in fields['next_action'])
                if args.case == 'review':
                    check('review does not rewrite user content', review_text(fields) == review_text(source_fields))
                else:
                    check('explicit customer and contact preserved', fields['customer_name'] == scope['customer_name']
                          and fields['contact_name'] == '王经理' and fields['opportunity_name'] == '')
                if args.case == 'first':
                    check('first visit retains unconfirmed budget and explicit role', fields['is_first_visit'] is True
                          and fields['customer_budget'] == '客户预算尚未确定' and fields['contact_role'] == '使用者'
                          and fields['customer_main_business'] and fields['customer_needs'])
            check('formal visits CRM and notifications unchanged', await formal_state() == before)
            check('provider metadata stays private', all(k not in result for k in (
                'provider', 'inference_route', 'fallback_reason', 'operation_id',
            )))
            artifacts = await connection.fetch(
                'SELECT id::text,status,model_ref FROM agent.artifact WHERE run_id=$1::uuid', run_id,
            )
            check('one pending visit artifact', len(artifacts) == 1
                  and artifacts[0]['status'] in {'pending_confirm', 'pending_supplement'}
                  and artifacts[0]['id'] == result['artifact_id'])
            state['ids']['artifact_id'] = artifacts[0]['id']
            check('no human confirmation created', await connection.fetchval(
                'SELECT count(*) FROM agent.confirmation WHERE artifact_id=$1::uuid', artifacts[0]['id'],
            ) == 0)
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
            check('one completed worker job without replay', len(jobs) == 1 and jobs[0]['status'] == 'succeeded'
                  and jobs[0]['attempts'] == 1)
            state['ids']['job_id'] = jobs[0]['id']
            check('one receipt and answer', await connection.fetchval(
                'SELECT count(*) FROM ops.job_effect WHERE job_id=$1::uuid', jobs[0]['id'],
            ) == 1 and await connection.fetchval(
                'SELECT count(*) FROM agent.message WHERE source_run_id=$1::uuid', run_id,
            ) == 1)
            context = await connection.fetchval('SELECT business_context FROM agent.run WHERE id=$1::uuid', run_id)
            trace = state['inference_route'] = context.get('inference_route')
            attempts = await connection.fetch(
                "SELECT provider_code,status,http_status,error_code,operation_id::text, "
                "request_snapshot->>'network_dispatch_suppressed' AS dispatch_suppressed "
                "FROM agent.model_invocation WHERE run_id=$1::uuid "
                "AND record_kind='provider_attempt' ORDER BY started_at",
                run_id,
            )
            state['attempts'] = [dict(row) for row in attempts]
            check('one operation and real final provider success', attempts
                  and len({row['operation_id'] for row in attempts}) == 1
                  and attempts[-1]['status'] == 'succeeded' and attempts[-1]['http_status'] == 200)
            if args.scenario == 'off':
                check('off uses original API only', trace is None
                      and all(row['provider_code'] == 'senseaudio' for row in attempts))
            else:
                check('artifact records actual provider', trace and artifacts[0]['model_ref'] == trace['model_ref'])
                if args.scenario == 'blocked':
                    check('controlled block original fallback', trace['provider'] == 'senseaudio'
                          and trace['fallback_reason'] == 'ControlledPlatformBlock'
                          and attempts[0]['dispatch_suppressed'] == 'true' and attempts[0]['http_status'] is None)
                else:
                    check('real platform request dispatched', attempts[0]['provider_code'] == 'agent_platform'
                          and attempts[0]['dispatch_suppressed'] == 'false')
                    state['platform_accepted'] = trace['provider'] == 'agent_platform'
                    check('platform result or validated fallback', state['platform_accepted'] or (
                        trace['provider'] == 'senseaudio'
                        and trace['fallback_reason'] in {'TimeoutError', 'InvalidAgentResult'}
                    ))
            await request('XS002', 'GET', f'/agent/runs/{run_id}', 404)
            check('other sales cannot read messages', await request('XS002', 'GET', path) == [])
            await set_request_context(connection, actors['XS002'])
            check('other sales cannot read artifact', await connection.fetchval(
                'SELECT count(*) FROM agent.artifact WHERE run_id=$1::uuid', run_id,
            ) == 0)
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
    parser.add_argument('--case', choices=('regular', 'first', 'review', 'poor'), required=True)
    parser.add_argument('--scope-file', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--base-url', default='https://www.ericepc.com/api/v1')
    parser.add_argument('--role', choices=('sales_app', 'sales_runtime'), default='sales_app')
    asyncio.run(run(parser.parse_args()))
