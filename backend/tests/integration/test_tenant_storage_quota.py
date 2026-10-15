"""Storage quota uses durable reservations and real restricted PostgreSQL writes."""
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, RoleCode, DataScope
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.visit_upload import VisitUploadService, LocalVisitImportStorage, UploadRejected
from tests.integration.feishu_fixtures import seed_fetchval, seed_execute, fixture_owner
from tests.integration.test_platform_control import platform, provision, activate, tenant, transition  # noqa: F401
from tests.integration.test_operations_api import TransactionDatabase, client_for
from tests.test_visit_upload_lifecycle import Source

pytestmark = pytest.mark.asyncio


async def company(client, connection):
    body, _, row = await provision(client)
    await activate(client, body, row)
    assert (await transition(client, await tenant(client, row['id']), 'active')).status_code == 200
    uid = await seed_fetchval(connection, 'SELECT id::text FROM platform.user_ref WHERE workspace_id=$1::uuid', row['id'])
    # Company administrators are not sales by default. Grant only the upload
    # functions for these synthetic fixtures through the existing override model.
    await seed_execute(connection, 'INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1::uuid,$2::uuid) ON CONFLICT DO NOTHING', row['id'], uid)
    await seed_execute(connection, """INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code)
        SELECT $1::uuid,$2::uuid,code,'allow',scopes[1] FROM config.permission_catalog
        WHERE code IN ('visit.upload','visit.read','visit.transcribe','visit.download_original')""", row['id'], uid)
    return ActorContext(workspace_id=row['id'], user_id=uid, role=RoleCode.ADMINISTRATOR, data_scope=DataScope.WORKSPACE)


async def quota(client, actor):
    response = await client.get(f'/api/v1/platform/tenants/{actor.workspace_id}/storage-quota')
    assert response.status_code == 200, response.text
    return response.json()


async def save(client, actor, limit):
    row = await quota(client, actor)
    response = await client.post(f'/api/v1/platform/tenants/{actor.workspace_id}/storage-quota', json={
        'version_no':row['version_no'], 'limit_bytes':limit, 'reason':'合成存储额度验收'})
    assert response.status_code == 200, response.text
    return response.json()


async def reserve(connection, actor, *, size=5, driver='postgres', import_id=None, receipt=None):
    await set_request_context(connection, actor)
    identifier, token = import_id or uuid4(), receipt or uuid4()
    state = await connection.fetchval('SELECT security.reserve_visit_storage($1,$2,$3,$4,$5,$6,$7,$8,$9)',
        identifier, token, size, driver, driver, 'synthetic-key', 'a'*64, '/synthetic/file.txt', 'retained.txt')
    assert state in ('reserved', 'external_pending')
    return identifier, token


async def test_postgres_upload_enforces_bytes_and_success_settles_once(platform, connection, tmp_path):  # noqa: F811
    client, _ = platform
    actor = await company(client, connection)
    assert (await quota(client, actor))['limit_bytes'] is None
    await save(client, actor, 5)
    db = TransactionDatabase(connection)
    storage = MaterialStorage()
    service = VisitUploadService(db, LocalVisitImportStorage(tmp_path), registry=storage)
    result = await service.upload(actor, source=Source(b'12345'), filename='original.txt')
    row = await quota(client, actor)
    assert (row['stored_bytes'], row['reserved_bytes'], row['remaining_bytes']) == (5, 0, 0)
    assert not list(tmp_path.iterdir())
    await set_request_context(connection, actor)
    assert await connection.fetchval('SELECT content FROM activity.visit_import_content WHERE import_id=$1::uuid', result['id']) == b'12345'
    with pytest.raises(UploadRejected) as error:
        await service.upload(actor, source=Source(b'6'), filename='other.txt')
    assert error.value.status_code == 409 and not list(tmp_path.iterdir())
    # Failed parsing still retains an original and consumes capacity.
    await seed_execute(connection, "UPDATE activity.visit_import SET status='failed' WHERE id=$1::uuid", result['id'])
    assert (await quota(client, actor))['stored_bytes'] == 5
    await save(client, actor, 0)
    await seed_execute(connection, 'DELETE FROM ops.job WHERE aggregate_id=$1::uuid', result['id'])
    await seed_execute(connection, 'DELETE FROM activity.visit_import WHERE id=$1::uuid', result['id'])
    assert (await quota(client, actor))['stored_bytes'] == 0
    assert await seed_fetchval(connection, 'SELECT count(*) FROM activity.visit_import_content WHERE import_id=$1::uuid', result['id']) == 0


