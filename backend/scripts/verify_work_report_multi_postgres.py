# ruff: noqa: E501, S108 -- Explicit disposable Unix socket and synthetic SQL evidence only.
"""Isolated PostgreSQL multi-report persistence proof.

Runs real ASGI report endpoints and leased snapshot/generation handlers under
separate restricted API/worker roles. Provider unavailability is synthetic:
weekly output must be the real deterministic basic_summary, never AI success.
Both roles use the current runtime permission manifest without extra grants.
It does not change LOCAL or production.
"""

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
from uuid import UUID, uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from verify_review_okr_postgres import migrate_through

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.work_reports import router
from sales_backend.services.work_report_jobs import WorkReportSnapshotHandler, WorkReportGenerationHandler
from sales_backend.services.work_report_generation import generate_work_report, GenerationProviders
from sales_backend.services.agent_platform.routing import RoutingPolicy
from sales_backend.domain.work_report import default_scheme
from sales_backend.job_context import JobLease,current_job_lease
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError

ROOT = Path(__file__).resolve().parents[2]


async def verify(args):
    socket = Path(args.socket).resolve()
    assert str(socket).startswith(("/private/tmp/sb-review-pg.", "/tmp/sb-review-pg."))
    config = dict(host=str(socket), port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid4().hex[:12]
    db, role = "sb_review_multi_" + suffix, "sb_review_multi_api_" + suffix
    result = dict(status="running", checks=[], calls=[], samples={}, database=db, role=role)
    worker_role=role+"_worker"
    c = None
    try:
        await admin.execute(f"CREATE DATABASE \"{db}\" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'")
        c = await asyncpg.connect(database=db, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 250)
        manifest = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())
        for service, runtime_role in (("api", role), ("worker", worker_role)):
            await c.execute(f'CREATE ROLE "{runtime_role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            policy = manifest["services"][service]
            for schema in policy["schemas"]:
                await c.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{runtime_role}"')
            for table, privs in policy["tables"].items():
                await c.execute(f'GRANT {",".join(privs)} ON {table} TO "{runtime_role}"')
            for signature in policy["functions"]:
                await c.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{runtime_role}"')
            for table, grants in policy["columns"].items():
                for priv, cols in grants.items():
                    await c.execute(f'GRANT {priv}({",".join(cols)}) ON {table} TO "{runtime_role}"')
        await c.execute("SELECT security.reconcile_runtime_grants()")
        result["fixture_extra_grants"] = []
        result["worker_role"] = worker_role
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
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)", ws, uid, "supervisor" if uid == user1 else "sales", "workspace" if uid == user1 else "self")
        for uid, tid in ((user1, team1), (user1, team2), (user2, team2)):
            await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, uid, tid)
        await c.execute("UPDATE platform.team_membership SET membership_role='supervisor' WHERE user_ref_id=$1",user1)
        permission_role = await c.fetchval("SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='supervisor'", ws)
        async def permission(code, scope="workspace", teams=()):
            await c.execute("INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids) VALUES($1,$2,$3,$4,$5::uuid[]) ON CONFLICT(role_id,permission_code) DO UPDATE SET scope_code=excluded.scope_code,team_ids=excluded.team_ids", ws, permission_role, code, scope, list(teams))
        for code in ("work_report.write", "agent.work_report", "work_report.read", "customer.read", "opportunity.read", "visit.read", "task.read", "target.read", "risk.read"):
            await permission(code)
        actor = ActorContext(workspace_id=str(ws), user_id=str(user1), role="supervisor", data_scope="workspace", team_ids=[str(team1), str(team2)])
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

        lock=asyncio.Lock()
        class Database:
            settings=SimpleNamespace()
            @asynccontextmanager
            async def transaction(self, identity, **options):
                async with lock:
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
            for case,kind,ids,period_kind,review_mode in (("team","team",[team1,team2],"week","weekly"),("member","member",[user1,user2],"week","weekly"),("monthly_team","team",[team1,team2],"month",None)):
                body=dict(period=dict(kind=period_kind,anchor_date=anchor.isoformat()),sources=["visit"],scope=dict(contract_version="work_report.scope_selection.v2",kind=kind,**{kind+"_ids":[str(x) for x in ids]}))
                if review_mode is not None:
                    body["review_mode"] = review_mode
                response=await client.post('/api/v1/work-reports/snapshots',json=body,headers={"Idempotency-Key":str(uuid4())})
                result["calls"].append(dict(case=case,kind=kind,status=response.status_code,response=response.json()))
                assert response.status_code==202,response.text
                if response.status_code==202:
                    snapshot_id=response.json()["id"]
                    await c.execute("RESET ROLE")
                    lease=uuid4()
                    job=await c.fetchval("UPDATE ops.job SET status='running',lease_token=$2,locked_until=clock_timestamp()+interval '10 minutes' WHERE aggregate_id=$1 RETURNING id",UUID(snapshot_id),lease)
                    await c.execute(f'SET ROLE "{worker_role}"')
                    token=current_job_lease.set(JobLease(str(job),str(lease)))
                    try:
                        await WorkReportSnapshotHandler(Database()).handle(snapshot_id,actor)
                        result["checks"].append("snapshot handler complete "+case)
                    except Exception as error:
                        result["calls"].append(dict(case=case,kind=kind,worker_error=type(error).__name__,detail=str(error),traceback=traceback.format_exc()))
                    finally:
                        current_job_lease.reset(token)
                        await c.execute(f'SET ROLE "{role}"')
                    snap=(await client.get('/api/v1/work-reports/snapshots/'+snapshot_id)).json()
                    result["calls"].append(dict(case=case,kind=kind,snapshot_status=snap["status"],scope=snap["scope"],coverage=snap.get("coverage")))
                    assert snap["status"]=="ready",snap
                    if review_mode is None:
                        assert snap.get("review_facts") is None
                    if snap["status"]=='ready':
                        scheme_response=await client.post('/api/v1/work-reports/schemes',json=dict(name="QA multi "+kind,config=default_scheme().model_dump(mode="json")),headers={"Idempotency-Key":str(uuid4())})
                        assert scheme_response.status_code==201,scheme_response.text
                        scheme=scheme_response.json()
                        run_response=await client.post('/api/v1/work-reports/runs',json=dict(snapshot_id=snapshot_id,snapshot_hash=snap["snapshot_hash"],scheme=dict(scheme_id=scheme["id"],version_no=scheme["version_no"])),headers={"Idempotency-Key":str(uuid4())})
                        assert run_response.status_code==202,run_response.text
                        run_id=run_response.json()["id"]
                        await c.execute("RESET ROLE")
                        runlease=uuid4()
                        runjob=await c.fetchval("UPDATE ops.job SET status='running',lease_token=$2,locked_until=clock_timestamp()+interval '10 minutes' WHERE aggregate_id=$1 RETURNING id",UUID(run_id),runlease)
                        await c.execute(f'SET ROLE "{worker_role}"')
                        async def unavailable(*args,**kwargs):
                            from sales_backend.services.agent_platform.routing import ProviderUnavailable
                            raise ProviderUnavailable("QA no model configured")
                        async def generate(request,**kwargs):
                            return await generate_work_report(request,providers=GenerationProviders(direct=unavailable),routing_policy=RoutingPolicy(platform_enabled=False),assert_active=kwargs['assert_active'])
                        token=current_job_lease.set(JobLease(str(runjob),str(runlease)))
                        try:
                            await WorkReportGenerationHandler(Database(),generate=generate).handle(run_id,actor)
                        finally:
                            current_job_lease.reset(token)
                            await c.execute(f'SET ROLE "{role}"')
                        run=(await client.get('/api/v1/work-reports/runs/'+run_id)).json()
                        assert run['status']=='succeeded' and run['result_status']=='basic_summary' and run['provider']=='none',run
                        result["calls"].append(dict(case=case,kind=kind,run=run))
                        docresponse=await client.get('/api/v1/work-reports/documents/'+run['document_id'])
                        assert docresponse.status_code==200,docresponse.text
                        assert docresponse.json()['scope']['subject_ids']==sorted(str(x) for x in ids)
                        assert next(x for x in snap['coverage'] if x['source_type']=='visit')['included']==1
                        doc2=await client.get('/api/v1/work-reports/documents/'+run['document_id'])
                        assert doc2.status_code==200 and doc2.json()==docresponse.json()
                        result["checks"].append("persisted document reentry "+case)


            await c.execute("RESET ROLE")
            await permission("work_report.read", "teams", [team1])
            await c.execute(f'SET ROLE "{role}"')
            revoked=await client.get('/api/v1/work-reports/documents/'+run['document_id'])
            result["calls"].append(dict(permission_revoked_document_status=revoked.status_code))
            assert revoked.status_code in (403,404),revoked.text
            result["checks"].append("saved multi-team document denied after scope permission narrowed")
        result["status"]="passed"
    except Exception as exc:
        result["status"] = "failed"
        result["failure"] = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{worker_role}"')
        result["own_database_and_role_cleaned"] = not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1) OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname=ANY($2::text[]))", db, [role, worker_role])
        await admin.close()
        paths = [f"backend/src/sales_backend/{part}/review_{name}.py" for name in ("scope", "multi") for part in ("contracts", "services")]
        paths += [f"backend/src/sales_backend/{part}/review_overview.py" for part in ("api", "contracts", "repositories", "services")]
        paths += ["backend/src/sales_backend/domain/review_aggregation.py", "backend/src/sales_backend/domain/review_changes.py", "backend/scripts/verify_work_report_multi_postgres.py"]
        paths += ["tools/installation/runtime_permissions.json"]
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
