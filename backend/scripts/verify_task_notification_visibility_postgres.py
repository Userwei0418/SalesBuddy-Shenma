# ruff: noqa: E402, E501, S108, S603, S608 -- Private cluster and fixed SQL.
"""Private PG regression: cancelled/revoked/stale-round task notifications stay hidden."""

import argparse
import asyncio
import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from uuid import uuid4

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", required=True)
args = parser.parse_args()
BASE = pathlib.Path(args.output).resolve()
BASE.mkdir(parents=True, exist_ok=True)
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend/src"), str(ROOT / "backend/scripts"), str(ROOT / "database/scripts")]
import asyncpg
from migrate import migrate
from verify_review_targets_postgres import grants

from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.notifications import NotificationRepository

PG = pathlib.Path("/opt/homebrew/opt/postgresql@16/bin")


async def probes(sock):
    c = await asyncpg.connect(
        host=str(sock), port=55439, user="postgres", database="postgres", password="", passfile="/dev/null", ssl=False
    )
    result = {
        "base_commit": "cdabd250f977e1477d3a8957e0f0f41a6dd5a565",
        "checks": [],
        "evidence_level": "isolated PostgreSQL + repository; synthetic identities, not JWT/HTTP",
        "shared_runtime_accessed": False,
    }
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            await migrate(c, ROOT / "database")
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        result["schema"] = await c.fetchval(
            "SELECT version FROM ops.schema_migration ORDER BY substring(version from 2)::integer DESC LIMIT 1"
        )
        result["postgres"] = await c.fetchval("SHOW server_version")
        await c.execute("CREATE ROLE review_api NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")
        await grants(c, "review_api")
        await c.execute("SET row_security=on")
        await c.execute("CREATE ROLE review_worker NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")
        await c.execute("GRANT USAGE ON SCHEMA workflow TO review_worker")
        await c.execute(
            "GRANT EXECUTE ON FUNCTION workflow.task_execution_enqueue_pause_rechecks(integer) TO review_worker"
        )
        ws, team = uuid4(), uuid4()
        await c.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Review only')", ws, str(ws)
        )
        await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        await c.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'Synthetic team')",
            team,
            ws,
            str(team),
        )
        actors = {}
        for label in ("reviewer", "executor"):
            uid = uuid4()
            await c.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)",
                uid,
                ws,
                label,
            )
            await c.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'operations','workspace',$3)",
                ws,
                uid,
                team,
            )
            await c.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)", ws, uid)
            actors[label] = ActorContext(
                workspace_id=str(ws), user_id=str(uid), role="operations", data_scope="workspace", team_ids=[str(team)]
            )

        async def seed_task(title):
            tid = uuid4()
            await c.execute(
                "INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,creator_team_id,status,due_at,association_kind) VALUES($1,$2,$3,$3,$4,$5,'in_progress',clock_timestamp()+interval '1 day','daily')",
                tid,
                ws,
                title,
                actors["reviewer"].user_id,
                team,
            )
            await c.execute(
                "INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'operations','owner')",
                tid,
                ws,
                actors["executor"].user_id,
                team,
            )
            await c.execute(
                "INSERT INTO workflow.task_execution(task_id,workspace_id,state) VALUES($1,$2,workflow.task_execution_initial($1))",
                tid,
                ws,
            )
            return tid

        @contextlib.asynccontextmanager
        async def runtime(actor):
            async with c.transaction():
                await c.execute("SET LOCAL ROLE review_api")
                await set_request_context(c, actor)
                assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                yield

        tid2 = await seed_task("CONFIDENTIAL reminder title")
        pause_id = uuid4()
        await c.execute(
            "UPDATE workflow.task_execution SET state=jsonb_set(state,'{pause}',$2::jsonb) WHERE task_id=$1",
            tid2,
            {
                "id": str(pause_id),
                "actor_user_id": actors["reviewer"].user_id,
                "reason": "CONFIDENTIAL pause reason",
                "recheck_at": "2026-01-01T00:00:00Z",
                "recheck_version": 1,
            },
        )

        async def worker():
            async with c.transaction():
                await c.execute("SET LOCAL ROLE review_worker")
                await c.execute("SELECT set_config('app.workspace_id','',true),set_config('app.user_ref_id','',true)")
                return await c.fetchval("SELECT workflow.task_execution_enqueue_pause_rechecks(100)")

        result["reminders_enqueued"] = await worker()
        repo = NotificationRepository()
        recipient = actors["executor"]
        nid = await c.fetchval(
            "SELECT id FROM workflow.notification WHERE object_id=$1 AND recipient_user_ref_id=$2",
            tid2,
            recipient.user_id,
        )
        assert nid is not None
        general = await c.fetchval(
            "INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body) VALUES($1,$2,'general','Public notification','General message') RETURNING id",
            ws,
            recipient.user_id,
        )

        async def visible(unread=True, limit=100):
            async with runtime(recipient):
                rows = await repo.list(c, unread_only=unread, limit=limit)
                return {row["id"]: row for row in rows}

        async def mark(identifier):
            async with runtime(recipient):
                return await repo.mark_read(c, notification_id=str(identifier))

        original_pause = await c.fetchval("SELECT state->'pause' FROM workflow.task_execution WHERE task_id=$1", tid2)
        assert {str(nid), str(general)} <= set(await visible())
        assert await mark(general)
        assert str(general) not in await visible()
        assert str(general) in await visible(False)
        result["checks"].append(
            "authorized current pause visible; unrelated notification marks read and retains history"
        )
        await c.execute(
            "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'task.read','deny',NULL)",
            ws,
            recipient.user_id,
        )
        assert await c.fetchval("SELECT status FROM workflow.notification WHERE id=$1", nid) == "pending"
        assert not await visible()
        assert str(nid) not in await visible(False)
        assert not await mark(nid)
        assert await c.fetchval("SELECT status FROM workflow.notification WHERE id=$1", nid) == "pending"
        result["checks"].append(
            "task.read revocation hides pending reminder immediately before worker cancellation; mark read denied"
        )
        result["reminders_after_revoke_run"] = await worker()
        assert await c.fetchval("SELECT status FROM workflow.notification WHERE id=$1", nid) == "cancelled"
        await c.execute(
            "DELETE FROM config.account_permission_override WHERE user_ref_id=$1 AND permission_code='task.read'",
            recipient.user_id,
        )
        assert str(nid) not in await visible(False)
        assert not await mark(nid)
        assert await c.fetchval("SELECT status FROM workflow.notification WHERE id=$1", nid) == "cancelled"
        result["checks"].append("cancelled reminder never returned or resurrected even after task permission restored")
        await c.execute("UPDATE workflow.notification SET status='pending' WHERE id=$1", nid)
        assert str(nid) in await visible()
        await c.execute(
            "UPDATE workflow.task_execution SET state=jsonb_set(state,'{pause,recheck_version}','2') WHERE task_id=$1",
            tid2,
        )
        assert str(nid) not in await visible(False) and not await mark(nid)
        await c.execute(
            "UPDATE workflow.task_execution SET state=jsonb_set(state,'{pause}',$2) WHERE task_id=$1",
            tid2,
            original_pause,
        )
        assert str(nid) in await visible()
        await c.execute(
            "UPDATE workflow.task_execution SET state=jsonb_set(state,'{pause}','null') WHERE task_id=$1", tid2
        )
        assert str(nid) not in await visible(False) and not await mark(nid)
        await c.execute(
            "UPDATE workflow.task_execution SET state=jsonb_set(state,'{pause}',$2) WHERE task_id=$1",
            tid2,
            original_pause,
        )
        result["checks"].append(
            "superseded pause round and explicit resume immediately hide old reminder without worker"
        )
        await c.execute(
            "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'task.complete','deny',NULL)",
            ws,
            recipient.user_id,
        )
        assert str(nid) not in await visible(False) and not await mark(nid)
        await c.execute(
            "DELETE FROM config.account_permission_override WHERE user_ref_id=$1 AND permission_code='task.complete'",
            recipient.user_id,
        )
        assert str(nid) in await visible()
        result["checks"].append("reminder requires current responsibility-specific action grant as well as task.read")
        await c.execute("UPDATE workflow.notification SET status='cancelled' WHERE id=$1", nid)
        unread_general = await c.fetchval(
            "INSERT INTO workflow.notification(workspace_id,recipient_user_ref_id,template_code,title,body,created_at) VALUES($1,$2,'general','Remaining','General',clock_timestamp()-interval '1 day') RETURNING id",
            ws,
            recipient.user_id,
        )
        assert set(await visible(limit=1)) == {str(unread_general)}
        result["checks"].append(
            "visibility filtering precedes LIMIT so cancelled rows do not consume unread page/count"
        )
        result["probe_completed"] = True
    finally:
        await c.close()
    return result


