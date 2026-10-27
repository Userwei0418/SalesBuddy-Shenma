"""Installation-owner recovery of a single original; never background deletion.

The platform UI sees aggregate usage only. This maintenance workflow verifies
bytes before registering an interrupted upload and records its operator/reason.
External uncertainty is never released merely because GET/HEAD returned 404.
"""
import hashlib
import os
import stat
from pathlib import Path

from sales_backend.async_resources import file_io
from sales_backend.db import set_request_context
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.visit_imports import create_import
from sales_backend.repositories.visit_upload_intents import reconcile_attempt
from sales_backend.services.capabilities import require_capability
from sales_backend.services.material_storage import CHUNK_SIZE
from sales_backend.services.visit_import import AUDIO_EXTENSIONS, DOCUMENT_EXTENSIONS


class ReconciliationConflict(ValueError):
    pass


async def require_owner(connection):
    if not await connection.fetchval("""SELECT current_user=pg_get_userbyid(c.relowner) OR r.rolsuper
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_roles r ON r.rolname=current_user
        WHERE n.nspname='control' AND c.relname='storage_allocation'"""):
        raise PermissionError('原件对账仅允许安装维护身份执行')


async def list_pending(connection, workspace, after=None):
    await require_owner(connection)
    rows=await connection.fetch("""SELECT * FROM control.storage_allocation WHERE workspace_id=$1::uuid
        AND state IN ('reserved','external_pending','discarding') AND ($2::uuid IS NULL OR import_id>$2::uuid)
        ORDER BY import_id LIMIT 101""",str(workspace),str(after) if after else None)
    return {'items':[summary(dict(row)) for row in rows[:100]],
            'next_after':str(rows[99]['import_id']) if len(rows)>100 else None}


async def snapshot(connection, workspace, identifier):
    await require_owner(connection)
    row=await connection.fetchrow('SELECT * FROM control.storage_allocation WHERE workspace_id=$1::uuid AND import_id=$2::uuid',str(workspace),str(identifier))
    if not row:
        raise LookupError('该公司没有这条原件记录')
    return dict(row)


def summary(row):
    return {k:str(row[k]) if k in ('workspace_id','import_id','updated_at') else row[k]
            for k in ('workspace_id','import_id','state','driver','size_bytes','updated_at')}


def staging_path(row, root):
    if not row['staging_path']:
        raise ValueError('此历史原件没有临时路径，需核查历史存储')
    path=Path(row['staging_path']); root=Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('上传暂存目录不可用，不能判断原件已不存在')
    if (path.parent.resolve()!=root or path.is_symlink() or path.suffix.lower() not in AUDIO_EXTENSIONS|DOCUMENT_EXTENSIONS
        or path.name != str(row['import_id'])+path.suffix):
        raise ValueError('原件路径不在受控上传目录')
    return path


def local_bytes(path, row):
    # Open without following symlinks and verify the bytes actually read.
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        details=os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size!=row['size_bytes'] or not 0<details.st_size<=100*CHUNK_SIZE:
            raise ValueError('原件大小或类型不符')
        content=stream.read(row['size_bytes']+1)
    if len(content)!=row['size_bytes'] or hashlib.sha256(content).hexdigest()!=row['content_sha256']:
        raise ValueError('原件完整性不符')
    return content


async def lock_allocation(connection, expected):
    await connection.execute('SELECT 1 FROM control.tenant_storage_quota WHERE workspace_id=$1 FOR UPDATE',expected['workspace_id'])
    row=await connection.fetchrow('SELECT * FROM control.storage_allocation WHERE import_id=$1 FOR UPDATE',expected['import_id'])
    if not row or row['workspace_id']!=expected['workspace_id'] or row['receipt']!=expected['receipt']:
        raise ReconciliationConflict('原件记录已变化，请重新检查')
    if row['state']=='stored' and await connection.fetchval('SELECT EXISTS(SELECT 1 FROM activity.visit_import WHERE id=$1)',row['import_id']):
        return dict(row)
    if row['updated_at']!=expected['updated_at'] or row['state']!=expected['state']:
        raise ReconciliationConflict('上传状态已变化，请重新检查')
    if await connection.fetchval('SELECT EXISTS(SELECT 1 FROM activity.visit_import WHERE id=$1)',row['import_id']):
        raise ReconciliationConflict('已有原件登记，请核对状态')
    return dict(row)


async def upload_actor(connection, row):
    if not row['actor_role']:
        raise PermissionError('原上传岗位不明确，需先核实身份')
    record=await IdentityRepository().find_actor_by_id(connection,workspace_id=str(row['workspace_id']),user_id=str(row['actor_id']),role=row['actor_role'])
    if not record:
        raise PermissionError('原上传账号当前无有效身份')
    actor=record.context
    await set_request_context(connection,actor)
    await require_capability(connection,actor,'visit.upload')
    if Path(row['filename']).suffix.lower() in AUDIO_EXTENSIONS:
        await require_capability(connection,actor,'visit.transcribe')
    return actor


async def audit(connection,row,reason,action):
    await connection.execute('''INSERT INTO control.audit(workspace_id,action,reason,before_state,after_state)
        VALUES($1,$2,$3,jsonb_build_object('import_id',$4::text,'state',$5::text),
        jsonb_build_object('maintenance_role',current_user,'state',(SELECT state FROM control.storage_allocation WHERE import_id=$4::uuid)))''',
        row['workspace_id'],'storage.'+action,reason,str(row['import_id']),row['state'])


