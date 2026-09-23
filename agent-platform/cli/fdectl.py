#!/usr/bin/env python3
"""Raccoon personal CLI: browser login, explicitly authorized workspace."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys
import time
import urllib.error
import urllib.request
import webbrowser

SERVER = os.environ.get('FDE_SERVER', '').rstrip('/')
CONFIG = Path(os.environ.get('FDE_CONFIG_DIR', str(Path.home()/'.config/raccoon-cli'/__import__('hashlib').sha256(SERVER.encode()).hexdigest()[:16])))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http(path, data=None, token=None):
    headers = {'Accept':'application/json'}
    if data is not None: headers['Content-Type']='application/json'
    if token: headers['Authorization']='Bearer '+token
    req = urllib.request.Request(SERVER+'/fde-cli/v1'+path, headers=headers, data=json.dumps(data).encode() if data is not None else None)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=90) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as e:
        try: return e.code, json.load(e)
        except (ValueError, UnicodeError): return e.code, {'ok':False,'error':'HTTP '+str(e.code)}


def load_token():
    data=json.loads((CONFIG/'session.json').read_text())
    if data.get('server') != SERVER or not data.get('workspace_id'):
        raise ValueError('凭据服务器或空间不匹配，请重新 auth login')
    if data.get('expires_at',0) <= time.time(): raise ValueError('授权已过期，请重新 auth login')
    return data['token']


def save_token(data):
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(CONFIG,0o700)
    temp=CONFIG/('session.'+os.urandom(6).hex()+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as out: json.dump(dict(data,server=SERVER),out)
        os.replace(temp,CONFIG/'session.json')
    finally:
        if temp.exists():temp.unlink()


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', default='fde', help='fde 表示管理员授权的工作空间')
    commands=p.add_subparsers(dest='action',required=True)
    auth=commands.add_parser('auth').add_subparsers(dest='auth_action',required=True)
    login=auth.add_parser('login');login.add_argument('--no-browser',action='store_true');login.add_argument('--label',default=socket.gethostname()+' / Codex')
    auth.add_parser('whoami');auth.add_parser('logout')
    commands.add_parser('doctor');commands.add_parser('list')
    create=commands.add_parser('create');create.add_argument('--file',required=True);create.add_argument('--dry-run',action='store_true')
    get=commands.add_parser('get');get.add_argument('id')
    configure=commands.add_parser('configure');configure.add_argument('id');configure.add_argument('--file',required=True);configure.add_argument('--revision',required=True);configure.add_argument('--dry-run',action='store_true')
    publish=commands.add_parser('publish');publish.add_argument('id');publish.add_argument('--revision',required=True);publish.add_argument('--note',default='Published by personal Codex CLI');publish.add_argument('--dry-run',action='store_true')
    return p


def build_request(args):
    data={k:v for k,v in vars(args).items() if k!='file'}
    if hasattr(args,'file'):
        value=json.loads(Path(args.file).read_text())
        if not isinstance(value,dict):raise ValueError('JSON 顶层必须是对象')
        data['spec' if args.action=='create' else 'patch']=value
    return data


def main():
    args=parser().parse_args()
    try:
        from urllib.parse import urlsplit
        u = urlsplit(SERVER)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
            raise ValueError('请先设置 FDE_SERVER=https://你的域名或IP:端口')
        if args.action=='auth':
            if args.auth_action=='login':
                _, response=http('/auth/start',{'label':args.label})
                if not response.get('ok'):raise ValueError(response.get('error'))
                data=response['data'];url=data['verification_uri']
                if not url.startswith(SERVER+'/fde-cli/v1/authorize?'):raise ValueError('授权地址异常')
                print('用自己的平台账号打开并确认：\n'+url+'\n确认码：'+data['user_code'],file=sys.stderr,flush=True)
                if not args.no_browser:webbrowser.open(url)
                deadline=time.monotonic()+min(data['expires_in'],600)
                while time.monotonic()<deadline:
                    time.sleep(max(data.get('interval',3),3))
                    code,response=http('/auth/poll',{'device_code':data['device_code']})
                    if response.get('ok'):
                        save_token(response['data'])
                        _,response=http('/auth/me',token=response['data']['token'])
                        break
                    if code != 202:raise ValueError(response.get('error'))
                else:raise ValueError('登录确认超时，请重新 auth login')
            else:
                token=load_token()
                _,response=http('/auth/me' if args.auth_action=='whoami' else '/auth/logout',None if args.auth_action=='whoami' else {},token)
                if args.auth_action=='logout' and response.get('ok'):(CONFIG/'session.json').unlink(missing_ok=True)
        else:
            _,response=http('/agent',build_request(args),load_token())
        print(json.dumps(response,ensure_ascii=False,indent=2))
        return 0 if response.get('ok') else 1
    except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
        print('操作失败：'+str(e)+'。写操作超时后请先 list/get 核对状态。',file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('已取消。',file=sys.stderr);return 130

if __name__=='__main__':sys.exit(main())
