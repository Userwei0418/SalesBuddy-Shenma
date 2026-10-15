# ruff: noqa: E501, S108 -- Explicit disposable Unix socket and synthetic SQL evidence only.
"""D02 real restricted-role API proof; no report generation or production access."""

import argparse
import asyncio
import hashlib
import io
import json
import os
import traceback
from contextlib import asynccontextmanager, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from verify_review_okr_postgres import grant_api, migrate_through

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.review_overview import router
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError

ROOT = Path(__file__).resolve().parents[2]


async def verify(args):
    socket = Path(args.socket).resolve()
    assert str(socket).startswith(("/private/tmp/sb-review-pg.", "/tmp/sb-review-pg."))
    assert Path(os.environ["PYTHONPATH"]).resolve() == ROOT / "backend/src"
    config = dict(host=str(socket), port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid4().hex[:12]
    db, role = "sb_review_multi_" + suffix, "sb_review_multi_api_" + suffix
    result = dict(status="running", checks=[], calls=[], samples={}, database=db, role=role)
    c = None
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        c = await asyncpg.connect(database=db, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 243)
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grant_api(c, role, stage="V243")
        await c.execute("SET row_security=on")
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        ws, user1, user2, team1, team2, customer1, customer2, opp1, opp2, visit, pending, completed, partner = [uuid4() for _ in range(13)]
        await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic multi')", ws, str(ws))
        await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        for tid in (team1, team2):
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)", tid, ws, str(tid))
        for uid in (user1, user2):
            await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)", uid, ws, str(uid))
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)", ws, uid, "operations" if uid == user1 else "sales", "workspace" if uid == user1 else "self")
        for uid, tid in ((user1, team1), (user1, team2), (user2, team2)):
            await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, uid, tid)
        permission_role = await c.fetchval("SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='operations'", ws)
        async def permission(code, scope="workspace", teams=()):
            await c.execute("INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids) VALUES($1,$2,$3,$4,$5::uuid[]) ON CONFLICT(role_id,permission_code) DO UPDATE SET scope_code=excluded.scope_code,team_ids=excluded.team_ids", ws, permission_role, code, scope, list(teams))
        for code in ("work_report.read", "customer.read", "opportunity.read", "visit.read", "task.read", "target.read", "risk.read"):
            await permission(code)
        actor = ActorContext(workspace_id=str(ws), user_id=str(user1), role="operations", data_scope="workspace", team_ids=[str(team1), str(team2)])
        anchor = await c.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        quarter = (anchor.month - 1) // 3 + 1
        start = date(anchor.year, 3 * quarter - 2, 1)
        targets = {}
        async with c.transaction():
            await set_request_context(c, actor)
            await c.execute("INSERT INTO crm.partner(id,workspace_id,name) VALUES($1,$2,'Synthetic partner')", partner, ws)
            for cid, oid, uid, tid, amount in ((customer1, opp1, user1, team1, 100), (customer2, opp2, user2, team2, 0)):
                await c.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,$3,$3,$4,$4,$5)", cid, ws, str(cid), uid, tid)
                await c.execute("SELECT set_config('app.native_opportunity_create',$1,true)", str(oid))
                await c.execute("INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,created_by_user_ref_id,amount,currency,probability,stage_code,status,sales_channel) VALUES($1,$2,$3,$4,$5,$6,$5,1000,'CNY',70,'proposal','open','direct')", oid, ws, cid, 'Multi ' + str(oid), uid, tid)
                await c.execute("INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,collection_amount,recognized_amount,collection_confidence) VALUES($1,$2,$3,$4,$5,$5,'high')", ws, oid, anchor.year, quarter, amount)
            for before, after in (("direct", "partner"), ("partner", "direct")):
                await c.execute("UPDATE crm.opportunity SET sales_channel=$2,partner_id=$3,partner_name=$4 WHERE id=$1", opp1, after, partner if after == "partner" else None, "Synthetic partner" if after == "partner" else None)
                version = await c.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", opp1)
                await c.execute("INSERT INTO crm.business_change(workspace_id,customer_id,opportunity_id,actor_user_ref_id,version_no,changes) VALUES($1,$2,$3,$4,$5,$6::jsonb)", ws, customer1, opp1, user1, version, [{"label": "销售渠道", "before": before, "after": after}])
            form = await c.fetchval("SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1")
            await c.execute("INSERT INTO activity.visit(id,workspace_id,customer_id,opportunity_id,recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,follow_up_record,next_action) VALUES($1,$2,$3,$4,$5,$6,$5,$7,'archived',clock_timestamp(),clock_timestamp(),'Shared formal visit','Next')", visit, ws, customer1, opp1, user1, team1, form)
            await c.execute("INSERT INTO activity.visit_participant(workspace_id,visit_id,user_ref_id,team_id_at_event) VALUES($1,$2,$3,$4)", ws, visit, user2, team2)
            for task, status in ((pending, "pending_execution"), (completed, "completed")):
                await c.execute("INSERT INTO workflow.task(id,workspace_id,title,description,creator_user_ref_id,creator_team_id,due_at,status,completed_at,completion_note,customer_id,opportunity_id) VALUES($1,$2,$3,$3,$4,$5,clock_timestamp()+interval '1 day',$6,CASE WHEN $6='completed' THEN clock_timestamp() END,'Synthetic complete',$7,$8)", task, ws, str(task), user1, team1, status, customer1, opp1)
                for uid, tid in ((user1, team1), (user2, team2)):
                    await c.execute("INSERT INTO workflow.task_assignee(workspace_id,task_id,assignee_user_ref_id,assignee_team_id,assignee_role,responsibility) VALUES($1,$2,$3,$4,'sales','owner')", ws, task, uid, tid)
            for typ, ident, amount in (("team", team1, 100), ("team", team2, 300), ("person", user1, 50), ("person", user2, 150)):
                for kind in ("collection", "collection_ppl", "forecast_recognized"):
                    targets[typ, ident, kind] = await c.fetchval("INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,team_id,user_ref_id,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end) VALUES($1,$2,$3,$4,$5,$6,$7,$8,'quarter',$9,($9::date+interval '3 months - 1 day')::date) RETURNING id", ws, start.year, typ, ident if typ == "team" else None, ident if typ == "person" else None, kind, amount, user1, start)
        await c.execute(f'SET ROLE "{role}"')

        class Database:
            @asynccontextmanager
            async def transaction(self, identity, **options):
                async with c.transaction(readonly=options.get("readonly", False), isolation=options.get("isolation", "read_committed")):
                    await set_request_context(c, identity)
                    yield c

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database] = lambda: Database()
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)
        @app.exception_handler(WorkReportError)
        async def report_error(request, exc):
            return JSONResponse(status_code=exc.status, content=dict(code=exc.code, detail=exc.detail))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            async def get(endpoint, pairs, *, expected=200, sample=None):
                response = await client.get('/api/v1/review/' + endpoint, params=[("anchor_date", anchor.isoformat()), *pairs])
                result["calls"].append(dict(endpoint=endpoint, params=pairs, status=response.status_code))
                if sample:
                    result["samples"][sample] = response.json()
                assert response.status_code == expected, (endpoint, response.status_code, response.text)
                return response.json()
            teamq = [("scope", "team"), ("team_ids", str(team2)), ("team_ids", str(team1)), ("team_ids", str(team1))]
            peopleq = [("scope", "member"), ("member_ids", str(user2)), ("member_ids", str(user1)), ("member_ids", str(user1))]
            first = await get("overview", teamq, sample="teams_overview")
            assert first["contract_version"] == "review.overview.v2" and len(first["metrics"]) == 3
            assert len(first["scope"]["resolved_member_ids"]) == 2
            for card in first["metrics"]:
                assert card["value"] == "100.00" and card["target"]["value"] == "400.00" and card["rate"] == "0.25000000", card
            assert first["generation"]["available"] is False
            reverse = await get("overview", [("scope", "team"), ("team_ids", str(team1)), ("team_ids", str(team2))])
            assert reverse["scope"]["scope_key"] == first["scope"]["scope_key"]
            assert reverse["scope"]["scope_revision"] == first["scope"]["scope_revision"]
            persons = await get("overview", peopleq, sample="members_overview")
            assert persons["metrics"][0]["target"]["value"] == "200.00" and persons["metrics"][0]["rate"] == "0.50000000"
            result["checks"].append("two teams/two people normalize repeats and reverse; exact independent targets 400 vs 200 and no average ratio")
            for kind in ("formal_visits", "pending_tasks", "completed_tasks"):
                detail = await get("activities", [*teamq, ("activity", kind)], sample=kind)
                assert detail["known_total"] == 1 and len(detail["items"]) == 1, detail
                assert len(detail["items"][0]["member_ids"]) == 2, detail
                assert detail["scope"]["scope_revision"] == first["scope"]["scope_revision"]
                person_detail = await get("activities", [*peopleq, ("activity", kind)])
                assert person_detail["known_total"] == 1
            changes = await get("activities", [*teamq, ("activity", "updated_opportunities")], sample="changes")
            assert changes["total"] is None and changes["history_coverage"]["status"] == "partial"
            changed = next(item for item in changes["items"] if item["id"] == str(opp1))
            events = changed["events"]
            channel = [change for e in events for change in e["changes"] if change["field"] == "sales_channel"]
            assert [(value["before"], value["after"]) for value in channel] == [("direct", "partner"), ("partner", "direct")]
            assert len({e["event_id"] for e in events}) == len(events)
            result["checks"].append("shared visit/completed/pending task distinct IDs and union owners; stable full channel round-trip events retain partial history")
            page0 = await get("opportunities", [*teamq, ("limit", "1"), ("offset", "0")], sample="pool_page0")
            page1 = await get("opportunities", [*teamq, ("limit", "1"), ("offset", "1")], sample="pool_page1")
            assert page0["total"] == page1["total"] == 2 and page0["has_more"] and not page1["has_more"]
            assert page0["items"][0]["id"] != page1["items"][0]["id"]
            search = await get("opportunities", [*teamq, ("q", str(opp2))])
            assert search["total"] == 1 and search["items"][0]["id"] == str(opp2)
            unavailable = await get("opportunities", [*teamq, ("filter", "not_updated")], sample="not_updated")
            assert not unavailable["available"] and unavailable["coverage"] == "unavailable" and unavailable["total"] is None
            old = await get("overview", [("scope", "team"), ("team_id", str(team1))], sample="legacy_single")
            assert old["contract_version"] == "review.overview.v1" and "scope_key" not in old["scope"]
            await get("overview", [*teamq, ("member_ids", str(user1))], expected=422)
            await get("overview", [("scope", "team"), ("team_id", str(team1)), ("expected_scope_revision", first["scope"]["scope_revision"])], expected=422, sample="legacy_revision_rejected")
            await get("overview", [("scope", "team"), *[("team_ids", str(team1))] * 101], expected=422)
            await get("overview", [("scope", "team"), ("team_ids", "")], expected=422)
            explicit_me = await get("overview", [("scope", "member"), ("member_ids", str(user1))], sample="explicit_member_v2")
            myself = await get("overview", [("scope", "self"), ("expected_scope_revision", explicit_me["scope"]["scope_revision"])], sample="self_v2")
            assert myself["contract_version"] == "review.overview.v2" and myself["scope"]["kind"] == "self"
            assert myself["scope"]["scope_key"] == explicit_me["scope"]["scope_key"]
            assert myself["scope"]["scope_revision"] == explicit_me["scope"]["scope_revision"]
            result["checks"].append("dedup precedes filtering/search/count/page; not_updated remains unavailable; legacy single selector remains v1 and mixed input is 422")
            result["checks"].append("self+expected revision selects v2 with same explicit-person key/revision; legacy+revision, empty array value and >100 raw IDs are422")
            await c.execute("RESET ROLE")
            await c.execute("DELETE FROM crm.sales_target WHERE id=$1", targets["team", team2, "collection_ppl"])
            await c.execute(f'SET ROLE "{role}"')
            missing = await get("overview", teamq, sample="missing_target")
            ppl = next(card for card in missing["metrics"] if card["metric_code"] == "collection_ppl")
            assert ppl["target"]["value"] is None and ppl["target"]["known_subtotal"] == "100.00" and ppl["rate"] is None
            await c.execute("RESET ROLE")
            await permission("target.read", "teams", [team1])
            await c.execute(f'SET ROLE "{role}"')
            denied = await get("overview", teamq, sample="target_denied")
            for card in denied["metrics"]:
                assert card["target"]["value"] is None and card["rate"] is None
                assert card["target"]["known_subtotal"] == "100.00"
                denied_component = next(part for part in card["target"]["components"] if part["subject_id"] == str(team2))
                assert denied_component["status"] == "permission_denied"
                assert denied_component["target_id"] is None and denied_component["value"] is None
            await c.execute("RESET ROLE")
            await permission("customer.read", "self")
            await permission("visit.read", "self")
            await c.execute(f'SET ROLE "{role}"')
            partial = await get("overview", teamq, sample="partial_sources")
            assert all(card["value"] is None and card["rate"] is None for card in partial["metrics"])
            assert partial["pool_counts"]["all"] is None and not partial["filter_availability"]["stale"]["available"]
            partial_pool = await get("opportunities", teamq, sample="partial_source_pool")
            assert all(item["stale_days"] is None and item["stale_basis"] == "unknown" for item in partial_pool["items"])
            await get("opportunities", [*teamq, ("expected_scope_revision", first["scope"]["scope_revision"])], expected=409, sample="changed_permission_revision")
            result["checks"].append("missing/unauthorized target never yields denominator/rate or leaks target identity; partial sources never claim full value/count; changed revision returns409")
            await c.execute("RESET ROLE")
            for code in ("target.read", "customer.read", "visit.read"):
                await permission(code)
            await c.execute(f'SET ROLE "{role}"')
            before_move = await get("overview", teamq)
            await c.execute("RESET ROLE")
            await c.execute("UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE workspace_id=$1 AND user_ref_id=$2 AND team_id=$3", ws, user1, team2)
            await c.execute(f'SET ROLE "{role}"')
            await get("activities", [*teamq, ("activity", "pending_tasks"), ("expected_scope_revision", before_move["scope"]["scope_revision"])], expected=409, sample="changed_membership_revision")
            await c.execute("RESET ROLE")
            await permission("work_report.read", "teams", [team1])
            await c.execute(f'SET ROLE "{role}"')
            await get("overview", [*teamq, ("expected_scope_revision", before_move["scope"]["scope_revision"])], expected=403, sample="revoked_subject")
            result["checks"].append("live membership movement invalidates revision; one revoked subject rejects entire union403 before stale409")
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["failure"] = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        result["own_database_and_role_cleaned"] = not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1) OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$2)", db, role)
        await admin.close()
        paths = [f"backend/src/sales_backend/{part}/review_{name}.py" for name in ("scope", "multi") for part in ("contracts", "services")]
        paths += [f"backend/src/sales_backend/{part}/review_overview.py" for part in ("api", "contracts", "repositories", "services")]
        paths += ["backend/src/sales_backend/domain/review_aggregation.py", "backend/src/sales_backend/domain/review_changes.py", "backend/scripts/verify_review_multi_postgres.py"]
        result["source_sha256"] = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}
        result["PYTHONPATH"] = os.environ["PYTHONPATH"]
        result["production_or_LOCAL_accessed"] = False
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
        print(json.dumps({key: result[key] for key in ("status", "checks", "own_database_and_role_cleaned")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(verify(parser.parse_args()))
