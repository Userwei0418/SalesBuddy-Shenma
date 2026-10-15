"""Closed-company data portability using installed TCP API and restricted roles.

Called only from the disposable product-instance test; no production credentials
or database targets are accepted by this module.
"""
import asyncio
import hashlib
import io
import json
import uuid
import zipfile

import httpx


PREFIX = '/api/v1/closed-company-export'


async def recorded_actions(connection, session, required):
    # HTTP response completion precedes its final audit callback. Poll that
    # bounded local effect, rather than assuming bytes received means it ran.
    async with asyncio.timeout(5):
        while True:
            actions = await connection.fetch(
                'SELECT action FROM security.closed_export_event WHERE session_id=$1 ORDER BY id',
                uuid.UUID(session))
            values = [row['action'] for row in actions]
            if all(values.count(action) >= count for action, count in required.items()):
                return values
            await asyncio.sleep(0.02)


async def exercise(root, maintenance, origin, headers, tokens, customers, password, platform_cookies):
    assert (await maintenance.fetchval('SELECT current_database()')).startswith('sb_product_test_')
    workspace, ordinary = tokens[0]
    other = tokens[1][0]
    ws = uuid.UUID(workspace)
    person = await maintenance.fetchrow('SELECT id,account_code FROM platform.user_ref WHERE workspace_id=$1 LIMIT 1', ws)
    code = await maintenance.fetchval('SELECT external_workspace_id FROM platform.workspace WHERE id=$1', ws)
    login = {'company_code':code,'account_code':person['account_code'],'password':password}
    request = {'sections':['organization','customers','opportunities','visits','tasks','actuals','targets','partners'],
               'include_originals':True,'reason':'Synthetic closed-company export acceptance'}
    async with httpx.AsyncClient(base_url=origin, headers=headers, trust_env=False, timeout=60) as browser:
        page = await browser.get('/closed-company-export')
        assert page.status_code==200 and "frame-ancestors 'none'" in page.headers['content-security-policy']
        # A closed-export cookie is never obtainable while ordinary business is active.
        assert (await browser.post(PREFIX+'/login',json=login)).status_code==401
        async with httpx.AsyncClient(base_url=origin,headers=headers,cookies=platform_cookies,
                                     trust_env=False,timeout=30) as platform:
            if headers.get('X-Forwarded-Proto') == 'https':
                # This loopback transport represents the trusted TLS proxy.
                platform.headers['Cookie'] = '; '.join(
                    f'{key}={value}' for key, value in platform_cookies.items())
            tenants = (await platform.get('/api/v1/platform/tenants')).json()['items']
            tenant = next(row for row in tenants if row['id']==workspace)
            result = await platform.post('/api/v1/platform/tenants/'+workspace+'/state',json={
                'version_no':tenant['version_no'],'status':'closed','reason':'Synthetic data export closure'})
            assert result.status_code==200, (result.status_code,result.text[:500])
        result = await browser.post(PREFIX+'/login',json=login)
        assert result.status_code==200, (result.status_code,result.text[:500])
        assert result.json()['workspace_id']==workspace and result.json()['company_status']=='closed'
        assert 'access_token' not in result.json() and 'grants' not in result.json()
        assert 'HttpOnly' in result.headers['set-cookie'] and 'Path='+PREFIX in result.headers['set-cookie']
        if headers.get('X-Forwarded-Proto') == 'https':
            assert 'Secure' in result.headers['set-cookie']
            browser.headers['Cookie'] = result.headers['set-cookie'].split(';', 1)[0]
        session = result.json()['session_id']
        # Only the dedicated path accepts this cookie; no token exchange occurs.
        assert (await browser.get('/api/v1/console/customers')).status_code==401
        assert (await browser.get('/api/v1/console/customers',headers={'Authorization':'Bearer '+ordinary})).status_code in (401,403)
        assert (await browser.post('/api/v1/console/auth/login',json={
            'workspace':code,'account_code':person['account_code'],'password':password})).status_code==401
        assert (await browser.get(PREFIX+'/options',headers={'X-Company-ID':other})).status_code==403
        assert (await browser.get(PREFIX+'/options',headers={'Authorization':'Bearer '+ordinary})).status_code==403
        options = await browser.get(PREFIX+'/options')
        assert options.status_code==200 and all(item['available'] for item in options.json()['sections'])
        response = await browser.post(PREFIX+'/export',json=request)
        assert response.status_code==200, (response.status_code,response.text[:500])
        assert hashlib.sha256(response.content).hexdigest()==response.headers['x-export-sha256']
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            assert manifest['workspace_id']==workspace and len(manifest['originals'])==2
            assert {row['id'] for row in map(json.loads,archive.read('crm/customer.ndjson').splitlines())}=={customers[workspace]}
            assert 'PRIVATE DRAFT EXCLUDED' not in archive.read('activity/visit.ndjson').decode()
            for item in manifest['files']:
                content = archive.read(item['path'])
                assert hashlib.sha256(content).hexdigest()==item['sha256']
                assert other not in content.decode()
            for original in manifest['originals']:
                content = archive.read(original['path'])
                assert hashlib.sha256(content).hexdigest()==original['sha256']
                assert workspace in content.decode() and other not in content.decode()
        events = await recorded_actions(maintenance, session, {'login':1,'prepared':1,'sent':1})
        assert events[:2]==['login','prepared']
        # Reject complete-category escalation while retaining available categories.
        await maintenance.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'customer.read','deny')",ws,person['id'])
        try:
            assert (await browser.post(PREFIX+'/export',json=request)).status_code==403
            values=(await browser.get(PREFIX+'/options')).json()['sections']
            assert not next(item for item in values if item['code']=='customers')['available']
        finally:
            await maintenance.execute("DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code='customer.read'",ws,person['id'])
        metadata = await browser.post(PREFIX+'/export',json={**request,'include_originals':False})
        assert metadata.status_code==200
        with zipfile.ZipFile(io.BytesIO(metadata.content)) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            assert not any(name.startswith('originals/') for name in archive.namelist())
            assert all('storage_key' not in item and 'file_path' not in item for item in manifest['originals'])
        await recorded_actions(maintenance, session, {'sent':2,'denied':1})
        assert (await browser.post(PREFIX+'/logout')).status_code==200
        assert (await browser.get(PREFIX+'/session')).status_code==401
        assert await maintenance.fetchval("SELECT status FROM platform.workspace WHERE id=$1",ws)=='closed'
        assert await maintenance.fetchval("SELECT count(*) FROM crm.customer WHERE id=$1",uuid.UUID(customers[workspace]))==1
        assert await maintenance.fetchval("SELECT count(*) FROM platform.auth_session WHERE workspace_id=$1 AND revoked_at IS NULL",ws)==0
        # Other tenant remains accessible with its ordinary identity.
        result=await browser.get('/api/v1/console/customers',headers={'Authorization':'Bearer '+tokens[1][1]})
        assert result.status_code==200 and [item['id'] for item in result.json()['items']]==[customers[other]]
    return {'status':'passed','actual_platform_close':True,'restricted_installed_api':True,
            'ordinary_sessions_revoked':True,'dedicated_cookie_only':True,'two_tenant_isolation':True,
            'all_categories_and_both_original_drivers':True,'metadata_only_and_explicit_deny':True,
            'prepared_sent_denied_audit_checked':True,'logout_checked':True,'no_business_reactivation_or_deletion':True}