async def test_reserved_capacity_tombstones_and_uncertain_objects(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection)
    await save(client, actor, 10)
    identifier, receipt = await reserve(connection, actor)
    await reserve(connection, actor, import_id=identifier, receipt=receipt)
    assert (await quota(client, actor))['reserved_bytes'] == 5
    await set_request_context(connection, actor)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval('SELECT security.release_unpublished_storage($1,NULL)', identifier)
    assert await connection.fetchval('SELECT security.fence_unpublished_storage($1,$2)', identifier, receipt)
    assert await connection.fetchval('SELECT security.release_unpublished_storage($1,$2)', identifier, receipt)
    assert await connection.fetchval('SELECT security.release_unpublished_storage($1,$2)', identifier, receipt)
    with pytest.raises(asyncpg.PostgresError) as error:
        async with connection.transaction():
            await reserve(connection, actor, import_id=identifier, receipt=receipt)
    assert error.value.sqlstate == 'P7409'
    external, token = await reserve(connection, actor, driver='s3')
    await connection.fetchval('SELECT security.mark_storage_external($1,$2)', external, token)
    assert not await connection.fetchval('SELECT security.release_unpublished_storage($1,$2)', external, token)
    row = await quota(client, actor)
    assert row['stored_bytes'] == 0 and row['reserved_bytes'] == 5 and row['pending_count'] == 1
    assert row['oldest_pending_at']
    # Lowering a limit does not revoke an already acknowledged reservation.
    await save(client, actor, 0)
    await seed_execute(connection, """INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,file_size,
        storage_driver,storage_profile,storage_key,content_sha256) VALUES($1,$2::uuid,$3::uuid,'retained.txt','',5,'s3','s3','synthetic-key',$4)""",
        external, actor.workspace_id, actor.user_id, 'a'*64)
    row = await quota(client, actor)
    assert row['stored_bytes'] == 5 and row['reserved_bytes'] == 0
    await seed_execute(connection, 'DELETE FROM activity.visit_import WHERE id=$1', external)
    row = await quota(client, actor)
    assert row['stored_bytes'] == 0 and row['reserved_bytes'] == 5  # deleting a pointer never proves S3 deletion


async def test_legacy_inserts_count_location_is_immutable_and_rollback_restores(platform, connection):  # noqa: F811
    client, _ = platform; actor = await company(client, connection)
    identifier = uuid4()
    await seed_execute(connection, """INSERT INTO activity.visit_import(id,workspace_id,created_by_user_ref_id,filename,file_path,file_size,storage_key)
        VALUES($1,$2::uuid,$3::uuid,'legacy.txt','/legacy.txt',7,'/legacy.txt')""", identifier, actor.workspace_id, actor.user_id)
    assert (await quota(client, actor))['stored_bytes'] == 7
    for field, value in [('file_size', '8'), ('storage_key', "'/elsewhere'")]:
        with pytest.raises(asyncpg.PostgresError) as error:
            await seed_execute(connection, f'UPDATE activity.visit_import SET {field}={value} WHERE id=$1', identifier)
        assert error.value.sqlstate == 'P7422'
    with pytest.raises(RuntimeError):
        async with connection.transaction():
            await seed_execute(connection, 'DELETE FROM activity.visit_import WHERE id=$1', identifier)
            assert (await quota(client, actor))['reserved_bytes'] == 7
            raise RuntimeError('rollback')
    assert (await quota(client, actor))['stored_bytes'] == 7
    with pytest.raises(asyncpg.PostgresError) as error:
        await seed_execute(connection, 'TRUNCATE activity.visit_import CASCADE')
    assert error.value.sqlstate == 'P7422'


