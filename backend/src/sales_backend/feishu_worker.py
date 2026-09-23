"""Dedicated least-privilege sync process; requires explicit FEISHU_SYNC_DATABASE_URL."""
import asyncio
import logging
import os
import signal
from dataclasses import replace

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.integrations.feishu import FeishuError
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.services.feishu_sync import FeishuSyncService

logger = logging.getLogger(__name__)


class FeishuWorker:
    def __init__(self, database):
        self.database = database
        self.repository = FeishuRepository()
        self.service = FeishuSyncService(database.settings, reuse_client=True)
        self.priority_claims = 0

    async def close(self):
        await self.service.close()

    async def heartbeat(self, event):
        while True:
            await asyncio.sleep(30)
            async with self.database.connection() as connection:
                await self.repository.heartbeat(connection, event)

    async def run_once(self):
        async with self.database.connection() as connection:
            validation = await self.repository.worker_config(connection, validation=True)
            if validation:
                await self.service.validate(connection, validation)
                return True
            # Prefer business changes over full-table repair, but reserve every
            # eleventh claim for FIFO so continuous writes cannot starve repair.
            prefer_history = self.priority_claims >= 10
            event = await self.repository.claim(connection, prefer_history=prefer_history)
            if not event:
                return False
            self.priority_claims = 0 if prefer_history else self.priority_claims + 1
            lock = f"feishu:{event['connection_id']}:{event['object_kind']}:{event['object_id']}"
            if not await self.repository.try_lock(connection, lock):
                await self.repository.finish(connection, event, error="OBJECT_BUSY", retryable=True)
                return True
            try:
                row = await self.repository.worker_config(connection, event["connection_id"])
                if not row:
                    await self.repository.finish(connection, event, error="CONNECTION_PAUSED", retryable=True)
                    return True
                handler = asyncio.create_task(self.service.handle(connection, event, row))
                heartbeat = asyncio.create_task(self.heartbeat(event))
                try:
                    done, _ = await asyncio.wait((handler, heartbeat), return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        await task
                finally:
                    for task in (handler, heartbeat):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(handler, heartbeat, return_exceptions=True)
                await self.repository.finish(connection, event)
            except FeishuError as exc:
                await self.repository.finish(connection, event, error=exc.code,
                    retryable=exc.retryable or (exc.unknown and event["object_kind"] != "refresh"),
                    retry_after=exc.retry_after)
            except Exception:
                # Provider responses, SQL parameters and content must never appear in logs.
                await self.repository.finish(connection, event, error="SYNC_PROCESSING_FAILED", retryable=True)
            finally:
                await self.repository.unlock(connection, lock)
            return True


async def verify_worker_role(connection):
    # Membership alone accepts superusers. Reject elevated or business-writing logins.
    safe = await connection.fetchval("""
      SELECT NOT (r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb)
        AND pg_has_role(current_user,'salegent_feishu_worker','member')
        AND NOT has_table_privilege(current_user,'crm.customer','INSERT,UPDATE,DELETE')
        AND NOT has_table_privilege(current_user,'activity.visit','INSERT,UPDATE,DELETE')
        AND NOT has_table_privilege(current_user,'workflow.task','INSERT,UPDATE,DELETE')
      FROM pg_roles r WHERE r.rolname=current_user
    """)
    if safe:
        safe = not await connection.fetchval("""
          SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
            AND has_schema_privilege(current_user,n.oid,'USAGE')
            AND has_function_privilege(current_user,p.oid,'EXECUTE')
            AND p.oid NOT IN ('ops.feishu_source(uuid,text,uuid)'::regprocedure,
                              'ops.feishu_reconcile(uuid)'::regprocedure,
                              'security.has_active_role(text)'::regprocedure))
        """)
    if not safe:
        raise RuntimeError("Dedicated non-elevated Feishu worker role required")


async def main():
    dsn = os.environ.get("FEISHU_SYNC_DATABASE_URL")
    if not dsn:
        raise RuntimeError("FEISHU_SYNC_DATABASE_URL must be explicitly configured")
    settings = replace(get_settings(), database_url=dsn, database_min_pool_size=1, database_max_pool_size=3)
    database = Database(settings)
    await database.connect()
    worker = FeishuWorker(database)
    loop, task = asyncio.get_running_loop(), asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        async with database.connection() as connection:
            await verify_worker_role(connection)
        # LISTEN is a wakeup hint; the database outbox remains durable authority.
        # A bounded local-only check recovers due retries and missed notifications.
        while True:
            try:
                async with database.connection() as listener:
                    wake = asyncio.Event()
                    def changed(*_args):
                        wake.set()
                    await listener.add_listener("sales_feishu_work", changed)
                    listener.add_termination_listener(changed)
                    try:
                        while not listener.is_closed():
                            wake.clear()
                            handled = await worker.run_once()
                            if not handled:
                                try:
                                    await asyncio.wait_for(wake.wait(), timeout=30)
                                except TimeoutError:
                                    pass
                    finally:
                        if not listener.is_closed():
                            await listener.remove_listener("sales_feishu_work", changed)
                            listener.remove_termination_listener(changed)
            except Exception:
                logger.error("feishu sync queue unavailable")
                await asyncio.sleep(5)

    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        try:
            await worker.close()
        finally:
            await database.close()


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
