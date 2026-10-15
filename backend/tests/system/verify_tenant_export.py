"""Real HTTP/RLS export acceptance inside the disposable four-role installation."""
import hashlib
import io
import json
import uuid
import zipfile

import httpx
import asyncpg


async def exercise(root, conn, origin, headers, tokens, customers):
    fixtures = {}
    for workspace, _ in tokens:
        ws = uuid.UUID(workspace)
        user = await conn.fetchval('SELECT id FROM platform.user_ref WHERE workspace_id=$1 LIMIT 1', ws)
        deleted = await conn.fetchval('''INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id,deleted_at)
            VALUES($1,'Deleted parent fixture','deleted-parent-fixture',$2,clock_timestamp()) RETURNING id''',ws,user)
        await conn.execute('''INSERT INTO crm.contact(workspace_id,customer_id,name)
            VALUES($1,$2,'Contact under deleted parent')''',ws,deleted)
        definition = await conn.fetchval("INSERT INTO config.form_definition(workspace_id,form_code,name,object_type) VALUES($1,'export-fixture','Synthetic export','visit') RETURNING id",ws)
        form = await conn.fetchval("INSERT INTO config.form_version(form_definition_id,version_no,status) VALUES($1,1,'active') RETURNING id",definition)
        records=[]
        for driver in ('postgres','local'):
            key=uuid.uuid4();visit=uuid.uuid4();content=('Synthetic export original '+workspace+' '+driver).encode()
            path=root/'materials'/('export-'+str(key)+'.txt')
            if driver=='local': path.touch(mode=0o600);path.write_bytes(content)
            await conn.execute('''INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,
                file_size,status,storage_profile,storage_driver,storage_key,content_sha256)
                VALUES($1,$2,$3,$4,$5,$6,'succeeded',$7,$8,$9,$10)''',key,ws,user,'../../synthetic-'+driver+'.txt',
                str(path) if driver=='local' else '',len(content),'legacy-local' if driver=='local' else 'postgres',driver,
                str(path) if driver=='local' else workspace+'/'+str(key),hashlib.sha256(content).hexdigest())
            if driver=='postgres': await conn.execute('INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,0,$3)',key,ws,content)
            await conn.execute('''INSERT INTO activity.visit(id,workspace_id,customer_id,recorder_user_ref_id,form_version_id,status,
                interaction_at,follow_up_record,source_import_id,confirmed_at,confirmed_by_user_ref_id)
                VALUES($1,$2,$3,$4,$5,'confirmed',clock_timestamp(),'Synthetic archived visit',$6,clock_timestamp(),$4)''',visit,ws,uuid.UUID(customers[workspace]),user,form,key)
            records.append((key,visit,content,path,driver))
        draft = await conn.fetchval('''INSERT INTO activity.visit(workspace_id,customer_id,recorder_user_ref_id,form_version_id,
            status,interaction_at,follow_up_record) VALUES($1,$2,$3,$4,'pending_confirm',clock_timestamp(),'PRIVATE DRAFT EXCLUDED') RETURNING id''',ws,uuid.UUID(customers[workspace]),user,form)
        await conn.execute('''INSERT INTO workflow.task(workspace_id,title,description,creator_user_ref_id,due_at,association_kind)
            VALUES($1,'Synthetic portable task','Synthetic task', $2,clock_timestamp()+interval '1 day','daily')''',ws,user)
        fixtures[workspace]=(user,records,draft)
    totals=[]
    async with httpx.AsyncClient(base_url=origin,trust_env=False,timeout=90,headers=headers) as client:
        for workspace, token in tokens:
            auth={'Authorization':'Bearer '+token}
            options=await client.get('/api/v1/console/company-data/options',headers=auth)
            assert options.status_code==200, (options.status_code,options.text[:500])
            assert all(s['available'] for s in options.json()['sections']), options.json()
            selected=[s['code'] for s in options.json()['sections']]
            body={'sections':selected,'include_originals':True,'reason':'Synthetic data portability acceptance'}
            response=await client.post('/api/v1/console/company-data/export',headers=auth,json=body)
            assert response.status_code==200, (response.status_code,response.text[:1000])
            assert response.headers['x-export-sha256']==hashlib.sha256(response.content).hexdigest()
            assert 'no-store' in response.headers['cache-control']
            archive=zipfile.ZipFile(io.BytesIO(response.content));manifest=json.loads(archive.read('manifest.json'))
            assert manifest['workspace_id']==workspace and len(manifest['originals'])==2
            assert all(not n.startswith('/') and '..' not in n.split('/') for n in archive.namelist())
            assert all(n not in archive.namelist() for n in ('private/bootstrap.json','platform/password_credential.ndjson'))
            for item in manifest['files']:
                content=archive.read(item['path'])
                assert hashlib.sha256(content).hexdigest()==item['sha256']
                rows=[json.loads(line) for line in content.splitlines()]
                assert len(rows)==item['rows']
                for row in rows:
                    if 'workspace_id' in row: assert row['workspace_id']==workspace
                if item['path']=='crm/customer.ndjson': assert [r['id'] for r in rows]==[customers[workspace]]
                if item['path']=='crm/contact.ndjson': assert not rows, 'Deleted-parent contact leaked into export'
                if item['path']=='activity/visit.ndjson':
                    assert {r['id'] for r in rows}=={str(r[1]) for r in fixtures[workspace][1]}
                    assert str(fixtures[workspace][2]) not in content.decode()
            for key,visit,content,_,_ in fixtures[workspace][1]:
                item=next(i for i in manifest['originals'] if i['id']==str(key))
                assert item['visit_ids']==[str(visit)]
                assert archive.read(item['path'])==content
            assert await conn.fetchval("SELECT count(*) FROM ops.audit_log WHERE workspace_id=$1 AND action_code='company.export.prepared'",uuid.UUID(workspace))==1
            # Binary metadata is retained without leaking storage keys in metadata-only mode.
            metadata=await client.post('/api/v1/console/company-data/export',headers=auth,json={**body,'include_originals':False})
            assert metadata.status_code==200
            nofiles=zipfile.ZipFile(io.BytesIO(metadata.content));m=json.loads(nofiles.read('manifest.json'))
            assert not any(n.startswith('originals/') for n in nofiles.namelist())
            assert all(not i['included'] and 'storage_key' not in i and 'path' not in i for i in m['originals'])
            other=next(w for w,_ in tokens if w!=workspace)
            assert (await client.post('/api/v1/console/company-data/export',headers={**auth,'X-Company-ID':other},json=body)).status_code==403
            assert (await client.post('/api/v1/console/company-data/export',headers=auth,json={**body,'workspace_id':other})).status_code==422
            user=fixtures[workspace][0];ws=uuid.UUID(workspace)
            await conn.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2) ON CONFLICT DO NOTHING',ws,user)
            for code in ('company.export','customer.read','visit.download_original'):
                await conn.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,$3,'deny')",ws,user,code)
                try: assert (await client.post('/api/v1/console/company-data/export',headers=auth,json=body)).status_code==403
                finally: await conn.execute('DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code=$3',ws,user,code)
            # Disabled modules are not smuggled through company.export.
            await conn.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='customer'",ws)
            try: assert (await client.post('/api/v1/console/company-data/export',headers=auth,json=body)).status_code==403
            finally: await conn.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='customer'",ws)
            # A second DB session serializes same-company builds across processes.
            async with conn.transaction():
                await conn.fetchval('SELECT pg_advisory_xact_lock(hashtextextended($1,0))','tenant-export:'+workspace)
                assert (await client.post('/api/v1/console/company-data/export',headers=auth,json=body)).status_code==429
            original=fixtures[workspace][1][1];path=original[3]
            path.write_bytes(b'broken synthetic material')
            try: assert (await client.post('/api/v1/console/company-data/export',headers=auth,json=body)).status_code==409
            finally: path.write_bytes(original[2])
            # Tamper only the owned synthetic association; missing originals must not be silently omitted.
            key=fixtures[workspace][1][0][0]
            try:
                await conn.execute("UPDATE activity.visit_import SET storage_key=$2 WHERE id=$1",key,other+'/'+str(key))
            except asyncpg.PostgresError as exc:
                assert 'ORIGINAL_LOCATION_IMMUTABLE' in str(exc)
            else: raise AssertionError('Original identity unexpectedly mutable')
            assert await conn.fetchval('SELECT storage_key FROM activity.visit_import WHERE id=$1',key)==workspace+'/'+str(key)
            await conn.execute("UPDATE platform.workspace SET status='suspended' WHERE id=$1",ws)
            try: assert (await client.post('/api/v1/console/company-data/export',headers=auth,json=body)).status_code in (401,403)
            finally: await conn.execute("UPDATE platform.workspace SET status='active' WHERE id=$1",ws)
            totals.append({'workspace':'synthetic-'+str(len(totals)+1),'tables':len(manifest['files']), 'originals':2,
                'isolation_and_hashes':True,'drafts_excluded':True,'deleted_parent_children_excluded':True,
                'permission_denials':3,'module_disabled_refused':True,
                'cross_company_header_refused':True,'workspace_body_refused':True,'busy_refused':True,
                'corrupt_original_refused':True,'stored_original_identity_immutable':True,'suspended_refused':True})
        assert (await client.post('/api/v1/console/company-data/export',json=body)).status_code==401
    snapshot=await snapshot_and_limits(root,conn,tokens[0][0],fixtures[tokens[0][0]][0])
    return {'status':'passed','snapshot_and_limits':snapshot,'tenants':totals,'only_synthetic_data':True,'production_changed':False}