async def test_tenant_isolation_private_ledger_and_inactive_tenant(platform, connection):  # noqa: F811
    client, _ = platform; first = await company(client, connection); second = await company(client, connection)
    await save(client, first, 0)
    with pytest.raises(asyncpg.PostgresError) as error:
        async with connection.transaction():
            await reserve(connection, first)
    assert error.value.sqlstate == 'P7430'
    identifier, receipt = await reserve(connection, second)
    assert (await quota(client, first))['reserved_bytes'] == 0
    assert (await quota(client, second))['reserved_bytes'] == 5
    await set_request_context(connection, first)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval('SELECT security.release_unpublished_storage($1,$2)', identifier, receipt)
    for query in ('SELECT * FROM control.storage_allocation','UPDATE control.tenant_storage_quota SET reserved_bytes=0',
                  "SELECT control.rebind_restored_local_original(gen_random_uuid(),'forged')"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.execute(query)
    async with await client_for(connection) as company_client:
        assert (await company_client.get(f'/api/v1/platform/tenants/{first.workspace_id}/storage-quota')).status_code == 401
    assert (await transition(client, await tenant(client, second.workspace_id), 'suspended')).status_code == 200
    # The same previously valid context does not authorize new reservations.
    with pytest.raises(PermissionError):
        await reserve(connection, second)


async def test_platform_validation_cas_audit_and_mfa(platform, connection):  # noqa: F811
    client, db = platform; actor = await company(client, connection); initial = await quota(client, actor)
    path = f'/api/v1/platform/tenants/{actor.workspace_id}/storage-quota'
    payload = dict(version_no=initial['version_no'], limit_bytes=3, reason='合成额度变更')
    assert (await client.post(path,json=payload)).status_code == 200
    assert (await client.post(path,json=payload)).status_code == 409
    for invalid in (-1, True, 1.5, 9007199254740992):
        assert (await client.post(path,json={**payload,'limit_bytes':invalid})).status_code == 422
    assert (await client.post(path,json={k:v for k,v in payload.items() if k!='limit_bytes'})).status_code == 422
    await save(client, actor, None)
    rows=(await client.get('/api/v1/platform/audit')).json()['items']
    event=next(r for r in rows if r['action']=='tenant.storage_quota')
    assert event['before_state']['limit_bytes']==3 and event['after_state']['limit_bytes'] is None
    assert 'storage_key' not in str(event) and 'staging_path' not in str(event)
    async with fixture_owner(connection):
        await connection.execute("UPDATE control.session SET mfa_verified_at=clock_timestamp()-interval '6 minutes' WHERE operator_id=$1", db.operator_id)
    assert (await client.post(path,json={**payload,'version_no':(await quota(client,actor))['version_no']})).status_code == 403


async def pending_upload(connection, actor, tmp_path, storage):
    from sales_backend.services.storage_quota import TenantStorageQuota
    upload=LocalVisitImportStorage(tmp_path).begin(str(uuid4()),'.txt')
    upload.filename='interrupted.txt'
    await upload.write(b'12345'); await upload.ready()
    location=await storage.describe(actor,upload)
    quota_service=TenantStorageQuota(TransactionDatabase(connection))
    await quota_service.reserve(actor,upload,location)
    return upload,location,quota_service


async def test_owner_recovery_registers_verified_original_once_and_fences_cleanup(platform, connection, tmp_path):  # noqa: F811
    from sales_backend.services.storage_reconciliation import reconcile
    client,_=platform; actor=await company(client,connection); await save(client,actor,5)
    storage=MaterialStorage(); upload,_,quota_service=await pending_upload(connection,actor,tmp_path,storage)
    with pytest.raises(PermissionError):
        await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'inspect')
    async with fixture_owner(connection):
        result=await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'recover','合成原件恢复')
        assert result['outcome']=='recovered'
        repeated=await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'recover','重复恢复')
        assert repeated['outcome']=='already_registered'
    assert not await quota_service.fence(actor,upload)
    assert upload.path.read_bytes()==b'12345'
    assert (await quota(client,actor))['stored_bytes']==5 and (await quota(client,actor))['reserved_bytes']==0
    await set_request_context(connection,actor)
    assert await connection.fetchval('SELECT content FROM activity.visit_import_content WHERE import_id=$1::uuid',upload.import_id)==b'12345'
    assert await seed_fetchval(connection,'SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid',upload.import_id)==1


async def test_cleanup_fence_blocks_recovery_and_only_absent_unpublished_files_release(platform, connection, tmp_path):  # noqa: F811
    from sales_backend.services.storage_reconciliation import reconcile, ReconciliationConflict
    client,_=platform; actor=await company(client,connection); storage=MaterialStorage()
    upload,_,quota_service=await pending_upload(connection,actor,tmp_path,storage)
    async with fixture_owner(connection):
        with pytest.raises(ReconciliationConflict):
            await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'release-unpublished','还存在的原件')
    assert await quota_service.fence(actor,upload)
    async with fixture_owner(connection):
        with pytest.raises(ReconciliationConflict):
            await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'recover','与清理竞争')
    upload.path.unlink()  # Simulate the cleanup process after the committed fence.
    async with fixture_owner(connection):
        result=await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'release-unpublished','原件已清理但额度回执丢失')
    assert result['outcome']=='released' and (await quota(client,actor))['reserved_bytes']==0


