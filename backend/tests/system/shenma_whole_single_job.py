"""Execute one fresh, exact report job in a marked preflight clone only.

DSNs are read only from SHENMA_PREFLIGHT_MAINTENANCE_DSN and
SHENMA_PREFLIGHT_RUNTIME_DSN. Never pass secrets on argv. This does not poll,
start queue maintenance, instantiate FeishuWorker, or process another job.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pwd
import re
from dataclasses import replace
from uuid import UUID

import asyncpg

from sales_backend.config import get_settings
from sales_backend.db import Database, normalize_database_url
from sales_backend.repositories.jobs import JobRepository
from sales_backend.worker import Worker

MARKER = "shenma-whole-121-validation"
DATABASE_PATTERN = r"shenma_preflight_[0-9]{8}t[0-9]{6}z"


async def assert_clone(connection):
    row = await connection.fetchrow("""SELECT datname,
        shobj_description(oid,'pg_database') AS marker FROM pg_database
        WHERE datname=current_database()""")
    if (not row or not re.fullmatch(DATABASE_PATTERN, row["datname"])
            or row["marker"] != MARKER):
        raise ValueError("Only an explicitly marked Shenma preflight clone may execute a sample job")
    return row["datname"]


def peer_mode():
    value = os.environ.get("SHENMA_PREFLIGHT_PEER_MAINTENANCE", "")
    if value not in {"", "1"}:
        raise ValueError("Invalid explicit peer maintenance mode")
    if value and (os.getuid() != 0 or os.geteuid() != 0):
        raise ValueError("Peer maintenance mode requires an explicitly launched root process")
    return value == "1"


async def connect_maintenance(dsn, *, use_peer):
    if not use_peer:
        return await asyncpg.connect(normalize_database_url(dsn))
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError("Peer maintenance requires root before the temporary identity switch")
    postgres = pwd.getpwnam("postgres")
    if postgres.pw_uid == 0:
        raise ValueError("Postgres peer identity must be unprivileged")
    os.seteuid(postgres.pw_uid)
    try:
        return await asyncpg.connect(normalize_database_url(dsn))
    finally:
        # The helper is standalone; no worker task starts during this handshake.
        os.seteuid(0)


def drop_to_runtime():
    if os.getuid() != 0 or os.geteuid() != 0:
        raise ValueError("Permanent runtime drop requires root")
    runtime = pwd.getpwnam("shenma-sales")
    if runtime.pw_uid == 0 or runtime.pw_gid == 0:
        raise ValueError("The application identity must be unprivileged")
    os.setgroups([])
    os.setgid(runtime.pw_gid)
    os.setuid(runtime.pw_uid)
    if (os.getuid() != runtime.pw_uid or os.geteuid() != runtime.pw_uid
            or os.getgid() != runtime.pw_gid or os.getegid() != runtime.pw_gid
            or os.getgroups()):
        raise ValueError("Permanent application identity drop did not complete")
    return runtime.pw_uid


async def execute_exact(*, job_id: UUID, workspace_id: UUID, user_id: UUID,
                        aggregate_id: UUID, job_type: str):
    if job_type not in {"work_report.snapshot", "work_report.generate"}:
        raise ValueError("Only report snapshot/generate jobs are allowed")
    if any(value.int == 0 for value in (job_id, workspace_id, user_id, aggregate_id)):
        raise ValueError("Exact sample identifiers must be nonzero UUIDs")
    maintenance_dsn = os.environ.get("SHENMA_PREFLIGHT_MAINTENANCE_DSN", "")
    runtime_dsn = os.environ.get("SHENMA_PREFLIGHT_RUNTIME_DSN", "")
    if not maintenance_dsn or not runtime_dsn:
        raise ValueError("Both private preflight DSN environment variables are required")
    use_peer = peer_mode()
    maintenance = await connect_maintenance(maintenance_dsn, use_peer=use_peer)
    database = None
    try:
        name = await assert_clone(maintenance)
        if not await maintenance.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ):
            raise ValueError("The exact claim requires the clone maintenance connection")
        for kind in ("json", "jsonb"):
            await maintenance.set_type_codec(kind, schema="pg_catalog", encoder=json.dumps,
                                             decoder=json.loads, format="text")
        runtime_os_uid = drop_to_runtime() if use_peer else os.geteuid()
        settings = replace(get_settings(), database_url=runtime_dsn, database_min_pool_size=1,
                           database_max_pool_size=3, worker_id="shenma-preflight-single-job")
        database = Database(settings)
        await database.connect()
        async with database.connection() as runtime:
            if await assert_clone(runtime) != name:
                raise ValueError("Runtime and maintenance connections must target the same clone")
            if await runtime.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ):
                raise ValueError("The report must execute under a non-bypass runtime role")
        # A queue item can only be claimed when its projection confirms the same
        # exact job, workspace and author. This is not a generic queue selector.
        async with maintenance.transaction():
            await maintenance.execute("SET LOCAL lock_timeout='3s'")
            await maintenance.execute("SET LOCAL statement_timeout='10s'")
            row = await maintenance.fetchrow("""UPDATE ops.job j SET
                status='running',attempts=attempts+1,locked_by=$6,
                locked_until=clock_timestamp()+make_interval(secs=>$7),
                lease_token=gen_random_uuid(),updated_at=clock_timestamp()
                WHERE j.id=$1 AND j.workspace_id=$2 AND j.aggregate_id=$3 AND j.job_type=$4
                AND j.payload->>'workspace_id'=$2::text AND j.payload->>'user_id'=$5::text
                AND j.status='queued' AND j.attempts=0 AND j.max_attempts>=1
                AND j.created_at>=clock_timestamp()-interval '30 minutes'
                AND j.available_at<=clock_timestamp()
                AND (($4='work_report.snapshot' AND EXISTS(SELECT 1 FROM agent.work_report_snapshot s
                  WHERE s.id=$3 AND s.workspace_id=$2 AND s.owner_user_ref_id=$5
                  AND s.job_id=j.id AND s.status='preparing'))
                 OR ($4='work_report.generate' AND EXISTS(SELECT 1 FROM agent.work_report_run r
                  WHERE r.id=$3 AND r.workspace_id=$2 AND r.owner_user_ref_id=$5
                  AND r.job_id=j.id AND r.status='queued')))
                RETURNING j.*""", job_id, workspace_id, aggregate_id, job_type, user_id,
                settings.worker_id, settings.worker_lock_seconds)
            if row is None:
                raise ValueError("Exact fresh queued report job not found; nothing was claimed")
        claimed = JobRepository._claimed(row)
        await Worker(database)._execute_claimed_job(claimed)
        receipt = dict(await maintenance.fetchrow("""SELECT id::text,job_type,status AS job_status,attempts,
            last_error_code FROM ops.job WHERE id=$1 AND workspace_id=$2""", job_id, workspace_id))
        if job_type == "work_report.snapshot":
            receipt["projection_status"] = await maintenance.fetchval(
                "SELECT status FROM agent.work_report_snapshot WHERE id=$1 AND workspace_id=$2",
                aggregate_id, workspace_id,
            )
            receipt["accepted"] = receipt["job_status"] == "succeeded" and receipt["projection_status"] == "ready"
        else:
            result = await maintenance.fetchrow("""SELECT status AS projection_status,
                result->>'status' AS result_status,
                result->>'provider' AS provider,document_id::text FROM agent.work_report_run
                WHERE id=$1 AND workspace_id=$2""", aggregate_id, workspace_id)
            receipt.update(dict(result))
            receipt["accepted"] = (receipt["job_status"] == "succeeded"
                                   and receipt["projection_status"] == "succeeded"
                                   and receipt["result_status"] == "generated"
                                   and receipt["provider"] in {"direct", "agent_platform"})
        receipt["runtime_os_uid"] = runtime_os_uid
        # Never print input payloads, model output, DSNs, credentials or tokens.
        return receipt
    finally:
        if database is not None:
            await database.close()
        await maintenance.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("job-id", "workspace-id", "user-id", "aggregate-id"):
        parser.add_argument("--"+field, type=UUID, required=True)
    parser.add_argument("--job-type", choices=("work_report.snapshot", "work_report.generate"), required=True)
    args = parser.parse_args()
    try:
        receipt = asyncio.run(execute_exact(**vars(args)))
    except Exception as exc:
        # Connection errors can contain private DSNs. Emit only the fixed type.
        print(json.dumps({"accepted": False, "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
