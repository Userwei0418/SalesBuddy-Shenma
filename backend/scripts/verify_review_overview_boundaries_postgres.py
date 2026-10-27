# ruff: noqa: E501, S108 -- Explicit private socket, synthetic SQL, opt-in verification only.
"""Narrow Overview permission regression proof; does not rerun the original suite."""

import argparse
import asyncio
import hashlib
import io
import json
from contextlib import asynccontextmanager, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from verify_review_targets_postgres import ROOT, grants, migrate_through

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.review_overview import router
from sales_backend.api.work_reports import router as old_report_router
from sales_backend.contracts.review_overview import ReviewSelection
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError
from sales_backend.repositories.work_report_meeting_facts import collect
from sales_backend.services.review_overview import read_metric_projection


async def verify(args):
    socket = Path(args.socket).resolve()
    assert str(socket).startswith(("/private/tmp/sb-review-pg.", "/tmp/sb-review-pg."))
    config = dict(host=str(socket), port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    name, role = "sb_review_overview_boundary_" + uuid4().hex[:12], "sb_review_boundary_reader_" + uuid4().hex[:12]
    connection, result = None, None
    checks, samples, http_calls = [], {}, []
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        connection = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(connection, 240)
        await connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grants(connection, role)
        for typ in ("json", "jsonb"):
            await connection.set_type_codec(
                typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
            )
        await connection.execute("SET row_security=on")
        ws, team, manager, member = [uuid4() for _ in range(4)]
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)", ws, str(ws)
        )
        await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        await connection.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'BOUNDARY','Synthetic boundary')",
            team,
            ws,
        )
        for uid, code in ((manager, "supervisor"), (member, "sales")):
            await connection.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)",
                uid,
                ws,
                code + uuid4().hex[:8],
            )
            await connection.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)",
                ws,
                uid,
                code,
                "team" if code == "supervisor" else "self",
                team,
            )
            await connection.execute(
                "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)",
                ws,
                uid,
                team,
                code,
            )
        actor = ActorContext(
            workspace_id=str(ws), user_id=str(manager), role="supervisor", data_scope="team", team_ids=[str(team)]
        )
        permission_role = await connection.fetchval(
            "SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='supervisor'", ws
        )

        async def grant_scope(permission, scope):
            await connection.execute(
                "INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids) VALUES($1,$2,$3,$4,$5::uuid[]) ON CONFLICT(role_id,permission_code) DO UPDATE SET scope_code=excluded.scope_code,team_ids=excluded.team_ids",
                ws,
                permission_role,
                permission,
                scope,
                [team] if scope == "teams" else [],
            )

        for permission in (
            "work_report.read",
            "opportunity.read",
            "customer.read",
            "visit.read",
            "task.read",
            "target.read",
        ):
            await grant_scope(permission, "teams")
        anchor = await connection.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        start = date(anchor.year, (anchor.month - 1) // 3 * 3 + 1, 1)
        quarter = (anchor.month - 1) // 3 + 1
        ids = {}
        async with connection.transaction():
            await set_request_context(connection, actor)
            form = await connection.fetchval(
                "SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1"
            )
            for label, uid, value in (("visible", manager, "111.11"), ("hidden", member, "98765.43")):
                customer, opportunity, visit, task = [uuid4() for _ in range(4)]
                ids[label] = dict(customer=customer, opportunity=opportunity, visit=visit, task=task)
                await connection.execute(
                    "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,$3,$3,$4,$4,$5)",
                    customer,
                    ws,
                    label + "_boundary_customer",
                    uid,
                    team,
                )
                await connection.execute("SELECT set_config('app.native_opportunity_create',$1,true)", str(opportunity))
                await connection.execute(
                    "INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount,currency,probability,stage_code,status,created_by_user_ref_id) VALUES($1,$2,$3,$4,$5,$6,1000,'CNY',70,'proposal','open',$5)",
                    opportunity,
                    ws,
                    customer,
                    label + "_boundary_opportunity",
                    uid,
                    team,
                )
                await connection.execute(
                    "INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,collection_amount,recognized_amount,collection_confidence) VALUES($1,$2,$3,$4,$5::text::numeric,$5::text::numeric,'high')",
                    ws,
                    opportunity,
                    anchor.year,
                    quarter,
                    value,
                )
                await connection.execute(
                    "INSERT INTO activity.visit(id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,follow_up_record,next_action) VALUES($1,$2,$3,$4,$5,$6,$5,$7,'archived',clock_timestamp(),clock_timestamp(),$8,'合成下一步')",
                    visit,
                    ws,
                    customer,
                    opportunity,
                    uid,
                    team,
                    form,
                    label + "_boundary_followup",
                )
                await connection.execute(
                    "INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,creator_team_id,due_at,status,customer_id,opportunity_id) VALUES($1,$2,$3,$3,$4,$5,clock_timestamp()+interval '1 day','pending_execution',$6,$7)",
                    task,
                    ws,
                    label + "_boundary_task",
                    uid,
                    team,
                    customer,
                    opportunity,
                )
                await connection.execute(
                    "INSERT INTO workflow.task_assignee(workspace_id,task_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'sales','owner')",
                    ws,
                    task,
                    uid,
                    team,
                )
                completed = uuid4()
                ids[label]["completed_task"] = completed
                await connection.execute(
                    "INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,creator_team_id,due_at,status,completed_at,completion_note,customer_id,opportunity_id) VALUES($1,$2,$3,$3,$4,$5,clock_timestamp()+interval '1 day','completed',clock_timestamp(),'Synthetic completion',$6,$7)",
                    completed,
                    ws,
                    label + "_completed_task",
                    uid,
                    team,
                    customer,
                    opportunity,
                )
                await connection.execute(
                    "INSERT INTO workflow.task_assignee(workspace_id,task_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'sales','owner')",
                    ws,
                    completed,
                    uid,
                    team,
                )
            for kind in ("collection", "collection_ppl", "forecast_recognized"):
                await connection.execute(
                    "INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,team_id,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end) VALUES($1,$2,'team',$3,$4,200000,$5,'quarter',$6,($6::date+interval '3 months - 1 day')::date)",
                    ws,
                    start.year,
                    team,
                    kind,
                    manager,
                    start,
                )

        class DB:
            settings = SimpleNamespace()

            @asynccontextmanager
            async def transaction(self, current_actor, readonly=False, isolation="read_committed"):
                async with connection.transaction(readonly=readonly, isolation=isolation):
                    await connection.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(connection, current_actor)
                    assert not await connection.fetchval(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                    yield connection

        database = DB()
        app = FastAPI()
        app.include_router(router)
        app.include_router(old_report_router)
        app.dependency_overrides[get_database] = lambda: database
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)

        @app.exception_handler(WorkReportError)
        async def error(request, exc):
            return JSONResponse({"code": exc.code, "detail": exc.detail}, status_code=exc.status)

        @app.exception_handler(PermissionError)
        async def forbidden(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=403)

        selected = dict(scope="team", team_id=str(team), anchor_date=str(anchor), period_type="quarter")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:

            async def get(endpoint="overview", **params):
                response = await client.get("/api/v1/review/" + endpoint, params={**selected, **params})
                http_calls.append({"endpoint": endpoint, "params": params, "status": response.status_code})
                assert response.status_code == 200, (endpoint, response.status_code, response.text)
                return response.json()

            baseline = await get()
            assert all(card["value"] == "98876.54" and card["coverage"] == "complete" for card in baseline["metrics"])
            baseline_actions = {item["code"]: item for item in baseline["activities"]}
            assert baseline_actions["formal_visits"]["count"] == 2 and baseline_actions["pending_tasks"]["count"] == 2

            assert baseline_actions["new_opportunities"]["count"] == 2
            assert baseline_actions["completed_tasks"]["count"] == 2
            baseline_not_updated = await get("opportunities", filter="not_updated")
            assert not baseline["filter_availability"]["not_updated"]["available"]
            assert baseline["filter_availability"]["not_updated"]["coverage"] == "unavailable"
            assert baseline["filter_availability"]["not_updated"]["known_count"] is None
            assert baseline["pool_counts"]["not_updated"] is None
            assert not baseline_not_updated["available"] and baseline_not_updated["coverage"] == "unavailable"
            assert baseline_not_updated["total"] is None and not baseline_not_updated["items"]
            samples["not_updated_unavailable"] = {
                "filter": baseline["filter_availability"]["not_updated"],
                "detail": baseline_not_updated,
            }
            checks.append(
                "unverifiable not_updated filter is unavailable with null known_count in overview and unavailable/null/empty in detail"
            )

            # Report permission remains team-wide, but source objects shrink.
            for permission in ("opportunity.read", "customer.read"):
                await grant_scope(permission, "self")
                overview = await get()
                details = await get("opportunities", metric_code="collection_ppl")
                for card in overview["metrics"]:
                    assert card["value"] is None and card["rate"] is None and card["gap"] is None
                    assert card["known_subtotal"] == "111.11" and card["coverage"] == "partial"
                    assert "source_scope_not_fully_authorized" in card["missing_reasons"]
                    assert {ref["opportunity_id"] for ref in card["source_refs"]} == {
                        str(ids["visible"]["opportunity"])
                    }
                assert details["total"] is None and details["known_total"] == 1 and details["coverage"] == "partial"
                assert [row["id"] for row in details["items"]] == [str(ids["visible"]["opportunity"])]
                assert overview["pool_counts"]["all"] is None
                assert overview["filter_availability"]["all"]["known_count"] == 1
                for payload in (overview, details):
                    encoded = json.dumps(payload, ensure_ascii=False)
                    for forbidden_value in (
                        str(ids["hidden"]["opportunity"]),
                        "hidden_boundary_opportunity",
                        "98765.43",
                        "98876.54",
                    ):
                        assert forbidden_value not in encoded
                async with database.transaction(actor, readonly=True, isolation="repeatable_read") as scoped:
                    projection = await read_metric_projection(scoped, actor, ReviewSelection(**selected))
                # Normalize datetime source references through the same JSON view.
                projection_cards = json.loads(json.dumps(projection["metrics"], default=str))
                for actual, expected in zip(projection_cards, overview["metrics"], strict=True):
                    for field in (
                        "metric_code",
                        "value",
                        "known_subtotal",
                        "coverage",
                        "rate",
                        "gap",
                        "missing_reasons",
                    ):
                        assert actual[field] == expected[field]
                activities = {item["code"]: item for item in overview["activities"]}
                for code in ("new_opportunities", "formal_visits"):
                    card = activities[code]
                    detail = await get("activities", activity=code)
                    assert card["count"] is None and card["known_count"] == 1 and card["coverage"] == "partial", (
                        permission,
                        code,
                        card,
                    )
                    assert "来源读取权限不覆盖所选统计范围" in card["missing_reasons"]
                    assert detail["total"] is None and detail["known_total"] == 1 and detail["coverage"] == "partial"
                    assert len(detail["items"]) == 1
                for code in ("lost_opportunities", "updated_opportunities"):
                    assert activities[code]["count"] is None
                    assert "来源读取权限不覆盖所选统计范围" in activities[code]["missing_reasons"]
                for code in ("completed_tasks", "pending_tasks"):
                    assert activities[code]["count"] == 2 and activities[code]["coverage"] == "complete"
                    detail = await get("activities", activity=code)
                    assert detail["total"] == 2 and detail["coverage"] == "complete"
                    field = "customer_id" if permission == "customer.read" else "opportunity_id"
                    task_key = "completed_task" if code == "completed_tasks" else "task"
                    hidden = next(row for row in detail["items"] if row["task_id"] == str(ids["hidden"][task_key]))
                    assert hidden[field] is None
                samples[permission + "_self"] = {"overview": overview, "details": details}
                await grant_scope(permission, "teams")
            checks.append(
                "team report scope plus self-only opportunity/customer reads retains visible 111.11 subtotal with null aggregates/rates/gaps, null pool/detail totals and no hidden opportunity/value; shared KR projection agrees"
            )

            checks.append(
                "customer/opportunity self scope makes all dependent action counts incomplete; new/formal overview and detail agree at one known row while independently authorized completed/pending task counts remain complete at two with restricted metadata redacted"
            )

            for target_scope in ("self", None):
                if target_scope:
                    await grant_scope("target.read", target_scope)
                else:
                    await connection.execute(
                        "DELETE FROM config.permission_role_grant WHERE role_id=$1 AND permission_code='target.read'",
                        permission_role,
                    )
                without_target = await get()
                async with database.transaction(actor, readonly=True, isolation="repeatable_read") as scoped:
                    projection = await read_metric_projection(scoped, actor, ReviewSelection(**selected))
                for card, shared in zip(without_target["metrics"], projection["metrics"], strict=True):
                    assert card["value"] == "98876.54" and card["coverage"] == "complete"
                    assert card["target"]["status"] == "permission_denied" and card["target"]["value"] is None
                    assert card["rate"] is None and card["gap"] is None
                    for field in ("metric_code", "value", "known_subtotal", "coverage", "target", "rate", "gap"):
                        assert card[field] == shared[field]
                assert "200000" not in json.dumps(without_target)
                samples["target_read_" + (target_scope or "revoked")] = without_target
            await grant_scope("target.read", "teams")
            checks.append(
                "team facts remain complete when target.read is self-only or revoked; effective team target/value/rate/gap are not disclosed, and shared KR metric projection uses identical permission-denied target semantics"
            )

            # A narrower visit grant cannot prove absence of formal follow-up.
            await grant_scope("visit.read", "self")
            narrowed = await get()
            stale = await get("opportunities", filter="stale")
            visits = await get("activities", activity="formal_visits")
            action = next(item for item in narrowed["activities"] if item["code"] == "formal_visits")
            assert action["count"] is None and action["known_count"] == 1 and action["coverage"] == "partial"
            assert (
                not narrowed["filter_availability"]["stale"]["available"] and narrowed["pool_counts"]["stale"] is None
            )
            assert narrowed["filter_availability"]["stale"]["coverage"] == "unavailable"
            assert narrowed["filter_availability"]["stale"]["known_count"] is None
            assert not stale["available"] and stale["total"] is None and stale["coverage"] == "unavailable"
            assert visits["total"] is None and visits["known_total"] == 1 and visits["coverage"] == "partial"
            assert [row["id"] for row in visits["items"]] == [str(ids["visible"]["visit"])]
            assert str(ids["hidden"]["visit"]) not in json.dumps(visits)
            samples["visit_self"] = {"overview": narrowed, "stale": stale, "formal_visits": visits}
            checks.append(
                "team report scope plus self-only visit read makes overview/detail stale unavailable and formal visit aggregate null while retaining the one authorized visit"
            )

            await connection.execute(
                "DELETE FROM config.permission_role_grant WHERE role_id=$1 AND permission_code='visit.read'",
                permission_role,
            )
            removed = await get()
            removed_stale = await get("opportunities", filter="stale")
            removed_visits = await get("activities", activity="formal_visits")
            old_report = await client.get(
                "/api/v1/work-reports/overview",
                params={
                    "scope_kind": "team",
                    "team_id": str(team),
                    "period_kind": "quarter",
                    "anchor_date": str(anchor),
                    "request_id": str(uuid4()),
                },
            )
            assert old_report.status_code == 403, old_report.text
            assert old_report.json()["code"] == "WORK_REPORT_PERMISSION_CHANGED"
            http_calls.append({"endpoint": "work-reports/overview", "status": old_report.status_code})
            async with database.transaction(actor, isolation="repeatable_read") as scoped:
                try:
                    await collect(scoped, actor, scope=None, period=None, allow_partial_sources=True)
                except WorkReportError as exc:
                    assert exc.status == 403
                else:
                    raise AssertionError("partial source collection must not permit write-capable report generation")
            action = next(item for item in removed["activities"] if item["code"] == "formal_visits")
            assert action["count"] is None and action["known_count"] == 0 and action["coverage"] == "unavailable"
            assert not removed["filter_availability"]["stale"]["available"] and removed["pool_counts"]["stale"] is None
            assert removed["filter_availability"]["stale"]["coverage"] == "unavailable"
            assert removed["filter_availability"]["stale"]["known_count"] is None
            assert not removed_stale["available"] and removed_stale["total"] is None
            assert (
                removed_visits["total"] is None and removed_visits["known_total"] == 0 and not removed_visits["items"]
            )
            samples["visit_revoked"] = {
                "formal_card": action,
                "stale": removed_stale,
                "formal_visits": removed_visits,
                "legacy_report_denied": old_report.json(),
            }
            checks.append(
                "fully revoked visit read never becomes formal visit count=0 or a stale result; new Review stays 200 with unavailable/null, while legacy report HTTP retains its 403 source requirement and partial collection cannot enable report generation"
            )

            await grant_scope("task.read", "self")
            task_overview = await get()
            task_details = await get("activities", activity="pending_tasks")
            action = next(item for item in task_overview["activities"] if item["code"] == "pending_tasks")
            assert action["count"] is None and action["known_count"] == 1 and action["coverage"] == "partial"
            assert task_details["total"] is None and task_details["known_total"] == 1
            assert [row["id"] for row in task_details["items"]] == [str(ids["visible"]["task"])]
            assert str(ids["hidden"]["task"]) not in json.dumps(task_details)
            completed_card = next(item for item in task_overview["activities"] if item["code"] == "completed_tasks")
            completed_details = await get("activities", activity="completed_tasks")
            assert (
                completed_card["count"] is None
                and completed_card["known_count"] == 1
                and completed_card["coverage"] == "partial"
            )
            assert completed_details["total"] is None and completed_details["known_total"] == 1
            assert [row["id"] for row in completed_details["items"]] == [str(ids["visible"]["completed_task"])]
            samples["task_self"] = {"pending_card": action, "pending_details": task_details}
            checks.append(
                "self-only task read retains one current pending task while the selected team count/total stay null; hidden task identity is absent"
            )
            for permission, activity in (
                ("customer.read", "new_claimed_customers"),
                ("opportunity.read", "new_opportunities"),
                ("task.read", "pending_tasks"),
            ):
                for restored in ("customer.read", "opportunity.read", "visit.read", "task.read"):
                    await grant_scope(restored, "teams")
                await connection.execute(
                    "DELETE FROM config.permission_role_grant WHERE role_id=$1 AND permission_code=$2",
                    permission_role,
                    permission,
                )
                unavailable = await get()
                unavailable_detail = await get("activities", activity=activity)
                unavailable_card = next(item for item in unavailable["activities"] if item["code"] == activity)
                assert unavailable_card["count"] is None and unavailable_card["known_count"] == 0
                assert unavailable_card["coverage"] == "unavailable"
                assert unavailable_detail["total"] is None and unavailable_detail["known_total"] == 0
                assert not unavailable_detail["items"]
                if permission != "task.read":
                    assert all(
                        card["value"] is None and card["known_subtotal"] is None and card["coverage"] == "unavailable"
                        for card in unavailable["metrics"]
                    )
                    assert (
                        next(item for item in unavailable["activities"] if item["code"] == "pending_tasks")["count"]
                        == 2
                    )
                else:
                    assert all(card["value"] == "98876.54" for card in unavailable["metrics"])
                    assert (
                        next(item for item in unavailable["activities"] if item["code"] == "formal_visits")["count"]
                        == 2
                    )
                samples[permission + "_revoked"] = {"card": unavailable_card, "detail": unavailable_detail}
            checks.append(
                "fully revoked customer/opportunity/task sources return unavailable/null and zero authorized rows without leaking counts; independently authorized task or opportunity/visit facts remain available"
            )
        result = {
            "status": "passed_isolated_postgres_and_asgi",
            "check_count": len(checks),
            "checks": checks,
            "http_calls": http_calls,
            "response_samples": samples,
            "samples_are_synthetic": True,
            "verification_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_sha256": {
                file: hashlib.sha256((ROOT / file).read_bytes()).hexdigest()
                for file in (
                    "backend/src/sales_backend/services/review_overview.py",
                    "backend/src/sales_backend/repositories/review_overview.py",
                    "backend/src/sales_backend/contracts/review_overview.py",
                    "backend/src/sales_backend/api/review_overview.py",
                    "backend/src/sales_backend/repositories/work_report_meeting_facts.py",
                    "backend/src/sales_backend/services/work_report_overview.py",
                )
            },
            "scope": "synthetic V240 database only; real restricted API role, RLS and repeatable-read HTTP; trusted identity supplied by fixture",
        }
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        gone = not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", name)
        role_gone = not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        await admin.close()
        assert gone and role_gone
    result["cleanup"] = {"own_database_dropped": gone, "own_role_dropped": role_gone, "cluster_stopped": False}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "check_count": len(checks),
                "http_call_count": len(http_calls),
                "output": args.output,
                "cleanup": result["cleanup"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(verify(parser.parse_args()))
