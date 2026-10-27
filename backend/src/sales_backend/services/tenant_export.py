"""Bounded, single-snapshot tenant portability packages; no persistent public URL."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
import zipfile

from sales_backend.async_resources import file_io
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.release_identity import RELEASE_IDENTITY
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.tenant_export_catalog import EXCLUSIONS, SECTIONS, require_permissions


class ExportRejected(ValueError):
    def __init__(self, message, status=409):
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 256 * 1024 * 1024
    max_rows: int = 250000
    timeout_seconds: int = 180

    @classmethod
    def configured(cls):
        values = {}
        for key, name, default, low, high in (
            ('max_bytes', 'SALES_TENANT_EXPORT_MAX_BYTES', cls.max_bytes, 1024 * 1024, 1024 ** 3),
            ('max_rows', 'SALES_TENANT_EXPORT_MAX_ROWS', cls.max_rows, 1, 1000000),
            ('timeout_seconds', 'SALES_TENANT_EXPORT_TIMEOUT_SECONDS', cls.timeout_seconds, 1, 600),
        ):
            try: value = int(os.environ.get(name, default))
            except ValueError: raise ExportRejected('导出服务容量配置无效', 503) from None
            if not low <= value <= high: raise ExportRejected('导出服务容量配置超出支持范围', 503)
            values[key] = value
        return cls(**values)


@dataclass
class Budget:
    limits: Limits
    byte_count: int = 0
    row_count: int = 0

    def add(self, size, rows=0):
        if size < 0 or rows < 0: raise ValueError('Invalid export size')
        if self.byte_count + size > self.limits.max_bytes or self.row_count + rows > self.limits.max_rows:
            raise ExportRejected('数据超过单包导出容量，请减少所选分类或联系管理员分批交付', 413)
        self.byte_count += size
        self.row_count += rows


@dataclass
class Package:
    directory: Path
    path: Path
    export_id: str
    manifest: dict
    sha256: str

    def cleanup(self):
        shutil.rmtree(self.directory)


def json_bytes(value):
    def encode(obj):
        if isinstance(obj, (datetime, date)): return obj.isoformat()
        if isinstance(obj, (Decimal, uuid.UUID)): return str(obj)
        raise TypeError('Unsupported export field type')
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=encode)+'\n').encode()


class _BufferedMemberWriter:
    """Bound NDJSON write batches in bytes and rows without owning the member."""

    _MAX_BYTES = 64 * 1024
    _MAX_ROWS = 50

    def __init__(self, member):
        self.member = member
        self.pending = bytearray()
        self.rows = 0

    async def write(self, content):
        if len(content) >= self._MAX_BYTES:
            # A single large row already passed the package budget; do not copy
            # it into the buffer or accumulate it with neighbouring rows.
            await self.flush()
            await file_io(self.member.write, content)
            return
        if len(self.pending) + len(content) > self._MAX_BYTES:
            await self.flush()
        self.pending.extend(content)
        self.rows += 1
        # The row bound retains regular await points even for a fast cursor and
        # tiny records, so cancellation/timeout cannot wait for a full byte batch.
        if self.rows >= self._MAX_ROWS:
            await self.flush()

    async def flush(self):
        if self.pending:
            await file_io(self.member.write, bytes(self.pending))
            self.pending.clear()
        self.rows = 0


class SnapshotDatabase:
    def __init__(self, connection, actor):
        self.connection, self.actor = connection, actor

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        if actor != self.actor or not readonly: raise PermissionError('Snapshot identity mismatch')
        yield self.connection


def table_query(table):
    tenant = 'id' if table.name == 'platform.workspace' else 'workspace_id'
    columns = table.columns.split(',')
    ordering = 'id' if 'id' in columns else ','.join(columns[:3])
    return (f'SELECT {table.columns} FROM {table.name} WHERE {tenant}=common.current_workspace_id() '
            f'AND ({table.predicate}) ORDER BY {ordering}')


class ActiveExportSource:
    """Ordinary tenant reads retain native RLS and a single database snapshot."""

    def __init__(self, database, actor, sections, originals, storage=None):
        self.database, self.actor = database, actor
        self.sections, self.originals = sections, originals
        self.storage = storage

    @asynccontextmanager
    async def snapshot(self):
        async with self.database.transaction(self.actor, readonly=True, isolation='repeatable_read') as conn:
            require_permissions(await AuthorizationRepository().effective(conn), self.sections, self.originals)
            if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))", 'tenant-export:'+self.actor.workspace_id):
                raise ExportRejected('本公司已有数据包正在生成，请稍后重试', 429)
            yield ActiveExportSnapshot(conn, self.actor, self.storage,
                                       (await conn.fetchval('SELECT transaction_timestamp()')).isoformat())


class ActiveExportSnapshot:
    def __init__(self, connection, actor, storage, snapshot_at):
        self.connection, self.actor, self.storage = connection, actor, storage
        self.snapshot_at = snapshot_at

    def rows(self, table):
        return self.connection.cursor(table_query(table), prefetch=50)

    def imports(self):
        return self.connection.cursor(r'''SELECT i.id,i.workspace_id,i.filename,i.file_size,i.file_path,
            i.storage_profile,i.storage_driver,i.storage_key,i.content_sha256,
            (i.filename !~* '\.(mp3|wav|m4a|aac|ogg|flac|amr|webm)$') AS original_download_allowed,
            ARRAY(SELECT v.id FROM activity.visit v WHERE v.workspace_id=common.current_workspace_id()
              AND v.source_import_id=i.id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived')) visit_ids
            FROM activity.visit_import i WHERE i.workspace_id=common.current_workspace_id()
            AND EXISTS(SELECT 1 FROM activity.visit v WHERE v.workspace_id=i.workspace_id
              AND v.source_import_id=i.id AND v.deleted_at IS NULL AND v.status IN ('confirmed','archived'))
            ORDER BY i.id''', prefetch=20)

    async def read_original(self, row):
        if row.get('original_download_allowed') is False:
            raise PermissionError('录音仅支持回听，不提供下载或导出')
        if not await self.connection.fetchval("SELECT security.authorization_import('visit.download_original',$1)", row['id']):
            raise PermissionError('没有所选原件的下载权限')
        self.storage = self.storage or MaterialStorage()
        return await self.storage.read(SnapshotDatabase(self.connection, self.actor), self.actor, row)


async def build_package(database, actor, sections, originals, *, limits=None, storage=None, source=None, export_id=None):
    limits = limits or Limits.configured()
    source = source or ActiveExportSource(database, actor, sections, originals, storage)
    budget = Budget(limits)
    export_id = uuid.UUID(export_id).hex if export_id else uuid.uuid4().hex
    directory = Path(tempfile.mkdtemp(prefix='salesbuddy-tenant-export-'))
    path = directory/'business-data.zip'
    try:
        async with asyncio.timeout(limits.timeout_seconds):
            manifest = {'format':'salesbuddy-tenant-export-v1', 'export_id':export_id,
                'workspace_id':actor.workspace_id, 'requested_by':actor.user_id,
                'sections':list(sections), 'originals_requested':originals,
                'source_build':RELEASE_IDENTITY, 'excluded_sections':[key for key in SECTIONS if key not in sections],
                'exclusions':EXCLUSIONS, 'restorable_database_backup':False, 'files':[], 'originals':[]}
            with path.open('xb') as handle:
                os.chmod(path, 0o600)
                with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    async with source.snapshot() as snapshot:
                        manifest['snapshot_at'] = snapshot.snapshot_at
                        expected_imports = set()
                        exported_imports = set()
                        for section in sections:
                            for table in SECTIONS[section].tables:
                                name = table.name.replace('.', '/')+'.ndjson'
                                digest = hashlib.sha256(); count = size = 0
                                with archive.open(name, 'w') as member:
                                    writer = _BufferedMemberWriter(member)
                                    async for row in snapshot.rows(table):
                                        value = dict(row)
                                        if 'workspace_id' in value and str(value['workspace_id']) != actor.workspace_id:
                                            raise ExportRejected('导出归属校验失败')
                                        if table.name == 'activity.visit' and value.get('source_import_id'):
                                            expected_imports.add(str(value['source_import_id']))
                                        content = json_bytes(value); budget.add(len(content), 1)
                                        await writer.write(content)
                                        digest.update(content); count += 1; size += len(content)
                                    await writer.flush()
                                manifest['files'].append({'path':name, 'rows':count, 'bytes':size,
                                    'sha256':digest.hexdigest(), 'columns':table.columns.split(',')})
                        if 'visits' in sections:
                            async for record in snapshot.imports():
                                row = dict(record)
                                if str(row['workspace_id']) != actor.workspace_id:
                                    raise ExportRejected('原件导出归属校验失败')
                                entry = {k:row[k] for k in ('id','filename','file_size','content_sha256','visit_ids')}
                                exported_imports.add(str(row['id']))
                                entry['included'] = originals and row.get('original_download_allowed') is not False
                                if originals and not entry['included']:
                                    entry['exclusion_reason'] = 'AUDIO_PLAYBACK_ONLY'
                                    entry['exclusion_message'] = '录音仅支持原录入本人回听，不提供下载或导出'
                                if entry['included']:
                                    budget.add(row['file_size'], 1)
                                    if row['storage_driver'] in {'postgres','s3'} and not (row['storage_key'] or '').endswith(actor.workspace_id+'/'+str(row['id'])):
                                        raise ExportRejected('原件存储归属校验失败')
                                    content = await snapshot.read_original(row)
                                    entry['path'] = 'originals/'+str(row['id'])+'.bin'
                                    entry['sha256'] = hashlib.sha256(content).hexdigest()
                                    await file_io(archive.writestr, entry['path'], content)
                                budget.add(len(json_bytes(entry)))
                                manifest['originals'].append(entry)
                            if exported_imports != expected_imports:
                                raise ExportRejected('跟进原件关联不完整或不可读取，未生成数据包')
                        manifest['record_count'] = budget.row_count
                        manifest['content_bytes'] = budget.byte_count
                    manifest_bytes = json_bytes(manifest); budget.add(len(manifest_bytes))
                    await file_io(archive.writestr, 'manifest.json', manifest_bytes)
                    await file_io(archive.writestr, 'README.txt', (
                        'SalesBuddy 本公司业务数据包。UTF-8 NDJSON，每行一个对象，金额为精确十进制字符串。\n'
                        '请先核对 manifest.json 的公司、快照时间、所选分类、排除项、各表列名/行数及 SHA-256。\n'
                        '原件通过 source_import_id / visit_ids 关联跟进；原文件名在 manifest.originals 中。\n'
                        '本包是所选可读业务数据，不能用于恢复整个数据库。未包含的分类/草稿/系统配置不可据此重建。\n'
                        '包内含公司资料，请通过公司批准的渠道保存和交付。\n').encode())
            # ZIP headers, the manifest and README are part of the advertised
            # package limit too, including empty-table exports.
            if path.stat().st_size > limits.max_bytes:
                raise ExportRejected('数据超过单包导出容量，请减少所选分类或联系管理员分批交付', 413)
            digest = await file_io(MaterialStorage._hash, path)
            return Package(directory, path, export_id, manifest, digest)
    except TimeoutError:
        await file_io(shutil.rmtree, directory)
        raise ExportRejected('导出超时，未生成可下载数据包；请减少分类或稍后重试', 504) from None
    except BaseException:
        await file_io(shutil.rmtree, directory)
        raise


async def record_prepared(connection, actor, package, reason):
    await connection.execute('''INSERT INTO ops.audit_log(workspace_id,actor_user_ref_id,actor_role_code,action_code,
        module_code,object_type,object_id,object_label,after_snapshot,result_code)
        VALUES($1::uuid,$2::uuid,$3,'company.export.prepared','company','tenant_export',$4::uuid,
        '本公司业务数据包已生成',$5::jsonb,'prepared')''', actor.workspace_id, actor.user_id, actor.role.value,
        package.export_id, {'sections':package.manifest['sections'],
        'include_originals':package.manifest['originals_requested'], 'reason':reason,
        'sha256':package.sha256, 'record_count':package.manifest['record_count']})
