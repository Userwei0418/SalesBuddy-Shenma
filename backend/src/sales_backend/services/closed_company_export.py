"""Purpose-bound export sessions; closed companies never regain business access."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import secrets

import asyncpg

from sales_backend.auth.passwords import DUMMY_HASH, verify_password
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.passwords import PasswordRepository
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.password_auth import LoginThrottled
from sales_backend.services.tenant_export import ExportRejected


SIGNATURES = {
    'candidate': 'closed_export_candidate(text,text)',
    'issue': 'closed_export_issue(uuid,uuid,text,text,text,text)',
    'status': 'closed_export_status(text)',
    'logout': 'closed_export_logout(text)',
    'authorize': 'closed_export_authorize(text,text[],boolean)',
    'rows': 'closed_export_rows(text,text,integer,integer)',
    'originals': 'closed_export_originals(text,boolean,integer,integer)',
    'event': 'closed_export_event(text,uuid,text,jsonb)',
}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def translate_error(exc):
    errors = {
        'P7401': (401, '导出会话无效或已过期，请重新登录'),
        'P7403': (403, '当前账号无权导出所选关闭公司的数据，请联系本公司管理员'),
        'P7422': (422, '导出参数无效，请重新选择'),
    }
    if exc.sqlstate in errors:
        status, message = errors[exc.sqlstate]
        raise ExportRejected(message, status) from exc
    raise exc


async def execute(connection, operation, *args):
    # All function names/types originate here. Table selection is a separately
    # reviewed SQL whitelist; neither request text nor a client GUC is authority.
    name, types = SIGNATURES[operation][:-1].split('(')
    parameters = ','.join(f'${n}::{kind}' for n, kind in enumerate(types.split(','), 1))
    try:
        return await connection.fetchval(f'SELECT security.{name}({parameters})', *args)
    except asyncpg.PostgresError as exc:
        translate_error(exc)


class ClosedExportService:
    def __init__(self, database):
        self.database = database

    async def call(self, operation, *args):
        async with self.database.connection() as connection:
            return await execute(connection, operation, *args)

    async def login(self, company, identifier, password, client_ip, *, previous_hash=None):
        company, identifier = company.strip(), identifier.strip().upper()
        repository = PasswordRepository()
        async with self.database.connection() as conn:
            # Failed attempts commit before password work and are never rolled
            # back by a rejected credential or permission check.
            network_key = digest('ip:'+client_ip)
            if not await repository.consume_attempt(conn, network_key, 50):
                state = await repository.limit_status(conn, network_key, 50)
                raise LoginThrottled('network', state['retry_after_seconds'])
            candidate = await execute(conn, 'candidate', company, identifier)
            canonical_company = candidate['company_code'] if candidate else company
            canonical_account = candidate['account_code'] if candidate else identifier
            account_key = digest('closed-export-account:'+canonical_company+':'+canonical_account)
            if not await repository.consume_attempt(conn, account_key, 5):
                state = await repository.limit_status(conn, account_key, 5)
                raise LoginThrottled('account', state['retry_after_seconds'])
        valid = await asyncio.to_thread(verify_password, password,
                                        candidate['password_hash'] if candidate else DUMMY_HASH)
        if not valid or not candidate or candidate.get('must_change_password', True):
            raise ExportRejected('公司码、账号或密码不正确，或账号不具备关闭公司导出资格', 401)
        token = secrets.token_urlsafe(48)
        async with self.database.connection() as conn:
            async with conn.transaction():
                profile = await execute(conn, 'issue', candidate['workspace_id'], candidate['user_id'],
                                        identifier, candidate['password_hash'], digest(token), account_key)
                if previous_hash:
                    await execute(conn, 'logout', previous_hash)
        return token, profile


def export_actor(profile):
    return ActorContext(workspace_id=profile['workspace_id'], user_id=profile['user_id'],
                        role='administrator', data_scope='workspace')


class ClosedExportSource:
    def __init__(self, database, token_hash, profile, sections, originals, storage=None):
        self.database, self.token_hash, self.profile = database, token_hash, profile
        self.actor = export_actor(profile)
        self.sections, self.originals = sections, originals
        self.storage = storage

    @asynccontextmanager
    async def snapshot(self):
        # Deliberately not Database.transaction(actor): its ordinary lifecycle
        # gate must continue rejecting every closed-company business operation.
        async with self.database.connection() as conn:
            async with conn.transaction(readonly=True, isolation='repeatable_read'):
                current = await execute(conn, 'authorize', self.token_hash, self.sections, self.originals)
                if (current['workspace_id'], current['user_id'], current['session_id']) != (
                        self.profile['workspace_id'], self.profile['user_id'], self.profile['session_id']):
                    raise ExportRejected('导出身份已变化，请重新登录', 401)
                if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtextextended($1,0))",
                                           'tenant-export:'+self.actor.workspace_id):
                    raise ExportRejected('本公司已有数据包正在生成，请稍后重试', 429)
                timestamp = (await conn.fetchval('SELECT transaction_timestamp()')).isoformat()
                yield ClosedExportSnapshot(conn, self.token_hash, self.actor, self.originals, self.storage, timestamp)


class ClosedExportSnapshot:
    def __init__(self, connection, token_hash, actor, originals, storage, snapshot_at):
        self.connection, self.token_hash, self.actor = connection, token_hash, actor
        self.originals, self.storage, self.snapshot_at = originals, storage, snapshot_at

    async def _pages(self, operation, argument, *, columns=None, table=None):
        offset = 0
        while True:
            result = await execute(self.connection, operation, self.token_hash, argument, offset, 100)
            items, next_offset = result['items'], result['next_offset']
            if (len(items) > 100 or (table and result.get('table') != table)
                    or (next_offset is not None and (not items or next_offset != offset+len(items)))):
                raise ExportRejected('导出分页契约不一致，未生成数据包')
            for row in items:
                if columns is not None and set(row) != columns:
                    raise ExportRejected('导出字段契约不一致，未生成数据包')
                yield row
            if next_offset is None:
                break
            offset = next_offset

    def rows(self, table):
        return self._pages('rows', table.name, columns=set(table.columns.split(',')), table=table.name)

    def imports(self):
        return self._pages('originals', self.originals)

    async def read_original(self, row):
        if row.get('original_download_allowed') is False:
            raise PermissionError('录音仅支持回听，不提供下载或导出')
        async def chunks():
            # Existing valid originals may have sub-MiB chunks. At least one
            # byte per chunk plus the shared size/hash checks bounds this loop
            # without imposing a new chunking format on historical materials.
            for index in range(row['file_size']+1):
                try:
                    part = await self.connection.fetchrow(
                        'SELECT * FROM security.closed_export_content_chunk($1,$2::uuid,$3)',
                        self.token_hash, str(row['id']), index)
                except asyncpg.PostgresError as exc:
                    translate_error(exc)
                if part is None:
                    return
                yield part
            raise ValueError('原件内容超过登记分块数')
        self.storage = self.storage or MaterialStorage()
        # The reader validates registered size, sequence and SHA-256 for all
        # storage drivers; only the PostgreSQL fetch capability differs.
        return await self.storage.read(None, self.actor, row, postgres_chunks=chunks)
