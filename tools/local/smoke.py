"""HTTP acceptance against fixed loopback only; synthetic tenants, no model calls."""
import json
import asyncio
import hashlib
from pathlib import Path
import sys
from local_paths import state_from_config
from otp_clock import wait_for_new_step
import time

import httpx

from bootstrap import connect, owner


async def reset_test_attempts(cfg):
    # Only the owned, fixed loopback cluster is eligible. Repeated smoke runs
    # share one IP; resetting its test bucket must never change product limits.
    admin = await owner(cfg)
    await admin.close()
    conn = await connect(cfg)
    try:
        marked = await conn.fetchval('SELECT version FROM public.salesbuddy_local_fixture WHERE id=1')
        assert marked == 1
        keys = [hashlib.sha256(text.encode()).hexdigest() for text in ('ip:127.0.0.1', 'account:local-b:AADMIN')]
        await conn.execute('DELETE FROM security.login_throttle WHERE key_hash=ANY($1::text[])', keys)
    finally:
        await conn.close()


def platform_otp(cfg, now=None):
    import base64
    import time
    from sales_backend.security.platform_mfa import authenticator
    return authenticator(base64.b32decode(cfg['platform_totp_seed'])).generate(time.time() if now is None else now).decode()


def main():
    cfg = json.loads(Path(sys.argv[1]).read_text())
    assert cfg['kind'] == 'salesbuddy-local-v1' and cfg['api_port'] == 18800
    local = state_from_config(cfg)
    fixtures = json.loads((local / 'fixtures.json').read_text())
    asyncio.run(reset_test_attempts(cfg))
    checks = []
    base = 'http://127.0.0.1:18800'
    with httpx.Client(base_url=base, timeout=30, trust_env=False) as client:
        admin = client.get('/admin')
        assert admin.status_code == 200 and '[LOCAL]' in admin.text
        checks.append('admin entry available')
        assert client.get('/api/v1/customers').status_code == 401
        checks.append('anonymous customer access denied')
        sessions = {}
        for account in fixtures['accounts']:
            result = client.post('/api/v1/auth/password/login', json={
                'account_code': account['account'], 'workspace': account['workspace'], 'password': cfg['test_password']})
            assert result.status_code == 200, (account['account'], result.status_code)
            session = result.json()
            assert session['actor']['role'] == account['role']
            assert not session['must_change_password']
            sessions[account['account']] = session
        checks.append('ten accounts authenticated with expected roles')
        for index, prefix in enumerate(('A', 'B')):
            session = sessions[prefix + 'ADMIN']
            headers = {'Authorization': 'Bearer ' + session['access_token']}
            response = client.get('/api/v1/console/companies', headers=headers)
            assert response.status_code == 200, response.status_code
            assert len(response.json()['items']) == 1
            own = fixtures['companies'][index]
            other = fixtures['companies'][1-index]
            assert session['actor']['workspace_id'] == own['id']
            response = client.get('/api/v1/console/customers', headers=headers)
            assert response.status_code == 200, response.status_code
            assert own['customer_id'] in response.text
            assert other['customer_id'] not in response.text
            response = client.get('/api/v1/console/customers/' + other['customer_id'], headers=headers)
            assert response.status_code in (403, 404), response.status_code
            response = client.post('/api/v1/console/companies/select', headers=headers, json={'company_id': other['id']})
            assert response.status_code in (403, 404), response.status_code
        checks.append('bidirectional tenant list/detail/company switch isolation')
        sales = {'Authorization': 'Bearer ' + sessions['ASALES']['access_token']}
        assert client.get('/api/v1/console/companies', headers=sales).status_code == 403
        checks.append('sales cannot enter company administration')
        bad = client.post('/api/v1/auth/password/login', json={
            'account_code': 'AADMIN', 'workspace': 'local-b', 'password': cfg['test_password']})
        assert bad.status_code == 401
        checks.append('wrong company login denied')
        response = client.post('/api/v1/auth/logout', headers=sales)
        assert response.status_code == 204
        assert client.get('/api/v1/auth/me', headers=sales).status_code == 401
        checks.append('logout revokes existing bearer session')
    with httpx.Client(base_url=base, timeout=30, trust_env=False, headers={'Origin':base}) as platform:
        page = platform.get('/platform')
        assert page.status_code == 200 and '[LOCAL]' in page.text
        assert platform.get('/api/v1/platform/tenants', headers={
            'Authorization':'Bearer '+sessions['AADMIN']['access_token']}).status_code == 401
        for attempt in range(3):
            issued_at = time.time()
            result = platform.post('/api/v1/platform/auth/login', json={'account_code':'PLATFORMADMIN',
                                   'password':cfg['test_password'], 'otp':platform_otp(cfg, issued_at)})
            if result.status_code != 403 or result.json().get('detail') != 'PLATFORM_MFA_INVALID' or attempt == 2:
                break
            # Synthetic test credentials only. Fresh codes must still pass all
            # real password/MFA/replay checks; this does not clear used steps.
            wait_for_new_step(int(issued_at) // 30)
        assert result.status_code == 200, (result.status_code, result.json().get('detail'))
        assert 'HttpOnly' in result.headers['set-cookie']
        assert platform.get('/api/v1/platform/tenants').status_code == 200
        assert platform.get('/api/v1/console/organization').status_code == 401
        assert platform.post('/api/v1/platform/auth/logout').status_code == 200
        assert platform.get('/api/v1/platform/tenants').status_code == 401
        checks.append('platform login and tenant identity domains remain independent')
    report = {'status': 'passed' , 'checked_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'build': json.loads((local / 'build.json').read_text()), 'checks': checks,
              'limits': ['No production traffic or data', 'No real AI/ASR/Feishu integration',
                         'WeChat simulator UI requires separate acceptance', 'Not a complete SaaS acceptance']}
    (local / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({'status': 'passed', 'checks': checks}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