async def registered_result(connection, row):
    # Caller owns quota -> allocation locks. The intent lock comes last, as it
    # does in normal registration. A committed original never gets a second job.
    if row.get('upload_intent_id') is not None:
        await upload_actor(connection, row)
        await reconcile_attempt(connection, row, registered=True)
    return {**summary(row), 'outcome': 'already_registered'}


async def reconcile(connection,storage,root,workspace,identifier,action,reason=''):
    row=await snapshot(connection,workspace,identifier)
    if action=='inspect': return summary(row)
    if action not in ('recover','release-unpublished') or not 1<=len(reason.strip())<=500:
        raise ValueError('需要明确的对账动作和原因')
    if row['state']=='stored':
        if not await connection.fetchval('SELECT EXISTS(SELECT 1 FROM activity.visit_import WHERE id=$1 AND workspace_id=$2)',row['import_id'],row['workspace_id']):
            raise ReconciliationConflict('额度记录和原件登记不一致，请核查')
        async with connection.transaction():
            current=await lock_allocation(connection,row)
            return await registered_result(connection,current)
    if row['state']=='released':
        if row.get('upload_intent_id') is not None:
            path=staging_path(row,root)
            try: await file_io(path.lstat)
            except FileNotFoundError: pass
            else: raise ReconciliationConflict('释放记录仍有暂存原件，需先核实清理')
            async with connection.transaction():
                current=await lock_allocation(connection,row)
                await upload_actor(connection,current)
                await reconcile_attempt(connection,current,registered=False)
        return {**summary(row),'outcome':'already_released'}
    path=staging_path(row,root)
    if action=='release-unpublished':
        # No release for external_pending, even if the object currently appears
        # absent: a previously submitted PUT could still complete later.
        if row['state'] not in ('reserved','discarding'):
            raise ReconciliationConflict('外部上传结果待确认，不能释放；请恢复原件登记')
        try: await file_io(path.lstat)
        except FileNotFoundError: pass
        else: raise ReconciliationConflict('原件仍在，保留容量；请使用恢复登记')
        async with connection.transaction():
            current=await lock_allocation(connection,row)
            if current['state']=='stored': return await registered_result(connection,current)
            await connection.execute('UPDATE control.tenant_storage_quota SET reserved_bytes=reserved_bytes-$2 WHERE workspace_id=$1',row['workspace_id'],row['size_bytes'])
            await connection.execute("UPDATE control.storage_allocation SET state='released',updated_at=clock_timestamp() WHERE import_id=$1",row['import_id'])
            if row.get('upload_intent_id') is not None:
                await upload_actor(connection,row)
                await reconcile_attempt(connection,row,registered=False)
            await audit(connection,row,reason,'release_unpublished')
        return {**summary(row),'state':'released','outcome':'released'}
    if row['state']=='discarding':
        raise ReconciliationConflict('原上传已进入清理，不能同时恢复登记')
    async with connection.transaction(): await upload_actor(connection,row)
    profile=storage.profile(row['profile'],row['driver'])
    expected=f"{row['workspace_id']}/{row['import_id']}"
    if row['driver']=='s3': expected=profile.get('prefix','visits').strip('/')+'/'+expected
    if row['storage_key']!=(str(path) if row['driver']=='local' else expected):
        raise ValueError('原件位置不符合该公司的存储目录')
    location=dict(profile=row['profile'],driver=row['driver'],key=row['storage_key'],sha256=row['content_sha256'])
    if row['driver']=='s3':
        from botocore.exceptions import ClientError
        try: content=await storage._read_s3(profile,row['storage_key'],row['size_bytes'])
        except ClientError as exc:
            if exc.response.get('ResponseMetadata',{}).get('HTTPStatusCode')!=404: raise
            # Retry only the exact verified original at its immutable key. This
            # remains the same reservation even if an earlier PUT arrives late.
            content=await file_io(local_bytes,path,row)
            async with connection.transaction():
                current=await lock_allocation(connection,row)
                if current['state']=='stored': return await registered_result(connection,current)
                actor=await upload_actor(connection,row)
                await connection.fetchval('SELECT security.mark_storage_external($1,$2)',row['import_id'],row['receipt'])
                await audit(connection,row,reason,'retry_original')
                row=await snapshot(connection,workspace,identifier)
            await storage._put_s3_content(profile,content,row['storage_key'])
            content=await storage._read_s3(profile,row['storage_key'],row['size_bytes'])
        if len(content)!=row['size_bytes'] or hashlib.sha256(content).hexdigest()!=row['content_sha256']:
            raise ValueError('对象存储原件完整性不符')
    else: content=await file_io(local_bytes,path,row)
    async with connection.transaction():
        current=await lock_allocation(connection,row)
        if current['state']=='stored': return await registered_result(connection,current)
        actor=await upload_actor(connection,row)
        await create_import(connection,actor,str(row['import_id']),row['filename'],path,row['size_bytes'],location)
        if row['driver']=='postgres':
            for number,start in enumerate(range(0,len(content),CHUNK_SIZE)):
                await connection.execute('INSERT INTO activity.visit_import_content(import_id,workspace_id,chunk_no,content) VALUES($1,$2,$3,$4)',
                    row['import_id'],row['workspace_id'],number,content[start:start+CHUNK_SIZE])
        await reconcile_attempt(connection,row,registered=True)
        await audit(connection,row,reason,'recover_original')
    # Staging cleanup is deliberately separate: a response lost at COMMIT must
    # not destroy the source being reconciled. Maintenance can verify it later.
    return {**summary(row),'state':'stored','outcome':'recovered'}
