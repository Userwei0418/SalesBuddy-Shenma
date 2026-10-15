"""Live read-path timings with original accounts. Only authentication and audit writes occur.

Password is read once from the terminal, never from arguments or output files.
Responses/tokens are kept in memory; reports contain routes, statuses and timings.
No AI generation, account creation, business mutation or database fixture is run.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime
import getpass
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

import httpx

ACCOUNTS = {'XS001':'sales','XS002':'sales','XS003':'sales','ZJ001':'supervisor','ZJ002':'supervisor',
            'ZJL001':'manager','FDE001':'fde','FDE002':'fde','FDE003':'fde','FDEL001':'fde_lead','OPSADMIN':'administrator'}
COMMON = ['/auth/me','/tasks/overview','/assistant/home','/tasks?page_size=100&offset=0','/notifications?unread_only=false',
          '/directory/members','/directory/task-assignees','/customer-assets/map','/customer-assets?period=year',
          '/opportunities?page_size=100','/visits/form-schema']
SALES = ['/customers?page_size=100','/workbench','/dashboard?personal=false',f'/opportunities/overview?year={datetime.now().year}',
         '/profile/evaluation','/profile/performance?scope=self&period=year','/profile/sales-growth?days=30',
         '/risks','/directory/fde-members','/directory/task-positions']
FDE = ['/fde/dashboard?scope=self','/fde/profile?days=30','/fde/visit-opportunities']
OPS = ['/console/auth/me','/console/summary','/console/organization','/console/customers','/console/claims',
       '/console/opportunities','/console/ai/overview','/console/ai/calls','/console/ai/rules',
       '/console/audit','/console/system-events','/console/activities']
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')

async def main(args, password):
    results=[]
    sessions=[]
    semaphore=asyncio.Semaphore(args.concurrency)
    async def request(client,code,method,path,phase,**kwargs):
        async with semaphore:
            start=time.perf_counter()
            entry={'account':code,'phase':phase,'path':UUID.sub('{id}',path),'method':method}
            try:
                response=await client.request(method,path,**kwargs)
                entry.update(status=response.status_code,bytes=len(response.content),
                             server_timing=response.headers.get('server-timing'),
                             db_queries=response.headers.get('x-db-queries'),request_id=response.headers.get('x-request-id'))
            except httpx.HTTPError as exc:
                response=None;entry.update(status=None,error=type(exc).__name__)
            entry['ms']=round((time.perf_counter()-start)*1000,1)
            results.append(entry)
            if entry['ms']>args.slow_ms or entry['status'] not in {200,204}:
                print(json.dumps(entry),flush=True)
            return response
    try:
        for code in args.accounts.split(','):
            role=ACCOUNTS[code]
            url=urlsplit(args.base)
            client=httpx.AsyncClient(base_url=args.base,trust_env=False,timeout=35,
                                    headers={'Origin':f'{url.scheme}://{url.netloc}'})
            sessions.append((client,code))
            login_path='/console/auth/login' if role=='administrator' else '/auth/password/login'
            response=await request(client,code,'POST',login_path,'login',json={
                'account_code':code,'password':password,'role':role})
            if response is None or response.status_code!=200:continue
            auth=response.json();assert auth['actor']['account_code']==code
            client.headers['Authorization']='Bearer '+auth['access_token'];del auth
            paths=list(OPS if role=='administrator' else COMMON + (FDE if role.startswith('fde') else SALES))
            if role=='manager':paths.remove('/profile/sales-growth?days=30')
            if role=='fde_lead':paths+=['/fde/dashboard?scope=team','/fde/activity?scope=team']
            if role in {'manager','supervisor'}:paths+=['/profile/performance?scope=team&period=year']
            paths_for_burst=[]
            for path in paths:
                r=await request(client,code,'GET',path,'sequential')
                if r is None or r.status_code!=200:continue
                paths_for_burst.append(path)
                if path.startswith('/tasks?page'):
                    ids = [item['id'] for item in r.json().get('items', [])[:100]]
                    state_path='/tasks/overview'+('?'+'&'.join('task_ids='+id for id in ids) if ids else '')
                    await request(client,code,'GET',state_path,'card-states')
                if path.startswith('/opportunities?page'):
                    items=r.json().get('items',[])
                    if items:
                        detail='/opportunities/'+items[0]['id']+'/detail'
                        await request(client,code,'GET',detail,'detail');paths_for_burst.append(detail)
                if path.startswith('/customer-assets/map'):
                    items=r.json().get('items',[])
                    if items:await request(client,code,'GET','/customers/'+items[0]['id'],'detail')
            # Exercise prepared-plan reuse and bounded simultaneous page reads.
            for round_no in range(args.rounds):
                await asyncio.gather(*(request(client,code,'GET',path,'parallel-'+str(round_no+1)) for path in paths_for_burst))
            print(json.dumps({'completed_account':code,'requests':sum(r['account']==code for r in results)}),flush=True)
        # All logged-in business roles together, still bounded by one global limit.
        if args.mixed:
            await asyncio.gather(*(request(client,code,'GET',path,'mixed')
                for client,code in sessions if 'Authorization' in client.headers
                for path in (['/console/organization','/console/ai/overview'] if code=='OPSADMIN' else
                ['/auth/me','/assistant/home','/tasks?page_size=100&offset=0','/customer-assets/map','/directory/members'])))
    finally:
        for client,code in sessions:
            if 'Authorization' in client.headers:
                await request(client,code,'POST','/auth/logout','logout')
            await client.aclose()
        ordered=sorted(r['ms'] for r in results if r['phase'] not in {'login','logout'})
        report={'base':args.base,'created_at':datetime.now().astimezone().isoformat(),
                'concurrency':args.concurrency,'rounds':args.rounds,'requests':len(results),
                'p95_ms':ordered[min(len(ordered)-1,int(len(ordered)*.95))] if ordered else None,
                'max_ms':max(ordered) if ordered else None,
                'errors':sum(r['status'] not in {200,204} for r in results),'measurements':results}
        target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(report,indent=2));target.chmod(0o600)
        print(json.dumps({k:v for k,v in report.items() if k!='measurements'}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base',required=True);p.add_argument('--output',required=True)
    p.add_argument('--accounts',default=','.join(ACCOUNTS));p.add_argument('--concurrency',type=int,choices=range(1,13),default=6)
    p.add_argument('--rounds',type=int,choices=range(0,7),default=1);p.add_argument('--mixed',action='store_true')
    p.add_argument('--slow-ms',type=int,default=1000)
    args=p.parse_args()
    if any(code not in ACCOUNTS for code in args.accounts.split(',')):p.error('Use original business accounts only')
    asyncio.run(main(args,getpass.getpass('现有业务账号密码（不保存）：')))
