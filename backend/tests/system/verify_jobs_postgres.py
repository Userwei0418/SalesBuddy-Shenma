"""Real PostgreSQL/worker-process recovery tests in a self-created, disposable database.

Run as the PostgreSQL maintenance OS user with the backend runtime. No model call,
production business data, or live worker interruption is used in these fault tests.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
import uuid
from dataclasses import replace

import asyncpg

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / 'backend/src'))
sys.path.insert(0, str(REPO / 'database/scripts'))
from migrate import migrate
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.job_context import JobLease, JobLeaseLost, current_job_lease
from sales_backend.repositories.jobs import JobRepository, record_job_effect
from sales_backend.worker import Worker
from sales_backend.config import get_settings


def settings(name):
    host, user = os.environ.get('PGHOST', '/tmp'), os.environ.get('PGUSER', 'postgres')
    return replace(get_settings(),
        app_env='test', auth_mode='password', access_token_secret='isolated-worker-test-signing-secret-not-production',
        database_url=f'postgresql:///{name}?host={quote(host)}&user={quote(user)}',
        database_min_pool_size=1, database_max_pool_size=4, worker_id='recovery-probe',
        worker_poll_seconds=0.1, worker_lock_seconds=3,
        worker_import_concurrency=1, worker_interactive_concurrency=2, worker_review_concurrency=1,
        worker_database_max_pool_size=8,
    )


class ProbeWorker(Worker):
    def __init__(self, database, mode='normal'):
        super().__init__(database)
        self.mode = mode

    async def _handle_job(self, job):
        actor = self._job_actor(job)
        if self.mode == 'before':
            async with self.database.transaction(actor) as conn:
                await conn.execute('INSERT INTO ops.probe_started(job_id) VALUES($1::uuid)', job.id)
            await asyncio.sleep(3600)
        if self.mode == 'slow':
            await asyncio.sleep(4.2)
        async with self.database.transaction(actor) as conn:
            await conn.execute('INSERT INTO ops.probe_result(job_id) VALUES($1::uuid)', job.id)
            await record_job_effect(conn, actor.workspace_id)
        if self.mode == 'after':
            await asyncio.sleep(3600)


class ConcurrentProbeWorker(ProbeWorker):
    async def _handle_job(self, job):
        if job.job_type == 'visit.import':
            actor = self._job_actor(job)
            async with self.database.transaction(actor) as conn:
                await conn.execute('INSERT INTO ops.probe_started(job_id) VALUES($1::uuid)', job.id)
            try:
                await asyncio.Future()
            finally:
                async with self.database.transaction(actor) as conn:
                    await conn.execute('INSERT INTO ops.probe_stopped(job_id) VALUES($1::uuid)', job.id)
        else:
            await super()._handle_job(job)


async def child(name, mode):
    assert name.startswith('salegent_verify_')
    if mode == 'concurrent':
        # Use the real entrypoint, SIGTERM handler, database pool and lane scheduler;
        # only the paid/external handlers are replaced with deterministic DB effects.
        import sales_backend.worker as worker_module
        worker_module.get_settings = lambda: settings(name)
        worker_module.Worker = ConcurrentProbeWorker
        await worker_module.main()
        return
    db = Database(settings(name))
    await db.connect()
    try:
        await ProbeWorker(db, mode).run_once()
    finally:
        await db.close()


async def main():
    config = {'host': os.environ.get('PGHOST', '/tmp'), 'user': os.environ.get('PGUSER', 'postgres')}
    admin = await asyncpg.connect(database='postgres', **config)
    name = 'salegent_verify_jobs_' + uuid.uuid4().hex[:12]
    conn = db = None
    results = []
    processes = []
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn = await asyncpg.connect(database=name, **config)
        await migrate(conn)
        workspace, user = uuid.uuid4(), uuid.uuid4()
        await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离故障测试')",
                           workspace, str(workspace))
        await conn.execute('CREATE TABLE ops.probe_result(job_id uuid); CREATE TABLE ops.probe_started(job_id uuid); '
                           'CREATE TABLE ops.probe_stopped(job_id uuid)')
        payload = json.dumps({'user_id': str(user), 'role': 'manager', 'data_scope': 'workspace', 'team_ids': []})
        async def enqueue(max_attempts=3, job_type='test.probe'):
            return await conn.fetchval('''INSERT INTO ops.job(workspace_id,job_type,payload,max_attempts)
                VALUES($1,$4,$2::jsonb,$3) RETURNING id''', workspace, payload, max_attempts, job_type)
        db = Database(settings(name))
        await db.connect()
        actor = ActorContext(workspace_id=str(workspace), user_id=str(user), role=RoleCode.MANAGER,
                             data_scope=DataScope.WORKSPACE, team_ids=())
        jobs = JobRepository()
        await enqueue()
        old = await jobs.claim(conn, worker_id='old-process', lock_seconds=3)
        await conn.execute("UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1::uuid", old.id)
        fresh = await jobs.claim(conn, worker_id='new-process', lock_seconds=30)
        assert fresh.id == old.id and fresh.attempts == old.attempts+1 and fresh.lease_token != old.lease_token
        for operation in [lambda: jobs.heartbeat(conn, job=old, lock_seconds=3), lambda: jobs.succeed(conn, job=old)]:
            try:
                await operation()
                raise AssertionError('stale operation accepted')
            except JobLeaseLost:
                pass
        token = current_job_lease.set(JobLease(old.id, old.lease_token))
        try:
            try:
                async with db.transaction(actor) as fenced:
                    await fenced.execute('INSERT INTO ops.probe_result VALUES($1::uuid)', old.id)
                raise AssertionError('stale business result accepted')
            except JobLeaseLost:
                pass
        finally:
            current_job_lease.reset(token)
        await jobs.succeed(conn, job=fresh)
        results.append('expired_running_reclaimed_and_stale_heartbeat_ack_and_result_rejected')
        slow_id = await enqueue()
        assert await ProbeWorker(db, 'slow').run_once()
        assert await conn.fetchval('SELECT status FROM ops.job WHERE id=$1', slow_id) == 'succeeded'
        assert await conn.fetchval('SELECT attempts FROM ops.job WHERE id=$1', slow_id) == 1
        results.append('generic_heartbeat_keeps_long_handler_alive')
        for mode, probe_table in [('before', 'ops.probe_started'), ('after', 'ops.probe_result')]:
            job_id = await enqueue()
            process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__)), '--child', name, mode,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            processes.append(process)
            for _ in range(100):
                if await conn.fetchval(f'SELECT EXISTS(SELECT 1 FROM {probe_table} WHERE job_id=$1)', job_id):
                    break
                if process.returncode is not None:
                    raise AssertionError('probe subprocess stopped before marker')
                await asyncio.sleep(0.1)
            else:
                raise AssertionError('probe process did not reach expected commit')
            process.kill()
            await process.communicate()
            await asyncio.sleep(3.3)
            handled = await ProbeWorker(db).run_once()
            assert handled is (mode == 'before')
            assert await conn.fetchval('SELECT status FROM ops.job WHERE id=$1', job_id) == 'succeeded'
            assert await conn.fetchval('SELECT count(*) FROM ops.probe_result WHERE job_id=$1', job_id) == 1
            assert await conn.fetchval('SELECT count(*) FROM ops.job_effect WHERE job_id=$1', job_id) == 1
            results.append('sigkill_'+mode+'_result_commit_recovers_without_duplicate_business_result')
        exhausted_id = await enqueue(max_attempts=1)
        await jobs.claim(conn, worker_id='disappeared', lock_seconds=3)
        await conn.execute("UPDATE ops.job SET locked_until=clock_timestamp()-interval '1 second' WHERE id=$1", exhausted_id)
        assert await jobs.claim(conn, worker_id='next', lock_seconds=3) is None
        assert await conn.fetchval('SELECT status FROM ops.job WHERE id=$1', exhausted_id) == 'dead_letter'
        results.append('interrupted_attempt_budget_is_bounded')

        # Real parallel claims: a long import holds its one slot while two short
        # interactive jobs and a review complete; later imports stay unclaimed.
        import_ids = [await enqueue(job_type='visit.import') for _ in range(4)]
        short_ids = [await enqueue(job_type=kind) for kind in ['agent.run', 'business.advice', 'battle_map.review']]
        process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__)), '--child', name,
            'concurrent', stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        processes.append(process)
        for _ in range(100):
            if await conn.fetchval('SELECT count(*) FROM ops.probe_result WHERE job_id=ANY($1::uuid[])', short_ids) == 3:
                break
            if process.returncode is not None:
                raise AssertionError('concurrent worker stopped before short jobs finished')
            await asyncio.sleep(0.05)
        else:
            raise AssertionError('short jobs did not bypass the blocked import within 5 seconds')
        import_state = await conn.fetch('SELECT attempts,status FROM ops.job WHERE id=ANY($1::uuid[])', import_ids)
        assert sum(r['attempts'] for r in import_state) == 1
        assert sum(r['status'] == 'running' for r in import_state) == 1
        results.append('real_typed_claims_allow_short_jobs_while_import_backlog_keeps_unclaimed_leases')
        # Let one heartbeat interval pass, then use the actual SIGTERM entrypoint.
        await asyncio.sleep(1.2)
        process.terminate()
        await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 0
        assert await conn.fetchval('SELECT count(*) FROM ops.probe_stopped WHERE job_id=ANY($1::uuid[])', import_ids) == 1
        await asyncio.sleep(3.2)
        for _ in import_ids:
            assert await ProbeWorker(db).run_once(job_types=('visit.import',))
        assert await conn.fetchval('SELECT count(*) FROM ops.probe_result WHERE job_id=ANY($1::uuid[])', import_ids) == 4
        assert await conn.fetchval('SELECT count(*) FROM ops.job_effect WHERE job_id=ANY($1::uuid[])', import_ids) == 4
        assert await conn.fetchval('SELECT max(attempts) FROM ops.job WHERE id=ANY($1::uuid[])', import_ids) == 2
        results.append('sigterm_cancels_all_handlers_and_leases_recover_once_without_duplicate_result')
        print(json.dumps({'checks': results, 'passed': len(results)}, ensure_ascii=False))
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.communicate()
        if db:
            await db.close()
        if conn:
            await conn.close()
        assert name.startswith('salegent_verify_')
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def bounded_main():
    # Failure still enters main's finally: terminate only our subprocesses and drop
    # only the generated salegent_verify_* database. Never leave a probe daemon.
    async with asyncio.timeout(120):
        await main()


if __name__ == '__main__':
    asyncio.run(child(sys.argv[2], sys.argv[3]) if len(sys.argv)>1 and sys.argv[1]=='--child' else bounded_main())