class SyntheticS3(MaterialStorage):
    """An in-memory object service, not proof of real S3 connectivity."""
    def __init__(self):
        super().__init__({'default':'s3','profiles':{'s3':{'driver':'s3','bucket':'synthetic'}}})
        self.objects={}; self.fail_after_put=False; self.puts=0

    async def _read_s3(self,profile,key,expected_size):
        from botocore.exceptions import ClientError
        if key not in self.objects:
            raise ClientError({'Error':{'Code':'NoSuchKey'},'ResponseMetadata':{'HTTPStatusCode':404}},'GetObject')
        return self.objects[key]

    async def _put_s3_content(self,profile,content,key):
        self.puts+=1; self.objects[key]=content
        if self.fail_after_put: raise TimeoutError('Object stored but acknowledgement lost')


async def test_external_unknown_never_releases_and_verified_retry_keeps_one_reservation(platform, connection, tmp_path):  # noqa: F811
    from sales_backend.services.storage_reconciliation import reconcile, ReconciliationConflict
    client,_=platform; actor=await company(client,connection); await save(client,actor,5)
    storage=SyntheticS3(); upload,location,quota_service=await pending_upload(connection,actor,tmp_path,storage)
    await quota_service.mark_external(actor,upload)
    async with fixture_owner(connection):
        with pytest.raises(ReconciliationConflict):
            await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'release-unpublished','外部暂时查无对象')
        storage.fail_after_put=True
        with pytest.raises(TimeoutError):
            await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'recover','恢复丢失回执')
    assert storage.objects[location['key']]==b'12345' and storage.puts==1
    assert (await quota(client,actor))['reserved_bytes']==5
    assert not await seed_fetchval(connection,'SELECT EXISTS(SELECT 1 FROM activity.visit_import WHERE id=$1::uuid)',upload.import_id)
    async with fixture_owner(connection):
        result=await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'recover','再次核实已保存原件')
    assert result['outcome']=='recovered' and storage.puts==1
    assert (await quota(client,actor))['stored_bytes']==5 and (await quota(client,actor))['reserved_bytes']==0
    assert upload.path.exists() and not await quota_service.fence(actor,upload)


async def test_wrong_original_and_changed_authorization_do_not_register(platform, connection, tmp_path):  # noqa: F811
    from sales_backend.services.storage_reconciliation import reconcile
    client,_=platform; actor=await company(client,connection); storage=MaterialStorage()
    upload,_,_=await pending_upload(connection,actor,tmp_path,storage)
    upload.path.write_bytes(b'wrong')
    async with fixture_owner(connection):
        with pytest.raises(ValueError,match='完整性'):
            await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'recover','内容错误')
    upload.path.write_bytes(b'12345')
    assert (await transition(client,await tenant(client,actor.workspace_id),'suspended')).status_code==200
    async with fixture_owner(connection):
        with pytest.raises(PermissionError):
            await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'recover','暂停公司不能恢复业务')
    assert (await quota(client,actor))['reserved_bytes']==5


async def test_recovery_preserves_original_role_and_pending_list_is_scoped(platform, connection, tmp_path):  # noqa: F811
    from sales_backend.services.storage_reconciliation import reconcile, list_pending
    client,_=platform; actor=await company(client,connection); other=await company(client,connection)
    await seed_execute(connection,"INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1::uuid,$2::uuid,'operations','workspace')",actor.workspace_id,actor.user_id)
    actor=actor.model_copy(update={'role':RoleCode.OPERATIONS})
    storage=MaterialStorage(); upload,_,_=await pending_upload(connection,actor,tmp_path,storage)
    async with fixture_owner(connection):
        assert (await list_pending(connection,other.workspace_id))['items']==[]
        pending=await list_pending(connection,actor.workspace_id)
        assert len(pending['items'])==1 and pending['items'][0]['import_id']==upload.import_id
        assert not {'filename','staging_path','receipt'}.intersection(pending['items'][0])
        await reconcile(connection,storage,tmp_path,actor.workspace_id,upload.import_id,'recover','原岗位恢复')
    payload=await seed_fetchval(connection,'SELECT payload FROM ops.job WHERE aggregate_id=$1::uuid',upload.import_id)
    assert payload['role']=='operations'  # not the alphabetically first administrator role
