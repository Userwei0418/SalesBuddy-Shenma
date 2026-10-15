"""Storage reservations survive the separate import registration transaction."""
import asyncpg

from sales_backend.db import set_request_context


class StorageQuotaExceeded(ValueError):
    pass


class TenantStorageQuota:
    def __init__(self, database):
        self.database = database

    async def reserve_in_transaction(self, connection, upload, location):
        """Intent and quota commit together; caller sets commit uncertainty markers."""
        try:
            state = await connection.fetchval(
                'SELECT security.reserve_visit_storage($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9)',
                upload.import_id, upload.quota_receipt, upload.size, location['driver'],
                location['profile'], location['key'], location['sha256'], str(upload.path), upload.filename)
            if state != 'reserved':
                raise ValueError('原件已有保存记录，请先对账，不可重复上传')
        except asyncpg.PostgresError as exc:
            if exc.sqlstate == 'P7430':
                raise StorageQuotaExceeded('公司原件存储额度不足，请联系平台调整额度') from exc
            raise

    async def reserve(self, actor, upload, location):
        try:
            async with self.database.connection() as connection:
                async with connection.transaction():
                    await set_request_context(connection, actor)
                    state = await connection.fetchval(
                        'SELECT security.reserve_visit_storage($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9)',
                        upload.import_id, upload.quota_receipt, upload.size, location['driver'],
                        location['profile'], location['key'], location['sha256'], str(upload.path), upload.filename)
                    if state != 'reserved':
                        raise ValueError('原件已有保存记录，请先对账，不可重复上传')
                    upload.quota_commit_may_have_started = True
            upload.quota_reserved = True
        except asyncpg.PostgresError as exc:
            if exc.sqlstate == 'P7430':
                raise StorageQuotaExceeded('公司原件存储额度不足，请联系平台调整额度') from exc
            raise

    async def mark_external(self, actor, upload):
        async with self.database.connection() as connection:
            async with connection.transaction():
                await set_request_context(connection, actor)
                await connection.fetchval('SELECT security.mark_storage_external($1::uuid,$2::uuid)',
                                          upload.import_id, upload.quota_receipt)

    async def fence(self, actor, upload):
        async with self.database.connection() as connection:
            async with connection.transaction():
                await set_request_context(connection, actor)
                return await connection.fetchval('SELECT security.fence_unpublished_storage($1::uuid,$2::uuid)',
                                                 upload.import_id, upload.quota_receipt)

    async def release(self, actor, upload):
        async with self.database.connection() as connection:
            async with connection.transaction():
                await set_request_context(connection, actor)
                return await connection.fetchval('SELECT security.release_unpublished_storage($1::uuid,$2::uuid)',
                                                 upload.import_id, upload.quota_receipt)
