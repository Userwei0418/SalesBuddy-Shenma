#!/usr/bin/env python3
import argparse,json,os,subprocess
from pathlib import Path
root=Path(__file__).resolve().parent;runtime=root/'runtime'
p=argparse.ArgumentParser();p.add_argument('mode',choices=['cli','workspace']);p.add_argument('email');p.add_argument('workspace',nargs='?',default='');a=p.parse_args()
if os.geteuid()!=0:p.error('请使用 sudo')
if a.mode=='cli' and not a.workspace:p.error('需要填写工作空间名称或 ID')
cmd=['docker','compose','-f','compose.json']
r=subprocess.run(cmd+['exec','-T','api','/app/api/.venv/bin/python','-',a.mode,a.email,a.workspace],input=(root/'access_lookup.py').read_text(),cwd=runtime,text=True,capture_output=True)
if r.returncode:raise SystemExit('授权未更改。'+r.stderr[-600:])
lines=[line for line in r.stdout.splitlines() if line.startswith('RACCOON_RESULT=')]
if len(lines)!=1:raise SystemExit('未取得明确查询结果，授权未更改。')
result=json.loads(lines[0].split('=',1)[1]);path=runtime/'.env';old=path.read_text();e=dict(line.split('=',1) for line in old.splitlines() if line and not line.startswith('#') and '=' in line)
if a.mode=='cli':
    if e.get('FDE_CLI_WORKSPACE_ID') not in ('',result['workspace_id']):raise SystemExit('此实例的个人 CLI 已绑定另一个工作空间。未覆盖原授权；请管理员先处理已有绑定。')
    e['FDE_CLI_WORKSPACE_ID']=result['workspace_id']; key='FDE_CLI_ALLOWED_ACCOUNTS'
else:key='SUPER_FDE_WORKSPACE_CREATOR_IDS'
values=set(filter(None,e.get(key,'').split(',')));values.add(result['account_id']);e[key]=','.join(sorted(values))
tmp=runtime/'.env.access.tmp';fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(''.join(f'{k}={v}\n' for k,v in e.items()))
os.replace(tmp,path)
subprocess.run(cmd+['up','-d','--no-build','--no-deps','api'],cwd=runtime,check=True)
print('新实例授权已更新。账号仍须通过本人网页登录确认 CLI 授权。')
