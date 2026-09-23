#!/usr/bin/env python3
"""Fresh instance configuration. No production config is read."""
import argparse, ipaddress, json, os, re, secrets, shutil, subprocess
from pathlib import Path
from urllib.parse import urlsplit

def read_env(path):
    return dict(line.split('=',1) for line in path.read_text().splitlines() if line and not line.startswith('#') and '=' in line)

def create_env(defaults, origin, project, bind):
    e=read_env(defaults)
    for key in ('SECRET_KEY','DB_PASSWORD','REDIS_PASSWORD','WEAVIATE_API_KEY','CODE_EXECUTION_API_KEY','PLUGIN_DAEMON_KEY','PLUGIN_DIFY_INNER_API_KEY','DIFY_AGENT_API_TOKEN','DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN','DIFY_AGENT_SERVER_SECRET_KEY'):
        e[key]=secrets.token_urlsafe(32)
    e['INIT_PASSWORD']=secrets.token_urlsafe(18) # UI max 30 characters
    e['SANDBOX_API_KEY']=e['CODE_EXECUTION_API_KEY']
    e['WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS']=e['WEAVIATE_API_KEY']
    e['WEAVIATE_AUTHENTICATION_APIKEY_USERS']='installer@example.invalid'
    e['WEAVIATE_AUTHORIZATION_ADMINLIST_USERS']=e['WEAVIATE_AUTHENTICATION_APIKEY_USERS']
    e['WEAVIATE_AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED']='false'
    e['WEAVIATE_DISABLE_TELEMETRY']='true'
    e['CELERY_BROKER_URL']=f"redis://:{e['REDIS_PASSWORD']}@redis:6379/1"
    e['DIFY_AGENT_REDIS_URL']=f"redis://:{e['REDIS_PASSWORD']}@redis:6379/2"
    e['DIFY_AGENT_PLUGIN_DAEMON_API_KEY']=e['PLUGIN_DAEMON_KEY']
    e['DIFY_AGENT_INNER_API_KEY']=e['PLUGIN_DIFY_INNER_API_KEY']
    for k in ('CONSOLE_API_URL','CONSOLE_WEB_URL','SERVICE_API_URL','TRIGGER_URL','APP_API_URL','APP_WEB_URL','FILES_URL'):
        e[k]=origin
    e.update(INTERNAL_FILES_URL='http://api:5001', ENDPOINT_URL_TEMPLATE=origin+'/e/{hook_id}', NEXT_PUBLIC_SOCKET_URL=origin.replace('https:','wss:',1), CONSOLE_CORS_ALLOW_ORIGINS=origin,WEB_API_CORS_ALLOW_ORIGINS=origin,COMPOSE_PROJECT_NAME=project, HTTPS_PORT=str(urlsplit(origin).port or 443), BIND_ADDRESS=bind, CELERY_WORKER_AMOUNT='1',SERVER_WORKER_AMOUNT='1',SERVER_WORKER_CONNECTIONS='100', API_WEBSOCKET_WORKER_AMOUNT='1',MODEL_LB_ENABLED='true',NEXT_TELEMETRY_DISABLED='1',DISABLE_TELEMETRY='true', FDE_CLI_WORKSPACE_ID='',FDE_CLI_ALLOWED_ACCOUNTS='',SUPER_FDE_WORKSPACE_CREATOR_IDS='',ENABLE_EMAIL_PASSWORD_LOGIN='true',ALLOW_REGISTER='false',ALLOW_CREATE_WORKSPACE='false')
    e.pop('COMPOSE_PROFILES',None)
    return e

def main():
    p=argparse.ArgumentParser(); p.add_argument('--public-url',required=True); p.add_argument('--install-dir',required=True);p.add_argument('--bind-address',default='0.0.0.0');p.add_argument('--cert');p.add_argument('--key');p.add_argument('--resume',action='store_true');a=p.parse_args()
    u=urlsplit(a.public_url.rstrip('/'))
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment or not re.fullmatch(r'[A-Za-z0-9.-]+',u.hostname):
        p.error('public-url 必须是 https://域名或IPv4[:端口]，不能带路径')
    try:
        port=u.port or 443; ipaddress.IPv4Address(a.bind_address)
        if not 1<=port<=65535:raise ValueError()
    except ValueError: p.error('端口或绑定地址无效')
    if bool(a.cert)!=bool(a.key):p.error('--cert 与 --key 必须一起提供')
    target=Path(a.install_dir).absolute(); origin=a.public_url.rstrip('/'); src=Path(__file__).resolve().parent
    if target.is_symlink() or target==Path('/') or len(target.parts)<3 or src==target or target in src.parents or src in target.parents:
        p.error('请选择独立的绝对安装目录，例如 /opt/raccoon-agent')
    marker=target/'instance.json'
    if target.exists():
        if not a.resume:p.error('目录已存在；不会覆盖。首次失败后可使用相同命令加 --resume')
        meta=json.loads(marker.read_text())
        if meta['public_url']!=origin or meta['bind_address']!=a.bind_address:p.error('恢复参数与原安装不一致')
        if not (target/'runtime/.env').is_file() or not (target/'runtime/tls/server.key').is_file():p.error('配置未完整生成，请更换空目录安装；不要覆盖已有数据')
        print('恢复现有安装；密钥和数据保持不变。');return
    target.mkdir(mode=0o700,parents=True)
    shutil.copytree(src/'runtime',target/'runtime')
    for name in ('manage.sh','healthcheck.py','access.py','access_lookup.py'):
        shutil.copy2(src/name,target/name)
    e=create_env(src/'runtime/env.defaults',origin,'raccoon_'+secrets.token_hex(5),a.bind_address)
    env=target/'runtime/.env';env.write_text(''.join(f'{k}={v}\n' for k,v in e.items()));env.chmod(0o600)
    tls=target/'runtime/tls';tls.mkdir(mode=0o700)
    if a.cert:
        shutil.copy2(a.cert,tls/'server.crt');shutil.copy2(a.key,tls/'server.key')
    else:
        try: ipaddress.ip_address(u.hostname);kind='IP'
        except ValueError:kind='DNS'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-nodes','-days','365','-subj','/CN='+u.hostname,'-addext','subjectAltName='+kind+':'+u.hostname,'-keyout',str(tls/'server.key'),'-out',str(tls/'server.crt')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (tls/'server.key').chmod(0o600)
    marker.write_text(json.dumps(dict(public_url=origin,bind_address=a.bind_address,project=e['COMPOSE_PROJECT_NAME'],version='20260923'),indent=2)+'\n');marker.chmod(0o600)
    info=target/'首次登录.txt';info.write_text('访问地址：'+origin+'\n初始化口令：'+e['INIT_PASSWORD']+'\n打开页面先输入初始化口令，然后创建你自己的管理员账号。\n');info.chmod(0o600)
    print('配置已生成；初始化口令保存在 '+str(info))
if __name__=='__main__':main()
