"""Opt-in V257 proof using a disposable PG16 database and restricted API role.

Only an explicit private Unix socket is accepted; never reads a saved DSN.
HTTP uses ASGI with injected synthetic identities, not production authentication.
"""

# ruff: noqa: E402, E501, S608, S108 -- Explicit private disposable socket only.
import argparse
import asyncio
import hashlib
import io
import json
import sys
from contextlib import asynccontextmanager, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/scripts"))
from verify_review_targets_postgres import apply, grants, migrate_through
from verify_review_targets_postgres import item as migration_item

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.task_execution import router
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.permission_catalog import CATALOG, PermissionDefinition
from sales_backend.services.task_execution import initialize_execution
from sales_backend.services.tasks import TaskService


async def verify(args):
    socket = str(Path(args.socket).resolve())
    if not socket.startswith(("/tmp/sb122-task-pg.", "/private/tmp/sb122-task-pg.")):
        raise ValueError("Requires a private task test socket")
    config = dict(host=socket, port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    name, role = "sb122_task_" + uuid4().hex[:10], "sb122_task_api_" + uuid4().hex[:10]
    worker_role = "sb122_task_worker_" + uuid4().hex[:10]
    report = {
        "status": "started",
        "schema": "V255+V257",
        "production_accessed": False,
        "http_auth": "injected synthetic identity; real RLS/command database",
        "checks": [],
        "samples": {},
    }
    c = None
    try:
        await admin.execute(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8'")
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 255)
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        ws, team = uuid4(), uuid4()
        await c.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Task fixture')", ws, str(ws)
        )
        await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        await c.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'Task team')", team, ws, str(team)
        )
        actors = {}
        for label in ("reviewer", "executor", "new_executor", "new_reviewer", "outsider"):
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
            actors[label] = ActorContext(
                workspace_id=str(ws), user_id=str(uid), role="operations", data_scope="workspace", team_ids=[str(team)]
            )
        # Historical pending-review task exists before V257 and has no checklist.
        legacy = uuid4()
        await c.execute(
            "INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,creator_team_id,status,due_at,association_kind,completion_note,completion_submitted_at) VALUES($1,$2,'Legacy','Legacy',$3,$4,'pending_review',clock_timestamp()+interval '10 day','daily','original result',clock_timestamp())",
            legacy,
            ws,
            actors["reviewer"].user_id,
            team,
        )
        await c.execute(
            "INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'operations','owner')",
            legacy,
            ws,
            actors["executor"].user_id,
            team,
        )
        migration_path = ROOT / "database/migrations/V257__task_execution_lifecycle.sql"
        await apply(c, migration_item("V257", migration_path), ROOT / "database")
        report["migration_sha256"] = hashlib.sha256(migration_path.read_bytes()).hexdigest()
        report["postgres_version"] = await c.fetchval("SHOW server_version")
        ledger = [
            dict(row)
            for row in await c.fetch(
                "SELECT migration_key,checksum_sha256 FROM ops.migration_checksum ORDER BY migration_key"
            )
        ]
        report["ledger_count"] = len(ledger)
        report["ledger_sha256"] = hashlib.sha256(
            json.dumps(ledger, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert any(
            row["migration_key"] == "V257" and row["checksum_sha256"] == report["migration_sha256"] for row in ledger
        )
        report["runtime_role"] = {"superuser": False, "bypassrls": False}
        report["checks"].append(
            "migration applied over complete V255 history; legacy requirements retain unknown progress"
        )
        assert (
            await c.fetchval(
                "SELECT count(*) FROM config.permission_role_grant WHERE permission_code IN ('task.feedback','task.reviewer_handover') AND workspace_id=$1",
                ws,
            )
            == 0
        )
        report["checks"].append(
            "new SQL catalog and future inherit templates register without adding rights to existing tenant roles"
        )
        for permission in ("task.feedback", "task.reviewer_handover"):
            # Explicit prospective catalog fixture; Backend owns production registration.
            CATALOG[permission] = PermissionDefinition(
                permission, permission, "task", ("self", "assigned", "teams", "workspace")
            )
            await c.execute(
                "INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code) SELECT workspace_id,id,$1,'workspace' FROM config.permission_role WHERE workspace_id=$2 AND builtin_role_code='operations'",
                permission,
                ws,
            )
        await c.execute(
            "UPDATE config.permission_role_grant SET scope_code='self' WHERE workspace_id=$1 AND permission_code IN ('task.read','task.review')",
            ws,
        )
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
        await grants(c, role)
        await c.execute("SET row_security=on")
        for signature in (
            "workflow.task_execution_read(uuid)",
            "workflow.task_execution_summary(uuid)",
            "workflow.task_execution_command(uuid,text,jsonb,uuid)",
            "workflow.task_execution_initialize(uuid,jsonb)",
            "workflow.task_execution_candidates(uuid,text,text,integer,integer)",
        ):
            await c.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')
        for table in ("task_execution", "task_execution_event", "task_execution_submission"):
            await c.execute(f'GRANT SELECT ON workflow.{table} TO "{role}"')
        await c.execute(f'CREATE ROLE "{worker_role}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE')
        await c.execute(f'GRANT USAGE ON SCHEMA workflow TO "{worker_role}"')
        await c.execute(
            f'GRANT EXECUTE ON FUNCTION workflow.task_execution_enqueue_pause_rechecks(integer) TO "{worker_role}"'
        )
        assert await c.fetchval("SELECT module FROM config.permission_catalog WHERE code='task.feedback'") == "待办事项"
        for denied_role, signature in (
            (role, "workflow.task_execution_enqueue_pause_rechecks(integer)"),
            (role, "workflow.task_execution_pause_recheck_recipients(uuid)"),
            (worker_role, "workflow.task_execution_summary(uuid)"),
            (worker_role, "workflow.task_execution_command(uuid,text,jsonb,uuid)"),
        ):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", denied_role, signature)

        async def run_worker(connection=c, limit=100):
            async with connection.transaction():
                await connection.execute(f'SET LOCAL ROLE "{worker_role}"')
                await connection.execute(
                    "SELECT set_config('app.workspace_id','',true),set_config('app.user_ref_id','',true)"
                )
                assert not await connection.fetchval(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
                count = await connection.fetchval("SELECT workflow.task_execution_enqueue_pause_rechecks($1)", limit)
                assert await connection.fetchval("SELECT current_setting('app.workspace_id')") == ""
                return count

        active = actors["reviewer"]

        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False):
                async with c.transaction(readonly=readonly):
                    await c.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(c, actor)
                    assert not await c.fetchval(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                    yield c

        db = DB()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database] = lambda: db
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=active)
        definitions = [
            {"item_id": str(uuid4()), "definition_version": 1, "title": title, "acceptance_criteria": title + " result"}
            for title in ("one", "two")
        ]
        async with db.transaction(actors["reviewer"]) as tx:
            created = await TaskService().create(
                tx,
                actor=actors["reviewer"],
                description="Synthetic task",
                due_at=datetime.now(UTC) + timedelta(days=20),
                priority_code="normal",
                assignee_account_code="executor",
            )
            task = created["id"]
            await initialize_execution(tx, task, definitions)
        report["checks"].append(
            "restricted normal TaskService creation plus same-transaction checklist initialization persists"
        )
        try:
            async with db.transaction(actors["reviewer"]) as tx:
                await TaskService().create(
                    tx,
                    actor=actors["reviewer"],
                    description="No checklist",
                    due_at=datetime.now(UTC) + timedelta(days=20),
                    priority_code="normal",
                    assignee_account_code="executor",
                )
        except asyncpg.CheckViolationError as exc:
            assert "INITIAL_CHECKLIST_REQUIRED" in str(exc)
        else:
            raise AssertionError("creation bypassed initialization gate")
        report["checks"].append(
            "deferred database creation gate rejects every source omitting checklist initialization"
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:

            async def read(actor="executor", tid=task):
                nonlocal active
                active = actors[actor]
                res = await client.get(f"/api/v1/tasks/{tid}/execution")
                assert res.status_code == 200, res.text
                result = res.json()
                async with db.transaction(actors[actor], readonly=True) as tx:
                    summary = await tx.fetchval("SELECT workflow.task_execution_summary($1::uuid)", tid)
                assert "events" not in summary and "submissions" not in summary
                assert summary == {key: value for key, value in result.items() if key not in ("events", "submissions")}
                return result

            async def command(action, extra, actor="executor", expected=200, body=None, key=None, tid=task):
                nonlocal active
                state = await read("reviewer" if expected == 403 else actor, tid)
                payload = body or {
                    **{x: state[x] for x in ("version_no", "requirements_version", "responsibility_version")},
                    **extra,
                }
                active = actors[actor]
                mutation_key = key or str(uuid4())
                replaying = await c.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM workflow.task_execution_request WHERE key=$1::uuid)", mutation_key
                )
                events_before = await c.fetchval(
                    "SELECT count(*) FROM workflow.task_execution_event WHERE task_id=$1::uuid", tid
                )
                res = await client.post(
                    f"/api/v1/tasks/{tid}/execution/{action}",
                    json=payload,
                    headers={"Idempotency-Key": mutation_key},
                )
                assert res.status_code == expected, (action, expected, res.status_code, res.text)
                result = res.json()
                if expected == 200:
                    actual_version = await c.fetchval("SELECT version_no FROM workflow.task WHERE id=$1::uuid", tid)
                    events_after = await c.fetchval(
                        "SELECT count(*) FROM workflow.task_execution_event WHERE task_id=$1::uuid", tid
                    )
                    if replaying or not result["changed"]:
                        assert actual_version == state["version_no"] and events_after == events_before, action
                    else:
                        assert actual_version == state["version_no"] + 1, (action, state["version_no"], actual_version)
                        assert events_after == events_before + 1, action
                    assert result["version_no"] == result["execution"]["version_no"], action
                    if not replaying:
                        assert result["version_no"] == actual_version, action
                    if result["event_id"]:
                        event_version = await c.fetchval(
                            "SELECT (body->>'version_no')::integer FROM workflow.task_execution_event WHERE id=$1::uuid",
                            result["event_id"],
                        )
                        assert event_version == result["version_no"], action
                        notices = await c.fetch(
                            "SELECT (payload->>'event_version')::integer AS v FROM workflow.notification WHERE payload->>'event_id'=$1",
                            result["event_id"],
                        )
                        assert all(row["v"] == event_version for row in notices), action
                        report.setdefault("version_checks", {}).setdefault(action, {"calls": 0, "notifications": 0})
                        report["version_checks"][action]["calls"] += 1
                        report["version_checks"][action]["notifications"] += len(notices)
                    if action == "submit_completion":
                        snapshot = next(
                            row["snapshot"]
                            for row in result["execution"]["submissions"]
                            if row["id"] == result["execution"]["current_submission"]
                        )
                        assert snapshot["version_no"] == result["version_no"]
                        declared = snapshot.get("actual_contribution")
                        if payload.get("actual_contribution"):
                            assert declared["task_version"] == result["version_no"]
                return result, payload

            if getattr(args, "focus", "full") == "contribution":
                from verify_task_completion_contribution_postgres import verify_contribution

                await verify_contribution(c, db, actors, ws, team, task, legacy, read, command, report)
                report["status"] = "passed"
                print(json.dumps({k: v for k, v in report.items() if k != "samples"}, ensure_ascii=False))
                return

            try:
                async with db.transaction(actors["outsider"], readonly=True) as tx:
                    await tx.fetchval("SELECT workflow.task_execution_summary($1::uuid)", task)
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise AssertionError("summary exposed a task outside current grants")
            report["checks"].append(
                "summary has same object authority and exact core fields as detail across every lifecycle read; no events/submissions"
            )
            active = actors["reviewer"]
            candidates = await client.get(
                f"/api/v1/tasks/{task}/execution/candidates",
                params={"purpose": "executor", "q": "new", "limit": 1, "offset": 0},
            )
            assert candidates.status_code == 200, candidates.text
            directory = candidates.json()
            assert directory["total"] == 2 and len(directory["items"]) == 1 and directory["limit"] == 1
            assert set(directory["items"][0]) == {"user_id", "display_name", "teams"}
            next_page = (
                await client.get(
                    f"/api/v1/tasks/{task}/execution/candidates",
                    params={"purpose": "executor", "q": "new", "limit": 1, "offset": 1},
                )
            ).json()
            assert next_page["items"][0]["user_id"] != directory["items"][0]["user_id"]
            reviewer_directory = (
                await client.get(f"/api/v1/tasks/{task}/execution/candidates", params={"purpose": "reviewer"})
            ).json()
            assert not (
                {actors["executor"].user_id, actors["reviewer"].user_id}
                & {r["user_id"] for r in reviewer_directory["items"]}
            )
            active = actors["executor"]
            forbidden_directory = await client.get(
                f"/api/v1/tasks/{task}/execution/candidates", params={"purpose": "reviewer"}
            )
            # Coordinator permission is workspace-scoped in this fixture, so also
            # test an explicit scope reduction through the existing role grant.
            assert forbidden_directory.status_code == 200
            await c.execute(
                "UPDATE config.permission_role_grant SET scope_code='self' WHERE workspace_id=$1 AND permission_code='task.reviewer_handover'",
                ws,
            )
            forbidden_directory = await client.get(
                f"/api/v1/tasks/{task}/execution/candidates", params={"purpose": "reviewer"}
            )
            assert forbidden_directory.status_code == 403
            active = actors["reviewer"]
            await c.execute(
                "UPDATE platform.user_ref SET status='inactive' WHERE id=$1", actors["new_executor"].user_id
            )
            filtered = (
                await client.get(
                    f"/api/v1/tasks/{task}/execution/candidates", params={"purpose": "executor", "q": "new"}
                )
            ).json()
            assert filtered["total"] == 1
            await command(
                "initiate_executor_handover",
                {"target_user_id": actors["new_executor"].user_id, "reason": "stale directory target"},
                actor="reviewer",
                expected=403,
            )
            await c.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", actors["new_executor"].user_id)
            report["checks"].append(
                "purpose-authorized candidate directory pages exact eligible users; inactive target disappears and stale selection is rejected by same write predicate"
            )

            await command("accept_task", {})
            s = await read()
            assert s["status"] == "pending_execution" and s["progress"]["percent"] == 0
            report["checks"].append("initial acceptance uses three versions and retains zero checklist progress")
            progress_body = {
                "item_id": definitions[0]["item_id"],
                "definition_version": 1,
                "state": "completed",
                "note": "verified result one",
            }
            key = str(uuid4())
            r, body = await command("set_item_progress", progress_body, key=key)
            assert r["execution"]["progress"]["percent"] == 50 and r["execution"]["status"] == "in_progress"
            replay, _ = await command("set_item_progress", {}, body=body, key=key)
            assert replay == r
            await command("set_item_progress", {}, body={**body, "note": "different"}, key=key, expected=409)
            await command("set_item_progress", progress_body, body=body, expected=409)
            nochange, _ = await command("set_item_progress", progress_body)
            assert nochange["changed"] is False and nochange["event_id"] is None
            report["checks"].append(
                "equal-weight persistent progress; stale lock conflict; idempotent identical replay/conflict; true no-op does not add version/event"
            )
            s = await read("reviewer")
            assert s["progress"]["percent"] == 50
            r, _ = await command("append_feedback", {"text": "reviewer asks detail"}, actor="reviewer")
            first_feedback = r["event_id"]
            await command("append_feedback", {"text": "executor answer", "reply_to_event_id": first_feedback})
            await command(
                "append_feedback", {"text": "correct another person", "corrects_event_id": first_feedback}, expected=403
            )
            await command(
                "append_feedback",
                {"text": "reviewer correction", "corrects_event_id": first_feedback},
                actor="reviewer",
            )
            s = await read()
            assert s["progress"]["percent"] == 50
            report["checks"].append(
                "append/reply/correction persisted; another author cannot rewrite/correct facts; feedback does not change progress"
            )
            r, _ = await command(
                "pause",
                {"reason": "waiting material", "recheck_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
            )
            pause_id = r["execution"]["pause"]["id"]
            try:
                async with db.transaction(actors["reviewer"]) as tx:
                    await tx.execute("SELECT set_config('app.task_execution_worker','true',true)")
                    await tx.fetchval("SELECT workflow.task_execution_enqueue_pause_rechecks(100)")
            except asyncpg.InsufficientPrivilegeError:
                pass
            else:
                raise AssertionError("API reached worker-only reminder function")
            assert await run_worker() == 0
            # Synthetic clock fixture only: production function has no caller time input.
            await c.execute(
                "UPDATE workflow.task_execution SET state=jsonb_set(state,'{pause,recheck_at}',to_jsonb(clock_timestamp()-interval '1 second')) WHERE task_id=$1",
                task,
            )
            before_reminders = await read()
            other_worker = await asyncpg.connect(database=name, **config)
            try:
                counts = await asyncio.gather(run_worker(), run_worker(other_worker))
                assert sorted(counts) == [0, 2], counts
            finally:
                await other_worker.close()
            assert await run_worker() == 0
            assert await read() == before_reminders
            await command("set_item_progress", {**progress_body, "item_id": definitions[1]["item_id"]}, expected=409)
            await command("append_feedback", {"text": "paused update"})
            await command(
                "recheck_pause",
                {
                    "pause_id": pause_id,
                    "reason": "still waiting",
                    "recheck_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
                },
            )
            s = await read()
            assert s["pause"]["id"] == pause_id and s["pause"]["recheck_version"] == 2
            assert await run_worker() == 0
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM workflow.notification WHERE object_id=$1 AND template_code='task_pause_recheck' AND status='cancelled'",
                    task,
                )
                == 2
            )
            await c.execute(
                "UPDATE workflow.task_execution SET state=jsonb_set(state,'{pause,recheck_at}',to_jsonb(clock_timestamp()-interval '1 second')) WHERE task_id=$1",
                task,
            )
            await c.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", actors["reviewer"].user_id)
            assert await run_worker() == 1
            await c.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", actors["reviewer"].user_id)
            assert await run_worker() == 1
            assert await run_worker() == 0
            report["samples"]["pause_rechecks"] = [
                dict(row)
                for row in await c.fetch(
                    "SELECT recipient_user_ref_id::text,status,dedupe_key,payload FROM workflow.notification WHERE object_id=$1 AND template_code='task_pause_recheck' ORDER BY dedupe_key",
                    task,
                )
            ]
            await command("resume", {"pause_id": pause_id, "reason": "ready"})
            report["checks"].append(
                "pause/recheck/resume intervals persist; execution blocked while paused; feedback allowed; due date unchanged"
            )
            assert await run_worker() == 0
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM workflow.notification WHERE object_id=$1 AND template_code='task_pause_recheck' AND status='pending'",
                    task,
                )
                == 0
            )
            report["checks"].append(
                "worker-only normal role without user context: future pause excluded; concurrent due runs deduplicate; recheck round and active grants revalidated; resume cancels stale reminders; no task/progress/version mutation"
            )
            # A request is confirmed against latest execution facts, not proposal-time facts.
            s = await read()
            req = dict(s["requirements"])
            req["checklist"] = req["checklist"] + [
                {
                    "item_id": str(uuid4()),
                    "definition_version": 1,
                    "title": "three",
                    "acceptance_criteria": "third result",
                }
            ]
            r, _ = await command("propose_requirements", {"requirements": req, "reason": "new deliverable"})
            cid = r["execution"]["pending_requirements"]["id"]
            await command(
                "set_item_progress", {**progress_body, "item_id": definitions[1]["item_id"], "note": "second result"}
            )
            await command("submit_completion", {"note": "cannot finish with pending change"}, expected=409)
            await command("respond_requirements", {"change_id": cid, "decision": "accept"}, actor="reviewer")
            s = await read()
            assert s["progress"]["completed"] == 2 and s["progress"]["total"] == 3 and s["progress"]["percent"] == 66
            report["checks"].append(
                "two-party difference confirmation; in-flight progress retained; denominator changes only at activation; pending change blocks submission"
            )
            # Text-only typo still needs both confirmations and invalidates affected evidence.
            req = dict(s["requirements"])
            req["title"] += " corrected"
            r, _ = await command("propose_requirements", {"requirements": req, "reason": "typo"})
            await command(
                "respond_requirements",
                {"change_id": r["execution"]["pending_requirements"]["id"], "decision": "accept"},
                actor="reviewer",
            )
            s = await read()
            assert s["progress"]["basis"] == "requires_revalidation" and s["progress"]["percent"] is None
            for item in s["items"]:
                await command(
                    "set_item_progress",
                    {
                        "item_id": item["item_id"],
                        "definition_version": item["definition_version"],
                        "state": "completed",
                        "note": "revalidated current requirement",
                    },
                )
            s = await read()
            assert s["progress"]["percent"] == 100 and s["status"] == "in_progress"
            report["checks"].append(
                "critical text changes including typo require confirmation; affected completions need revalidation; 100 percent does not complete lifecycle"
            )
            r, _ = await command("submit_completion", {"note": "complete evidence"})
            sid = r["execution"]["current_submission"]
            frozen = r["execution"]["submissions"][-1]
            await command(
                "pause",
                {"reason": "not while review", "recheck_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
                expected=409,
            )
            await command("propose_requirements", {"requirements": req, "reason": "not while review"}, expected=409)
            await command("append_feedback", {"text": "supplementary comment"})
            r, _ = await command(
                "review_completion",
                {
                    "submission_id": sid,
                    "decision": "reject",
                    "note": "redo first",
                    "rework_item_ids": [definitions[0]["item_id"]],
                },
                actor="reviewer",
            )
            assert r["execution"]["submissions"][-1] == frozen and r["execution"]["items"][0]["state"] == "rework"
            report["checks"].append(
                "immutable submission survives comments/rejection; review binds submission round; rejected items explicitly rework"
            )
            r, _ = await command(
                "pause",
                {"reason": "handover paused", "recheck_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
            )
            pause_id = r["execution"]["pause"]["id"]
            r, _ = await command(
                "initiate_executor_handover",
                {"target_user_id": actors["new_executor"].user_id, "reason": "new owner"},
                actor="reviewer",
            )
            hid = r["execution"]["execution_handover"]["id"]
            assert (
                r["execution"]["executor_user_id"] == actors["new_executor"].user_id
                and r["execution"]["executor_transferred"] is True
            )
            await c.execute(
                "UPDATE workflow.task_execution SET state=jsonb_set(state,'{pause,recheck_at}',to_jsonb(clock_timestamp()-interval '1 second')) WHERE task_id=$1",
                task,
            )
            assert await run_worker() == 2
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM workflow.notification WHERE object_id=$1 AND template_code='task_pause_recheck' AND payload->>'pause_id'=$2 AND recipient_user_ref_id=$3",
                    task,
                    pause_id,
                    actors["executor"].user_id,
                )
                == 0
            )
            report["checks"].append(
                "reminder uses current responsibility and actual read scope; transferred former executor/pause author receives no task data"
            )
            await command("set_item_progress", progress_body, actor="executor", expected=403)
            r, _ = await command(
                "respond_executor_handover",
                {"handover_id": hid, "decision": "reject", "reason": "unavailable"},
                actor="new_executor",
            )
            assert (
                r["execution"]["status"] != "cancelled" and r["execution"]["execution_handover"]["status"] == "rejected"
            )
            await command("resume", {"pause_id": pause_id, "reason": "not accepted"}, actor="reviewer", expected=409)
            r, _ = await command(
                "initiate_executor_handover",
                {"target_user_id": actors["executor"].user_id, "reason": "redirect"},
                actor="reviewer",
            )
            await command(
                "respond_executor_handover",
                {"handover_id": r["execution"]["execution_handover"]["id"], "decision": "accept"},
                actor="executor",
            )
            s = await read()
            assert s["pause"]["id"] == pause_id
            await command("resume", {"pause_id": pause_id, "reason": "accepted and ready"})
            report["checks"].append(
                "executor handover changes owner immediately; old owner denied; reject keeps task; accepting does not resume pause"
            )
            r, _ = await command(
                "initiate_reviewer_handover",
                {"target_user_id": actors["new_reviewer"].user_id, "reason": "reviewer unavailable"},
                actor="reviewer",
            )
            assert r["execution"]["current_reviewer"] == actors["reviewer"].user_id
            await command(
                "respond_reviewer_handover",
                {"handover_id": r["execution"]["reviewer_handover"]["id"], "decision": "accept"},
                actor="new_reviewer",
            )
            s = await read()
            assert (
                s["current_reviewer"] == actors["new_reviewer"].user_id
                and s["creator_user_ref_id"] == actors["reviewer"].user_id
            )
            report["checks"].append(
                "reviewer responsibility switches only on candidate acceptance; immutable creator retained"
            )
            # Legacy valid pending review can reject original evidence; no fake checklist.
            legacy_state = await read("reviewer", str(legacy))
            assert legacy_state["progress"]["basis"] == "missing_checklist"
            await command(
                "review_completion",
                {"submission_id": str(legacy), "decision": "reject", "note": "need checklist", "rework_item_ids": []},
                actor="reviewer",
                tid=str(legacy),
            )
            await command("submit_completion", {"note": "still no checklist"}, tid=str(legacy), expected=409)
            report["checks"].append(
                "legacy valid pending review uses original evidence; rejected legacy must establish checklist before next submission"
            )
            # Read grants are SELF-scoped: the new review assignment is real authority,
            # whereas an unrelated same-company account cannot use the object endpoint.
            active = actors["outsider"]
            denied = await client.get(f"/api/v1/tasks/{task}/execution")
            assert denied.status_code == 403, denied.text
            report["checks"].append(
                "self-scoped executor/reviewer/candidate reads work; unrelated same-tenant account denied"
            )
            # The original self-assigned exemption cannot be resurrected by a round trip.
            async with db.transaction(actors["reviewer"]) as tx:
                own = await TaskService().create(
                    tx,
                    actor=actors["reviewer"],
                    description="Self task permanence",
                    due_at=datetime.now(UTC) + timedelta(days=20),
                    priority_code="normal",
                    assignee_account_code="reviewer",
                )
                ownid = own["id"]
                ownitem = {
                    "item_id": str(uuid4()),
                    "definition_version": 1,
                    "title": "deliver",
                    "acceptance_criteria": "evidence",
                }
                await initialize_execution(tx, ownid, [ownitem])
            await command("accept_task", {}, actor="reviewer", tid=ownid)
            r, _ = await command(
                "initiate_executor_handover",
                {"target_user_id": actors["new_executor"].user_id, "reason": "handover"},
                actor="reviewer",
                tid=ownid,
            )
            await command(
                "respond_executor_handover",
                {"handover_id": r["execution"]["execution_handover"]["id"], "decision": "accept"},
                actor="new_executor",
                tid=ownid,
            )
            r, _ = await command(
                "initiate_reviewer_handover",
                {"target_user_id": actors["new_reviewer"].user_id, "reason": "independent review"},
                actor="reviewer",
                tid=ownid,
            )
            await command(
                "respond_reviewer_handover",
                {"handover_id": r["execution"]["reviewer_handover"]["id"], "decision": "accept"},
                actor="new_reviewer",
                tid=ownid,
            )
            r, _ = await command(
                "initiate_executor_handover",
                {"target_user_id": actors["reviewer"].user_id, "reason": "return original"},
                actor="reviewer",
                tid=ownid,
            )
            await command(
                "respond_executor_handover",
                {"handover_id": r["execution"]["execution_handover"]["id"], "decision": "accept"},
                actor="reviewer",
                tid=ownid,
            )
            await command(
                "set_item_progress",
                {
                    "item_id": ownitem["item_id"],
                    "definition_version": 1,
                    "state": "completed",
                    "note": "current evidence",
                },
                actor="reviewer",
                tid=ownid,
            )
            r, _ = await command(
                "submit_completion", {"note": "requires independent review"}, actor="reviewer", tid=ownid
            )
            assert r["execution"]["status"] == "pending_review"
            ownsid = r["execution"]["current_submission"]
            await command(
                "review_completion",
                {"submission_id": ownsid, "decision": "approve"},
                actor="reviewer",
                tid=ownid,
                expected=403,
            )
            await command(
                "review_completion", {"submission_id": ownsid, "decision": "approve"}, actor="new_reviewer", tid=ownid
            )
            assert (await read("reviewer", ownid))["status"] == "completed"
            report["checks"].append(
                "first executor handover permanently removes self-task exemption; round trip still requires distinct current reviewer"
            )
            # Two independent DB connections compete with the same aggregate version.
            state = await read()
            racebase = {x: state[x] for x in ("version_no", "requirements_version", "responsibility_version")}
            raceitem = state["items"][0]

            async def compete(action, extra):
                other = await asyncpg.connect(database=name, **config)
                try:
                    for typ in ("json", "jsonb"):
                        await other.set_type_codec(
                            typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
                        )
                    async with other.transaction():
                        await other.execute(f'SET LOCAL ROLE "{role}"')
                        await other.execute("SET LOCAL row_security=on")
                        await set_request_context(other, actors["executor"])
                        return await other.fetchval(
                            "SELECT workflow.task_execution_command($1::uuid,$2,$3::jsonb,$4::uuid)",
                            task,
                            action,
                            {**racebase, **extra},
                            uuid4(),
                        )
                except asyncpg.PostgresError as exc:
                    return exc.sqlstate
                finally:
                    await other.close()

            race = await asyncio.gather(
                compete(
                    "pause",
                    {"reason": "race pause", "recheck_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
                ),
                compete(
                    "set_item_progress",
                    {
                        "item_id": raceitem["item_id"],
                        "definition_version": raceitem["definition_version"],
                        "state": "completed",
                        "note": "race progress",
                    },
                ),
            )
            assert sum(isinstance(x, dict) for x in race) == 1 and race.count("40001") == 1, race
            report["checks"].append(
                "two independent restricted connections serialize concurrent pause/progress: exactly one winner, one version conflict"
            )

            report["samples"]["final"] = await read()
            # Regression from independent QA: OLD owner must remain protected
            # when NEW responsibility is collaborator or moves to another task.
            await c.execute(
                "INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'operations','collaborator')",
                task,
                ws,
                actors["new_reviewer"].user_id,
                team,
            )
            owner_guard_before = await read()
            for sql, arguments in (
                (
                    "UPDATE workflow.task_assignee SET responsibility='collaborator' WHERE task_id=$1 AND responsibility='owner'",
                    [task],
                ),
                (
                    "UPDATE workflow.task_assignee SET responsibility='owner' WHERE task_id=$1 AND assignee_user_ref_id=$2",
                    [task, actors["new_reviewer"].user_id],
                ),
                (
                    "UPDATE workflow.task_assignee SET task_id=$2 WHERE task_id=$1 AND responsibility='owner'",
                    [task, legacy],
                ),
                (
                    "UPDATE workflow.task_assignee SET task_id=$2,responsibility='collaborator' WHERE task_id=$1 AND responsibility='owner'",
                    [task, legacy],
                ),
                (
                    "UPDATE workflow.task_assignee SET assignee_user_ref_id=$2 WHERE task_id=$1 AND responsibility='owner'",
                    [task, actors["reviewer"].user_id],
                ),
                ("DELETE FROM workflow.task_assignee WHERE task_id=$1 AND responsibility='owner'", [task]),
                (
                    "INSERT INTO workflow.task_assignee(task_id,workspace_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'operations','owner')",
                    [task, ws, actors["reviewer"].user_id, team],
                ),
            ):
                try:
                    async with db.transaction(actors["reviewer"]) as tx:
                        assert await tx.fetchval("SELECT security.authorization_task('task.coordinate',$1)", task)
                        await tx.execute(sql, *arguments)
                except asyncpg.SerializationError as exc:
                    assert str(exc) == "TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED"
                else:
                    raise AssertionError("owner relationship bypass: " + sql)
            assert (
                await c.fetchval(
                    "SELECT count(*) FROM workflow.task_assignee WHERE task_id=$1 AND responsibility='owner'", task
                )
                == 1
            )
            assert await read() == owner_guard_before
            report["checks"].append(
                "independent QA owner-demotion regression: INSERT/UPDATE/DELETE, downgrade/upgrade, owner identity and task-id move all reject under legitimate coordinator; owner/projection/versions unchanged"
            )
            # Runtime cannot bypass through table DML or arbitrary app switches.
            for sql, args_ in [
                ("UPDATE workflow.task SET title='bypass' WHERE id=$1::uuid", [task]),
                ("UPDATE workflow.task SET status='completed' WHERE id=$1::uuid", [task]),
                ("DELETE FROM workflow.task_assignee WHERE task_id=$1::uuid AND responsibility='owner'", [task]),
                ("UPDATE workflow.task_execution SET state='{}' WHERE task_id=$1::uuid", [task]),
                ("UPDATE workflow.task_execution_submission SET snapshot='{}' WHERE task_id=$1::uuid", [task]),
            ]:
                try:
                    async with db.transaction(actors["reviewer"]) as tx:
                        await tx.execute(
                            "SELECT set_config('app.task_execution_mode','true',true),set_config('app.task_execution_authorized','true',true)"
                        )
                        await tx.execute(sql, *args_)
                except asyncpg.PostgresError as exc:
                    assert exc.sqlstate in ("40001", "42501"), (sql, exc)
                else:
                    raise AssertionError("runtime DML bypass " + sql)
            report["checks"].append(
                "NOSUPERUSER/NOBYPASSRLS direct task/owner/snapshot/projection writes denied despite self-set GUC switches"
            )
            # Explicit permission deny is checked before an otherwise identical replay.
            await c.execute(
                "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)",
                ws,
                actors["executor"].user_id,
            )
            await c.execute(
                "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'task.complete','deny',NULL)",
                ws,
                actors["executor"].user_id,
            )
            await command("set_item_progress", {}, body=body, key=key, expected=403)
            report["checks"].append("revoked current action permission rejects identical idempotency replay")
        report["status"] = "passed"
    except asyncpg.PostgresError as exc:
        print("POSTGRES_CONTEXT:", exc.context)
        raise
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{worker_role}"')
        await admin.close()
        report["disposable_database_and_role_removed"] = True
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"}, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--socket", required=True)
    p.add_argument("--port", type=int, default=55437)
    p.add_argument("--output", required=True)
    p.add_argument("--focus", choices=("full", "contribution"), default="full")
    asyncio.run(verify(p.parse_args()))