def main():
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR") if k in os.environ}
    with tempfile.TemporaryDirectory(prefix="sb122-task-review.", dir="/tmp") as name:
        p = pathlib.Path(name)
        sock = p / "socket"
        sock.mkdir(mode=0o700)
        subprocess.run(
            [str(PG / "initdb"), "-D", str(p / "pg"), "-U", "postgres", "-A", "trust", "--no-locale", "-E", "UTF8"],
            check=True,
            capture_output=True,
            env=env,
        )
        started = False
        try:
            subprocess.run(
                [
                    str(PG / "pg_ctl"),
                    "-D",
                    str(p / "pg"),
                    "-l",
                    str(p / "postgres.log"),
                    "-o",
                    f"-k {sock} -p 55439 -c listen_addresses='' -c unix_socket_permissions=0700",
                    "-w",
                    "start",
                ],
                check=True,
                capture_output=True,
                env=env,
            )
            started = True
            result = asyncio.run(probes(sock))
        finally:
            if started:
                stopped = subprocess.run(
                    [str(PG / "pg_ctl"), "-D", str(p / "pg"), "-m", "immediate", "-w", "stop"],
                    check=True,
                    capture_output=True,
                    env=env,
                )
        result["private_cluster_stopped"] = stopped.returncode == 0
    result["private_cluster_removed"] = not p.exists()
    (BASE / "PROBE_RESULT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
