# ruff: noqa: E501, S108 -- Opt-in isolated synthetic fixture, fixed SQL and explicit private socket.
"""D01/D04 delta proof only: real restricted-role PostgreSQL and ASGI."""

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
from sales_backend.contracts.review_overview import ReviewSelection
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError
from sales_backend.services.review_overview import read_metric_projection


async def verify(args):
    socket = Path(args.socket).resolve()
    assert str(socket).startswith(("/private/tmp/sb-review-pg.", "/tmp/sb-review-pg."))
    config = dict(host=str(socket), port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    db, role = "sb_delta04_" + uuid4().hex[:12], "sb_delta04_reader_" + uuid4().hex[:12]
    c, result, checks, calls, samples = None, None, [], [], {}
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        c = await asyncpg.connect(database=db, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 240)
        await c.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grants(c, role)
        await c.execute("SET row_security=on")
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        anchor = await c.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        quarter = (anchor.month - 1) // 3 + 1
        start = date(anchor.year, 3 * quarter - 2, 1)
        ids, actors = {}, {}
        for label in ("own", "foreign"):
            ws, team, owner, customer = [uuid4() for _ in range(4)]
            ids[label] = dict(ws=ws, team=team, owner=owner, customer=customer, partner=uuid4())
            await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)", ws, str(ws))
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'DELTA','Synthetic delta')", team, ws)
            await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)", owner, ws, label + "_owner")
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'sales','self',$3)", ws, owner, team)
            await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, owner, team)
            await c.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,$3,$3,$4,$4,$5)", customer, ws, label + "_customer", owner, team)
            await c.execute("INSERT INTO crm.partner(id,workspace_id,name) VALUES($1,$2,'Synthetic partner')", ids[label]["partner"], ws)
            actors[label] = ActorContext(workspace_id=str(ws), user_id=str(owner), role="sales", data_scope="self", team_ids=[str(team)])
        ws, owner = ids["own"]["ws"], ids["own"]["owner"]
        hidden_actor = uuid4()
        await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,'hidden_actor','HIDDEN_ACTOR_NAME','hidden_actor')", hidden_actor, ws)
        opportunities = {}

        async def business(ident, changes, *, actor_id=None, label="own"):
            spec = ids[label]
            version = await c.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", ident)
            await c.execute("INSERT INTO crm.business_change(workspace_id,customer_id,opportunity_id,actor_user_ref_id,version_no,changes) VALUES($1,$2,$3,$4,$5,$6::jsonb)", spec["ws"], spec["customer"], ident, actor_id or spec["owner"], version, changes)

        for label, names in (("own", ("roundtrip", "false_only", "state_extra", "restricted_actor")), ("foreign", ("FOREIGN_SECRET",))):
            spec = ids[label]
            await c.execute("SELECT set_config('app.workspace_id',$1,false),set_config('app.user_ref_id',$2,false),set_config('app.role_code','sales',false),set_config('app.team_ids',$3,false)", str(spec["ws"]), str(spec["owner"]), str(spec["team"]))
            for name in names:
                ident = uuid4()
                opportunities[name] = ident
                await c.execute("SELECT set_config('app.native_opportunity_create',$1,false)", str(ident))
                await c.execute("INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount,currency,probability,stage_code,status,sales_channel,created_by_user_ref_id) VALUES($1,$2,$3,$4,$5,$6,1000,'CNY',30,'qualified','open','direct',$5)", ident, spec["ws"], spec["customer"], name, spec["owner"], spec["team"])
                await c.execute("INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,collection_amount,recognized_amount,collection_confidence) VALUES($1,$2,$3,$4,100,200,'high')", spec["ws"], ident, anchor.year, quarter)
                if name == "roundtrip":
                    for before, after in (("direct", "partner"), ("partner", "direct")):
                        await c.execute("UPDATE crm.opportunity SET sales_channel=$2,partner_id=$3,partner_name=$4 WHERE id=$1", ident, after, spec["partner"] if after == "partner" else None, "Synthetic partner" if after == "partner" else None)
                        await business(ident, [{"label": "销售渠道", "before": before, "after": after}])
                elif name == "false_only":
                    await c.execute("UPDATE crm.opportunity SET updated_at=clock_timestamp() WHERE id=$1", ident)
                    await business(ident, [{"label": "销售渠道", "before": "partner", "after": "partner"}, {"field": "field_edit_reason", "before": None, "after": {"reason": "metadata only"}}])
                elif name == "state_extra":
                    await c.execute("UPDATE crm.opportunity SET amount=1500,probability=70,stage_code='proposal',name='state_extra_changed' WHERE id=$1", ident)
                    await business(ident, [{"field": "name", "before": "state_extra", "after": "state_extra_changed"}, {"field": "probability", "before": "30", "after": "70"}])
                    await c.execute("UPDATE crm.opportunity_forecast SET collection_amount=200 WHERE opportunity_id=$1", ident)
                else:
                    await c.execute("UPDATE crm.opportunity SET name=name||'_changed' WHERE id=$1", ident)
                    await business(ident, [{"field": "name", "before": name, "after": name + "_changed"}], actor_id=hidden_actor if label == "own" else None, label=label)
        for kind, amount in (("collection", 1000), ("collection_ppl", 2000), ("forecast_recognized", 1000)):
            await c.execute("INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end) VALUES($1,$2,'person',$3,$4,$5,$3,'quarter',$6,($6::date+interval '3 months - 1 day')::date)", ws, anchor.year, owner, kind, amount, start)
        actor = actors["own"]

        class DB:
            settings = SimpleNamespace()

            @asynccontextmanager
            async def transaction(self, current_actor, readonly=False, isolation="read_committed"):
                async with c.transaction(readonly=readonly, isolation=isolation):
                    await c.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(c, current_actor)
                    assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                    yield c

        database = DB()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database] = lambda: database
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)

        @app.exception_handler(WorkReportError)
        async def error(request, exc):
            return JSONResponse({"code": exc.code, "detail": exc.detail}, status_code=exc.status)

        selected = dict(scope="self", anchor_date=str(anchor), period_type="day")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            async def get(endpoint="overview", **params):
                response = await client.get("/api/v1/review/" + endpoint, params={**selected, **params})
                calls.append({"endpoint": endpoint, "params": params, "status": response.status_code})
                assert response.status_code == 200, response.text
                return response.json()

            overview = await get()
            assert [m["metric_code"] for m in overview["metrics"]] == ["forecast_collection", "collection_ppl", "forecast_recognized"]
            assert [m["value"] for m in overview["metrics"]] == ["500.00", "500.00", "200.00"]
            assert [m["target"]["kind"] for m in overview["metrics"]] == ["collection", "collection_ppl", "forecast_recognized"]
            assert [m["rate"] for m in overview["metrics"]] == ["0.50000000", "0.25000000", "0.20000000"]
            async with database.transaction(actor, readonly=True, isolation="repeatable_read") as connection:
                projection = await read_metric_projection(connection, actor, ReviewSelection(**selected))
            assert [m["metric_code"] for m in projection["metrics"]] == ["forecast_collection", "collection_ppl", "forecast_recognized"]
            third = await get("opportunities", metric_code="forecast_recognized")
            assert third["known_total"] == 1 and third["items"][0]["id"] == str(opportunities["state_extra"])
            samples["top_cards"] = overview["metrics"]
            checks.append("D01 compatibility: overview API retains all three metrics for existing Web; independent collection/PPL denominators; shared projection and recognized drilldown retain third metric; UI owns two top cards")

            details = await get("activities", activity="updated_opportunities")
            assert details["known_total"] == 3 and details["total"] is None and details["coverage"] == "partial"
            assert details["history_coverage"]["complete_from"] is None
            action = next(a for a in overview["activities"] if a["code"] == "updated_opportunities")
            assert action["count"] is None and action["known_count"] == 3 and action["history_coverage"] == details["history_coverage"]
            by_id = {row["id"]: row for row in details["items"]}
            assert str(opportunities["false_only"]) not in by_id and len(by_id) == len(details["items"])
            roundtrip = by_id[str(opportunities["roundtrip"])]
            assert [[v for v in (e["changes"][0]["before"], e["changes"][0]["after"])] for e in roundtrip["events"]] == [["direct", "partner"], ["partner", "direct"]]
            assert all(e["event_id"] == e["id"] and e["kind"] == "business_change" for e in roundtrip["events"])
            pages = [await get("activities", activity="updated_opportunities", limit=1, offset=i) for i in range(4)]
            assert [page["items"][0]["id"] for page in pages[:3]] == [r["id"] for r in details["items"]]
            assert not pages[3]["items"] and all(page["known_total"] == 3 for page in pages)
            samples["changes"] = details
            checks.append("same-value and reason-only events removed before distinct opportunity counts/order/pagination; A-B-A remains two events in one item")

            extra = by_id[str(opportunities["state_extra"])]
            assert {e["kind"] for e in extra["events"]} == {"business_change", "opportunity_state", "opportunity_forecast"}
            fields = [ch["field"] for e in extra["events"] for ch in e["changes"]]
            assert fields.count("probability") == 1 and {"name", "amount", "stage_code", "collection_amount"} <= set(fields)
            assert all(e["actor_id"] is None and e["actor_status"] == "not_recorded" for e in extra["events"] if e["kind"] != "business_change")
            assert roundtrip["events"][0]["actor_display_name"] == "own_owner"
            hidden = by_id[str(opportunities["restricted_actor"])]["events"][0]
            assert hidden["actor_id"] is None and hidden["actor_display_name"] is None and hidden["actor_status"] == "restricted"
            serialized = json.dumps(details)
            assert str(hidden_actor) not in serialized and "HIDDEN_ACTOR_NAME" not in serialized and "FOREIGN_SECRET" not in serialized
            checks.append("same-version exact probability evidence coalesced without masking extra state fields; independent forecast kept; authorized actor name only, missing actors null, foreign records absent")

            pool = await get("opportunities", filter="all")
            assert pool["known_total"] == 4
            no_change = next(r for r in pool["items"] if r["id"] == str(opportunities["false_only"]))
            assert no_change["updated_in_selected_day"] is None
            assert all(overview["filter_availability"][f]["available"] for f in ("all", "stale", "high_risk", "key_collection"))
            not_updated = await get("opportunities", filter="not_updated")
            assert not not_updated["available"] and not_updated["total"] is None and not not_updated["items"]
            key_collection = await get("opportunities", filter="key_collection")
            assert key_collection["known_total"] == 4
            checks.append("unknown all-field history keeps not_updated unavailable; timestamps do not invent false; all/stale/high_risk/key_collection capabilities retained")

            role_id = await c.fetchval("SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='sales'", ws)
            await c.execute("DELETE FROM config.permission_role_grant WHERE role_id=$1 AND permission_code='opportunity.read'", role_id)
            removed = await get("activities", activity="updated_opportunities")
            assert removed["items"] == [] and removed["known_total"] == 0 and removed["total"] is None
            assert removed["coverage"] == "unavailable"
            samples["source_revoked"] = removed
            checks.append("same actor opportunity.read revocation returns no event identity/value and unavailable/null instead of a fabricated zero aggregate")
        files = ["api/review_overview.py", "contracts/review_overview.py", "repositories/review_overview.py", "services/review_overview.py", "domain/review_changes.py", "domain/opportunities.py"]
        result = dict(status="passed_isolated_postgres_and_asgi", check_count=len(checks), checks=checks,
                      http_calls=calls, response_samples=samples, synthetic_data=True, migrations_through=240,
                      source_sha256={"backend/src/sales_backend/" + p: hashlib.sha256((ROOT / "backend/src/sales_backend" / p).read_bytes()).hexdigest() for p in files},
                      script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      boundaries=["real restricted API role with RLS, synthetic ASGI identity", "no production/LOCAL/model/browser", "no migration or ACL product changes"])
    finally:
        if c is not None:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
    if result is not None:
        result["cleanup"] = dict(database_dropped=True, role_dropped=True, cluster_stopped=False)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = asyncio.run(verify(args))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps({"status": result["status"], "groups": result["check_count"], "http_calls": len(result["http_calls"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
