#!/usr/bin/env python3
"""Bootstrap only the authorized customer instance through its HTTPS API."""
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import time
import urllib.error
import urllib.request

assert os.geteuid() == 0 and socket.gethostname() == 'opsbuddy'
root = Path('/opt/raccoon-agent')
origin = 'https://ops-salesbuddy.shenzhoukuntai.com:18899'
assert json.loads((root / 'instance.json').read_text())['public_url'] == origin
env = dict(line.split('=', 1) for line in (root / 'runtime/.env').read_text().splitlines()
           if line and not line.startswith('#') and '=' in line)
assert env['CONSOLE_API_URL'] == origin and env['ALLOW_REGISTER'] == 'false'
original_getaddrinfo = socket.getaddrinfo
def local_address(host, *args, **kwargs):
    if host == 'ops-salesbuddy.shenzhoukuntai.com':
        host = '127.0.0.1'
    return original_getaddrinfo(host, *args, **kwargs)
socket.getaddrinfo = local_address
cookies = http.cookiejar.CookieJar()
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
client = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), NoRedirect(),
    urllib.request.HTTPCookieProcessor(cookies),
    urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(root / 'runtime/tls/server.crt'))))
def api(path, payload=None):
    assert path.startswith('/console/api/')
    headers = {'Content-Type': 'application/json', 'Origin': origin}
    for cookie in cookies:
        if cookie.name == '__Host-csrf_token':
            headers['X-CSRF-Token'] = cookie.value
    request = urllib.request.Request(origin + path, headers=headers,
        data=None if payload is None else json.dumps(payload).encode())
    try:
        with client.open(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f'{path}: HTTP {error.code}') from None

receipt_dir = Path('/var/lib/shenma-provision')
receipt_dir.mkdir(mode=0o700, exist_ok=True)
receipt = receipt_dir / 'agent-admin.json'
status, setup = api('/console/api/setup')
assert status == 200 and setup['step'] in ('finished', 'not_started')
if receipt.exists():
    assert not receipt.is_symlink() and receipt.stat().st_uid == 0
    account = json.loads(receipt.read_text())
    assert account['email'] == 'admin@example.com' and account['origin'] == origin
else:
    assert setup['step'] == 'not_started', 'Existing instance has no matching bootstrap receipt'
    account = {'email': 'admin@example.com', 'name': '神码管理员',
               'password': 'Sk9!' + secrets.token_urlsafe(18), 'origin': origin,
               'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    fd = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(account, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
if setup['step'] == 'not_started':
    status, result = api('/console/api/init', {'password': env['INIT_PASSWORD']})
    assert status == 201 and result['result'] == 'success'
    status, result = api('/console/api/setup', {key: account[key] for key in ('email', 'name', 'password')} | {'language': 'zh-Hans'})
    assert status == 201 and result['result'] == 'success'
status, setup = api('/console/api/setup')
assert setup['step'] == 'finished'
status, result = api('/console/api/login', {'email': account['email'],
    'password': base64.b64encode(account['password'].encode()).decode(), 'remember_me': False})
assert status == 200 and result['result'] == 'success'
status, profile = api('/console/api/account/profile')
assert status == 200 and profile['email'] == account['email']
status, workspaces = api('/console/api/workspaces')
assert status == 200 and len(workspaces['workspaces']) == 1
status, apps = api('/console/api/apps?page=1&limit=20')
assert status == 200
status, result = api('/console/api/logout', {})
assert status == 200 and result['result'] == 'success'
evidence = {'origin': origin, 'setup': 'finished', 'admin_email': account['email'],
            'login_profile_logout': 'passed', 'workspace_count': len(workspaces['workspaces']),
            'app_count': apps.get('total'), 'credential_receipt': str(receipt),
            'verified_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
(receipt_dir / 'agent-admin-verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(evidence, ensure_ascii=False))
