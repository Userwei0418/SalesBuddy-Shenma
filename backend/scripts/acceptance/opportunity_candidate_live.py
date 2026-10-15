"""Opt-in real model verification for shared-customer opportunity candidates; never confirms business writes."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx


async def main(args):
    prior = json.loads(args.business.read_text())
    output = {'checks': [], 'account': 'XS002'}
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120) as client:
        async def req(method, path, expected=200, **kwargs):
            response = await client.request(method, path, **kwargs)
            assert response.status_code == expected, (path, response.status_code, response.text[:200])
            return response.json()

        auth = await req('POST', '/auth/session', json={'account_code': 'XS002'})
        client.headers['Authorization'] = 'Bearer ' + auth['access_token']
        customer_id = prior['ids']['customer']
        customer = await req('GET', '/customers/' + customer_id)
        opportunities = await req('GET', '/opportunities', params={'customer_id': customer_id})
        own = opportunities['items'][0]
        cases = [
            ('none', '今天再次拜访客户，演示客服知识检索。客户认可方向，尚未批准采购。本次不关联商机。'),
            ('update', f'本次继续跟进已有商机“{own["name"]}”，只调整 ACV 为1200元，其他字段没有变化。'),
            ('create', '本次新增独立项目“【演示】仓储质检新场景”，ACV 8万元，商机确认30%，预计2026年12月20日关单。'),
        ]
        for action, text in cases:
            conv = await req('POST', '/conversations', 201,
                             json={'mode': 'opportunity_draft', 'customer_id': customer_id})
            accepted = await req('POST', f'/conversations/{conv["id"]}/messages', 202,
                                json={'text': text, 'client_message_id': str(uuid4())})
            for _ in range(120):
                run = await req('GET', '/agent/runs/' + accepted['run_id'])
                if run['status'] in ('waiting_human', 'succeeded', 'failed', 'cancelled'):
                    break
                await asyncio.sleep(1)
            result = run.get('result') or {}
            passed = run['status'] == 'waiting_human' and result.get('action') == action
            if action == 'none':
                passed = passed and not result.get('name') and not result.get('opportunity_id')
            if action == 'update':
                passed = (passed and result.get('opportunity_id') == own['id']
                          and result.get('amount') == 1200 and result.get('probability') is None)
            if action == 'create':
                passed = (passed and not result.get('opportunity_id') and result.get('amount') == 80000
                          and result.get('probability') == 30)
            row = {'case': action, 'passed': bool(passed), 'run_id': run['id'],
                   'prompt_version': result.get('prompt_version'), 'status': run['status'],
                   'customer_readable': bool(customer['id']), 'result_action': result.get('action')}
            output['checks'].append(row)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2))
            print(json.dumps(row, ensure_ascii=False), flush=True)
            assert passed, row


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--business', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
