"""Upload/commit/Worker/archive faults in a newly created, disposable database.

Uses no model API. Invoke with the same PGHOST/PGUSER as run_integration_postgres;
never point this at a business database. The runner owns and drops its database.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from urllib.parse import quote
from uuid import uuid4

from run_integration_postgres import main as isolated_database

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.visit_imports import import_detail, retry_import
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services import visit_import, visit_upload
from sales_backend.services.visit_upload import LocalVisitImportStorage, VisitUploadService
from sales_backend.worker import Worker


class Source:
    def __init__(self, content):
        self.content = content

    async def read(self, size):
        chunk, self.content = self.content[:size], self.content[size:]
        return chunk


class RuntimeDatabase(Database):
    def __init__(self, settings, role):
        super().__init__(settings)
        self.role = role
        self.after_commit_failure = None
        self.before_commit = None

    @asynccontextmanager
    async def connection(self):
        async with super().connection() as connection:
            await connection.execute(f'SET ROLE "{self.role}"')
            try:
                yield connection
            finally:
                await connection.execute("RESET ROLE")

    @asynccontextmanager
    async def transaction(self, actor, *, readonly=False):
        async with super().transaction(actor, readonly=readonly) as connection:
            yield connection
            if not readonly and self.before_commit:
                await self.before_commit()
        # The actual Database transaction context has exited and PostgreSQL has
        # committed. Inject failure only here, not in a fake connection/Mock COMMIT.
        if not readonly and self.after_commit_failure:
            failure, self.after_commit_failure = self.after_commit_failure, None
            raise failure


async def verify(config, name, role):
    assert name.startswith("salegent_verify_integration_")
    settings = replace(
        get_settings(), app_env="test", auth_mode="password", senseaudio_api_key="",
        database_url=f"postgresql:///{name}?host={quote(config['host'])}&user={quote(config['user'])}",
        database_min_pool_size=1, database_max_pool_size=4,
        worker_id="isolated-upload", worker_lock_seconds=30,
    )
    db = RuntimeDatabase(settings, role)
    await db.connect()
    prior_root = visit_import.IMPORT_ROOT
    prior_storage_root = os.environ.get("VISIT_IMPORT_ROOT")
    original_register = visit_upload.create_import
    results = []
    try:
        async with db.connection() as conn:
            assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            actor = (await IdentityRepository().find_actor_by_account(
                conn, workspace_external_id="demo-sales-workspace", account_code="XS001",
            )).context
        with TemporaryDirectory(prefix="salegent-upload-postgres-") as folder:
            root = Path(folder)
            visit_import.IMPORT_ROOT = root
            os.environ["VISIT_IMPORT_ROOT"] = str(root)
            service = VisitUploadService(db, LocalVisitImportStorage(root))
            worker = Worker(db)
            import_id = str(uuid4())
            service._id_factory = lambda: import_id
            inserted = asyncio.Event()
            async def insert_then_pause(*args):
                await original_register(*args)
                inserted.set()
                await asyncio.Future()
            visit_upload.create_import = insert_then_pause
            task = asyncio.create_task(service.upload(actor, source=Source(b"before commit"), filename="before.txt"))
            try:
                await asyncio.wait_for(inserted.wait(), 3)
                async with db.transaction(actor, readonly=True) as conn:
                    assert await conn.fetchval("SELECT count(*) FROM activity.visit_import WHERE id=$1::uuid", import_id) == 0
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("pre-commit cancellation was swallowed")
            finally:
                visit_upload.create_import = original_register
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            async with db.transaction(actor, readonly=True) as conn:
                assert await conn.fetchval("SELECT count(*) FROM activity.visit_import WHERE id=$1::uuid", import_id) == 0
                assert await conn.fetchval("SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid", import_id) == 0
            assert not list(root.iterdir())
            results.append("cancel_before_commit_rolls_back_both_rows_and_removes_original")

            # Unknown outcome must retain even if this particular transaction
            # really rolls back: a client cannot infer server commit from an error.
            import_id = str(uuid4())
            waiting = asyncio.Event()
            async def pause_commit():
                waiting.set()
                await asyncio.Future()
            db.before_commit = pause_commit
            task = asyncio.create_task(service.upload(actor, source=Source(b"unknown commit"), filename="unknown.txt"))
            try:
                await asyncio.wait_for(waiting.wait(), 3)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            finally:
                db.before_commit = None
            assert (root / (import_id + ".txt")).read_bytes() == b"unknown commit"
            async with db.transaction(actor, readonly=True) as conn:
                assert await conn.fetchval("SELECT count(*) FROM activity.visit_import WHERE id=$1::uuid", import_id) == 0
            results.append("uncertain_commit_preserves_original_for_explicit_reconciliation")

            for failure in (RuntimeError("lost response after commit"), asyncio.CancelledError()):
                import_id = str(uuid4())
                db.after_commit_failure = failure
                try:
                    await service.upload(actor, source=Source("客户已确认测试范围".encode()), filename="committed.txt")
                except type(failure):
                    pass
                else:
                    raise AssertionError("post-commit failure injection was not reached")
                path = root / (import_id + ".txt")
                assert path.exists() and not list(root.glob("*.part"))
                async with db.transaction(actor, readonly=True) as conn:
                    assert (await import_detail(conn, actor, import_id))["status"] == "queued"
                    assert await conn.fetchval("SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid", import_id) == 1
                assert await worker.run_once(job_types=("visit.import",))
                async with db.transaction(actor, readonly=True) as conn:
                    detail = await import_detail(conn, actor, import_id)
                    assert detail["status"] == "succeeded" and detail["extracted_text"] == "客户已确认测试范围"
                    assert await conn.fetchval("SELECT count(*) FROM ops.job_effect e JOIN ops.job j ON j.id=e.job_id "
                                               "WHERE j.aggregate_id=$1::uuid", import_id) == 1
                results.append("actual_commit_survives_" + type(failure).__name__ + "_and_worker_finishes")

            # A normal upload is read by the real Worker, then linked to the
            # human-confirmed archive by the existing source_import_id contract.
            import_id = str(uuid4())
            response = await service.upload(actor, source=Source("客户需求明确，约定下周复核。".encode()), filename="archive.txt")
            assert response["status"] == "queued"
            assert await worker.run_once(job_types=("visit.import",))
            async with db.transaction(actor) as conn:
                customer_id = str(await conn.fetchval("SELECT id FROM crm.customer WHERE owner_user_ref_id=$1::uuid LIMIT 1", actor.user_id))
                archived = await VisitRepository().create(conn, actor, customer_id=customer_id, fields={
                    "interaction_at": "2026-09-14", "created_date": "2026-09-14", "contact_name": "验收联系人",
                    "follow_up_record": "客户需求明确，约定下周复核。", "next_action": "9月21日由销售复核测试报告。",
                    "source_import_id": import_id, "_follow_up_quality_score": 85,
                })
            async with db.transaction(actor, readonly=True) as conn:
                assert str(await conn.fetchval("SELECT source_import_id FROM activity.visit WHERE id=$1::uuid", archived["id"])) == import_id
                assert (await VisitRepository().detail(conn, archived["id"]))["status"] == "archived"
            assert (root / (import_id + ".txt")).exists()
            results.append("success_worker_receipt_and_archived_source_are_linked")

            # Cancellation leaves a running lease and a durable original. Reclaim
            # must re-use it and produce exactly one final import effect.
            import_id = str(uuid4())
            await service.upload(actor, source=Source(b"interrupted extraction"), filename="interrupted.txt")
            started, release, finished = threading.Event(), threading.Event(), threading.Event()
            original_extract = visit_import.document_text
            def pause_extraction(*args):
                started.set()
                try:
                    assert release.wait(5)
                    return original_extract(*args)
                finally:
                    finished.set()
            visit_import.document_text = pause_extraction
            task = asyncio.create_task(worker.run_once(job_types=("visit.import",)))
            try:
                async with asyncio.timeout(3):
                    while not started.is_set():
                        await asyncio.sleep(0.01)
                task.cancel()
                try:
                    await asyncio.wait_for(task, 3)
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("Worker cancellation was swallowed")
            finally:
                release.set()
                visit_import.document_text = original_extract
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                async with asyncio.timeout(3):
                    while not finished.is_set():
                        await asyncio.sleep(0.01)
            assert (root / (import_id + ".txt")).read_bytes() == b"interrupted extraction"
            async with db.transaction(actor) as conn:
                assert (await import_detail(conn, actor, import_id))["status"] == "processing"
                assert await conn.fetchval("SELECT status FROM ops.job WHERE aggregate_id=$1::uuid", import_id) == "running"
                await conn.execute("UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' "
                                   "WHERE aggregate_id=$1::uuid", import_id)
            assert await worker.run_once(job_types=("visit.import",))
            async with db.transaction(actor, readonly=True) as conn:
                assert (await import_detail(conn, actor, import_id))["status"] == "succeeded"
                assert await conn.fetchval("SELECT attempts FROM ops.job WHERE aggregate_id=$1::uuid", import_id) == 2
                assert await conn.fetchval("SELECT count(*) FROM ops.job_effect e JOIN ops.job j ON j.id=e.job_id "
                                           "WHERE j.aggregate_id=$1::uuid", import_id) == 1
            results.append("cancelled_worker_reclaims_same_original_and_records_one_effect")

            # Failed imports keep their original and the same user retry API.
            import_id = str(uuid4())
            await service.upload(actor, source=Source(b"retryable original"), filename="retry.txt")
            original_extract = visit_import.document_text
            def invalid_document(*args):
                raise ValueError("test extraction failure")
            visit_import.document_text = invalid_document
            try:
                assert await worker.run_once(job_types=("visit.import",))
            finally:
                visit_import.document_text = original_extract
            async with db.transaction(actor) as conn:
                assert (await import_detail(conn, actor, import_id))["status"] == "failed"
                assert (await retry_import(conn, actor, import_id))["status"] == "queued"
            assert await worker.run_once(job_types=("visit.import",))
            async with db.transaction(actor, readonly=True) as conn:
                assert (await import_detail(conn, actor, import_id))["status"] == "succeeded"
            assert (root / (import_id + ".txt")).read_bytes() == b"retryable original"
            results.append("failed_import_keeps_original_and_owner_retry_succeeds")
        return results
    finally:
        visit_import.IMPORT_ROOT = prior_root
        if prior_storage_root is None:
            os.environ.pop("VISIT_IMPORT_ROOT", None)
        else:
            os.environ["VISIT_IMPORT_ROOT"] = prior_storage_root
        visit_upload.create_import = original_register
        await db.close()


async def main():
    results = []
    async def check(config, name, role):
        results.extend(await verify(config, name, role))
    await isolated_database(serve=check)
    print({"passed": results})


if __name__ == "__main__":
    asyncio.run(main())
