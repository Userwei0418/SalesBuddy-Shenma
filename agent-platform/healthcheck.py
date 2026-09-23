#!/usr/bin/env python3
import argparse,json,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit
root=Path(__file__).resolve().parent; runtime=root/'runtime'
p=argparse.ArgumentParser();p.add_argument('--wait',type=int,default=0);a=p.parse_args()
meta=json.loads((root/'instance.json').read_text());url=meta['public_url'];u=urlsplit(url);port=u.port or 443
def check():
    compose=['docker','compose','-f','compose.json']
    expected=set(subprocess.check_output(compose+['config','--services'],cwd=runtime,text=True).split())
    raw=subprocess.check_output(compose+['ps','-a','--format','json'],cwd=runtime,text=True).strip()
    rows=json.loads(raw) if raw.startswith('[') else [json.loads(x) for x in raw.splitlines() if x]
    states={r['Service']:r for r in rows}
    for name in expected:
        r=states.get(name,{})
        if name=='init_permissions':
            if r.get('State')!='exited' or r.get('ExitCode')!=0:return False,'权限初始化尚未完成'
        elif r.get('State')!='running' or r.get('Health') not in ('','healthy',None):return False,name+' 未就绪'
    cmd=['curl','--noproxy','*','--silent','--show-error','--fail','--max-time','5','--cacert',str(runtime/'tls/server.crt'),'--resolve',f'{u.hostname}:{port}:127.0.0.1']
    for path in ['/console/api/setup','/signin','/brand-bootstrap.js','/favicon.ico']:
        r=subprocess.run(cmd+[url+path],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        if r.returncode:return False,path+' 无法通过 HTTPS 检查（检查证书域名和端口）'
    return True,'所有服务运行中，API、登录页、品牌资源 HTTPS 检查通过。'
deadline=time.monotonic()+a.wait
while True:
    try:ok,msg=check()
    except (subprocess.CalledProcessError,ValueError,KeyError) as ex:ok,msg=False,type(ex).__name__
    if ok:print(msg);break
    if time.monotonic()>=deadline:raise SystemExit('未就绪：'+msg+'；运行 sudo bash manage.sh logs 查看原因。')
    time.sleep(5)