async def snapshot_and_limits(root, maintenance, workspace, user):
    """Use the real restricted API login, not maintenance, for package reads."""
    from contextlib import asynccontextmanager
    from dataclasses import replace
    from sales_backend.config import get_settings
    from sales_backend.db import Database
    from sales_backend.domain.agent import ActorContext
    from sales_backend.services.tenant_export import build_package, ExportRejected, Limits
    from instance_service import service_environment
    _, environment = service_environment(root, 'api')
    database = Database(replace(get_settings(),database_url=environment['DATABASE_URL'],database_min_pool_size=1,database_max_pool_size=2))
    await database.connect()
    actor=ActorContext(workspace_id=workspace,user_id=str(user),role='administrator',data_scope='workspace')
    task=await maintenance.fetchrow('SELECT id,title FROM workflow.task WHERE workspace_id=$1 LIMIT 1',uuid.UUID(workspace))
    fired=[]
    class Connection:
        def __init__(self, original): self.original=original
        def __getattr__(self, key): return getattr(self.original,key)
        def cursor(self, query, *args, **kwargs):
            async def rows():
                async for row in self.original.cursor(query,*args,**kwargs): yield row
                if 'FROM crm.customer WHERE' in query and not fired:
                    fired.append(True)
                    await maintenance.execute("UPDATE workflow.task SET title='Changed after export snapshot' WHERE id=$1",task['id'])
            return rows()
    class HookDatabase:
        @asynccontextmanager
        async def transaction(self, *args, **kwargs):
            async with database.transaction(*args,**kwargs) as conn: yield Connection(conn)
    try:
        package=await build_package(HookDatabase(),actor,['customers','tasks'],False)
        try:
            with zipfile.ZipFile(package.path) as archive:
                rows=[json.loads(line) for line in archive.read('workflow/task.ndjson').splitlines()]
                assert next(r for r in rows if r['id']==str(task['id']))['title']==task['title']
            assert fired and await maintenance.fetchval('SELECT title FROM workflow.task WHERE id=$1',task['id'])=='Changed after export snapshot'
        finally: package.cleanup()
        try:
            package=await build_package(database,actor,['organization','customers'],False,limits=Limits(max_rows=1))
        except ExportRejected as exc: assert exc.status==413
        else:
            package.cleanup();raise AssertionError('Over-limit export unexpectedly succeeded')
        package=await build_package(database,actor,['partners'],False)
        bound=package.path.stat().st_size-1
        package.cleanup()
        try:
            package=await build_package(database,actor,['partners'],False,limits=Limits(max_bytes=bound))
        except ExportRejected as exc: assert exc.status==413
        else:
            package.cleanup();raise AssertionError('ZIP overhead bypassed package capacity')
    finally:
        await maintenance.execute('UPDATE workflow.task SET title=$2 WHERE id=$1',task['id'],task['title'])
        await database.close()
    return {'repeatable_read_under_concurrent_write':True,'restricted_api_login':True,
            'oversized_package_refused':True,'zip_overhead_included_in_limit':True}
