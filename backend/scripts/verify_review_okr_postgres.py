"""Opt-in synthetic O/KR proof on an explicitly supplied isolated Unix socket.

Creates and drops only its own randomized DB/NOLOGIN roles. No saved DSNs, real
business records, production endpoints, model calls or notification workers.
"""
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "database/tests"))
sys.path.insert(0, str(ROOT / "database/scripts"))
from migrate import apply, item
from verify_runtime_grants_postgres import migrate_through

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.review_okr import router
from sales_backend.services.operations import OperationsError
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.review_okr import ReviewOkrRepository
from sales_backend.services.review_okr import read_key_results
from sales_backend.services.review_overview import read_metric_projection
from sales_backend.contracts.review_overview import ReviewSelection


async def grant_api(conn, role, *, stage):
    # Each fixture starts before its next migration; the moving installation
    # policy can already contain tables/functions from later core stages.
    frozen = json.loads((ROOT / "backend/tests/fixtures/review_release_contract.frozen.json").read_text())
    policy = frozen["policies"][stage]
    assert policy["schema"] == stage
    actual = await conn.fetchval(
        "SELECT version FROM ops.schema_migration "
        "ORDER BY substring(version FROM 2)::integer DESC LIMIT 1"
    )
    assert actual == stage, (actual, stage)
    rules = policy["services"]["api"]
    for schema in rules["schemas"]:
        await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
    for table, privileges in rules["tables"].items():
        await conn.execute(f'GRANT {",".join(privileges)} ON {table} TO "{role}"')
    for table, grants in rules["columns"].items():
        for privilege, columns in grants.items():
            await conn.execute(f'GRANT {privilege}({",".join(columns)}) ON {table} TO "{role}"')
    for signature in rules["functions"]:
        assert await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", signature), signature
        await conn.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')


async def verify():
    host = os.environ.get("PGHOST", "")
    if not host.startswith("/tmp/sb-review-pg."):
        raise ValueError("Only the explicitly created sb-review-pg temporary Unix socket is allowed")
    config = dict(host=host, port=int(os.environ["PGPORT"]), user=os.environ.get("PGUSER", "postgres"),
                  password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid4().hex[:12]
    db, api, worker = "sb_okr_" + suffix, "sb_okr_api_" + suffix, "sb_okr_worker_" + suffix
    conn = None
    checks = []
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        conn = await asyncpg.connect(database=db, **config)
        await migrate_through(conn, 240)
        for role in (api, worker):
            await conn.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grant_api(conn, api, stage="V240")
        await conn.execute(f'GRANT USAGE ON SCHEMA security,crm,common TO "{worker}"')
        path = ROOT / "database/migrations/V241__review_okr.sql"
        await apply(conn, item("V241", path), ROOT / "database")
        checks.append("V240_to_V241_actual_postgres_forward_migration")
        for table in ("crm.review_objective", "crm.review_key_result", "crm.review_key_result_progress"):
            assert await conn.fetchval("SELECT has_table_privilege($1,$2,'SELECT')", api, table)
            assert not await conn.fetchval("SELECT has_table_privilege($1,$2,'INSERT,UPDATE,DELETE')", api, table)
            assert not await conn.fetchval("SELECT has_table_privilege($1,$2,'SELECT')", worker, table)
        for role in (api, worker):
            assert not await conn.fetchval("SELECT has_function_privilege($1,'security.review_okr_participants(uuid,jsonb)','EXECUTE')", role)
            assert not await conn.fetchval("SELECT has_function_privilege($1,'security.reconcile_runtime_grants()','EXECUTE')", role)
        assert not await conn.fetchval("SELECT has_function_privilege($1,'security.save_review_objective(uuid,jsonb,integer)','EXECUTE')", worker)
        checks.append("exact_API_read_and_mutation_functions_no_worker_or_direct_write_grants")

        fixtures = []
        for label in ("A", "B"):
            ws, owner, member, team = [uuid4() for _ in range(4)]
            await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic Okr')", ws, str(ws))
            await conn.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
            for user, role in ((owner, "operations"), (member, "sales")):
                await conn.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$4,$4)",
                                   user, ws, str(user), "OKR_" + label + "_" + role)
                await conn.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)",
                                   ws, user, role, "workspace" if role == "operations" else "self")
            await conn.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'Synthetic Okr team')", team, ws, str(team))
            for user in (owner, member):
                await conn.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, user, team)
            fixtures.append(dict(ws=ws, owner=owner, member=member, team=team))
        a, b = fixtures
        # Existing sales target.read is intentionally broad; use an explicit
        # denied synthetic account to verify collaborator membership never grants access.
        await conn.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)", a["ws"], a["member"])
        await conn.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'target.read','deny')", a["ws"], a["member"])
        for typename in ("json", "jsonb"):
            await conn.set_type_codec(typename, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        actors = [ActorContext(workspace_id=str(f["ws"]), user_id=str(f["owner"]), role="operations", data_scope="workspace") for f in fixtures]
        await conn.execute("SET row_security=on")
        await conn.execute(f'SET ROLE "{api}"')
        repo = ReviewOkrRepository()

        @asynccontextmanager
        async def tx(actor=actors[0]):
            async with conn.transaction():
                await set_request_context(conn, actor)
                yield

        async def reject(fn, code, actor=actors[0]):
            try:
                async with tx(actor):
                    await fn()
            except asyncpg.PostgresError as exc:
                assert exc.sqlstate == code, (exc.sqlstate, str(exc))
            else:
                raise AssertionError("Expected SQL rejection " + code)

        def objective_body(f=a, **changes):
            return dict(title="Synthetic O", scope_type="team", user_id=None, team_id=str(f["team"]),
                department_code="sales", period_type="quarter", period_start="2026-10-01", period_end="2026-12-31",
                owner_user_id=str(f["owner"]), collaborator_user_ids=[str(f["member"])] * 2, parent_id=None, **changes)

        async with tx():
            o = await repo.save(conn, "objective", None, objective_body())
            assert o["collaborator_user_ids"] == [str(a["member"])]
            other = await repo.save(conn, "objective", None, objective_body() | {"title": "Child", "parent_id": o["id"]})
            fetched = await repo.objectives(conn, objective_id=UUID(o["id"]))
            assert fetched["total"] == 1 and fetched["items"][0]["editable"]
        checks.append("objective_create_deduplicate_and_real_RLS_readback")
        mutable = {k: o[k] for k in ("title", "owner_user_id", "collaborator_user_ids", "parent_id", "status")}
        await reject(lambda: repo.save(conn, "objective", UUID(o["id"]), mutable | {"parent_id": other["id"]}, 1), "22023")
        async with tx():
            updated = await repo.save(conn, "objective", UUID(o["id"]), mutable | {"title": "Updated"}, 1)
            assert updated["version_no"] == 2
        await reject(lambda: repo.save(conn, "objective", UUID(o["id"]), mutable, 1), "P0001")
        await reject(lambda: repo.save(conn, "objective", None, objective_body() | {"period_start": "2027-01-01", "period_end": "2027-03-31", "parent_id": o["id"]}), "22023")
        checks.append("objective_cycle_period_and_stale_version_rejected")
        async with tx(actors[1]):
            foreign = await repo.save(conn, "objective", None, objective_body(b))
            assert (await repo.objectives(conn, objective_id=UUID(o["id"]))) ["total"] == 0
        await reject(lambda: repo.save(conn, "objective", None, objective_body() | {"parent_id": foreign["id"]}), "P0002")
        await reject(lambda: repo.save(conn, "objective", None, objective_body() | {"owner_user_id": str(b["owner"])}), "42501")
        checks.append("foreign_tenant_parent_owner_and_read_rejected")
        member_actor = ActorContext(workspace_id=str(a["ws"]), user_id=str(a["member"]), role="sales", data_scope="self")
        async with tx(member_actor):
            assert (await repo.objectives(conn, objective_id=UUID(o["id"]))) ["total"] == 0
        await reject(lambda: repo.save(conn, "objective", None, objective_body()), "42501", member_actor)
        checks.append("collaborator_assignment_does_not_expand_member_permissions")

        async with tx():
            targets = await conn.fetchval("SELECT security.save_target_batch('team',NULL,$1,'quarter',$2,$3,$4::jsonb,'Synthetic','sales')",
                a["team"], date(2026, 10, 1), date(2026, 12, 31), [{"kind": "collection", "amount": "1000", "version_no": None}])
            target = targets["items"][0]
            kbody = dict(objective_id=o["id"], title="Synthetic KR", metric_code="forecast_collection", target_id=target["id"],
                         owner_user_id=str(a["owner"]), collaborator_user_ids=[], parent_id=None)
            kr = await repo.save(conn, "key_result", None, kbody)
            child = await repo.save(conn, "key_result", None, kbody | {"parent_id": kr["id"]})
            fetched = (await repo.key_results(conn, key_result_id=UUID(kr["id"]))) ["items"][0]
            assert fetched["effective_target"]["value"] == "1000.00" and fetched["progress_mode"] == "automatic"
            assert fetched["progress"]["value"] is None
        await reject(lambda: repo.save(conn, "key_result", None, kbody | {"metric_code": "forecast_recognized"}), "22023")
        kmutable = {key: kr[key] for key in ("title", "owner_user_id", "collaborator_user_ids", "parent_id", "status")}
        await reject(lambda: repo.save(conn, "key_result", UUID(kr["id"]), kmutable | {"parent_id": child["id"]}, 1), "22023")
        await reject(lambda: conn.fetchval("SELECT security.confirm_review_key_result_progress($1,1,500,'Synthetic',true)", UUID(kr["id"])), "22023")
        checks.append("KR_target_identity_tax_kind_cycle_and_financial_manual_write_guards")

        async with tx():
            await conn.fetchval("SELECT security.save_review_metric('custom_calls',$1::jsonb,NULL)",
                               dict(name="Synthetic calls", unit="次", decimal_places=0, definition="Synthetic confirmed", active=True))
            manual = await repo.save(conn, "key_result", None, kbody | {"metric_code": "custom_calls", "target_id": None})
            progress = await conn.fetchval("SELECT security.confirm_review_key_result_progress($1,1,0,'Synthetic observed zero',true)", UUID(manual["id"]))
            assert progress["progress"]["value"] == "0.00" and progress["key_result"]["version_no"] == 2
            read = (await repo.key_results(conn, key_result_id=UUID(manual["id"]))) ["items"][0]
            assert read["progress"]["value"] == "0.00" and read["progress"]["status"] == "confirmed"
            assert await conn.fetchval("SELECT amount FROM crm.sales_target WHERE id=$1", UUID(target["id"])) == Decimal("1000")
        await reject(lambda: conn.fetchval("SELECT security.confirm_review_key_result_progress($1,2,1,' ',true)", UUID(manual["id"])), "22023")
        await reject(lambda: conn.fetchval("SELECT security.confirm_review_key_result_progress($1,2,1.5,'Synthetic',true)", UUID(manual["id"])), "22023")
        await reject(lambda: conn.fetchval("SELECT security.confirm_review_key_result_progress($1,1,1,'Synthetic',true)", UUID(manual["id"])), "P0001")
        checks.append("manual_zero_evidence_precision_confirmation_and_concurrent_version")
        async with tx():
            note = await conn.fetchval("SELECT security.mention_review_okr('objective',$1,2,$2::uuid[])", UUID(o["id"]), [a["owner"]])
            replay = await conn.fetchval("SELECT security.mention_review_okr('objective',$1,2,$2::uuid[])", UUID(o["id"]), [a["owner"]])
            assert note["created"] == 1 and replay["created"] == 0 and not note["permission_changed"]
        await reject(lambda: conn.fetchval("SELECT security.mention_review_okr('objective',$1,2,$2::uuid[])", UUID(o["id"]), [a["member"]]), "42501")
        await reject(lambda: conn.fetchval("SELECT security.mention_review_okr('objective',$1,2,$2::uuid[])", UUID(o["id"]), [b["owner"]]), "42501")
        checks.append("in_app_mentions_deduplicate_without_grant_or_cross_tenant_disclosure")
        # Two independent API sessions race opposing graph edges. The tenant lock
        # must serialize them so exactly one succeeds and the other sees the cycle.
        async with tx():
            left = await repo.save(conn, "objective", None, objective_body() | {"title": "Left"})
            right = await repo.save(conn, "objective", None, objective_body() | {"title": "Right"})
        async def edge(node, parent):
            parallel = await asyncpg.connect(database=db, **config)
            try:
                for typename in ("json", "jsonb"):
                    await parallel.set_type_codec(typename, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
                await parallel.execute("SET row_security=on")
                await parallel.execute(f'SET ROLE "{api}"')
                try:
                    async with parallel.transaction():
                        await set_request_context(parallel, actors[0])
                        body = {k: node[k] for k in ("title", "owner_user_id", "collaborator_user_ids", "parent_id", "status")}
                        await repo.save(parallel, "objective", UUID(node["id"]), body | {"parent_id": parent["id"]}, 1)
                        return "saved"
                except asyncpg.PostgresError as exc:
                    return exc.sqlstate
            finally:
                await parallel.close()
        outcome = await asyncio.gather(edge(left, right), edge(right, left))
        assert sorted(outcome) == ["22023", "saved"], outcome
        checks.append("concurrent_opposing_parent_edges_cannot_form_cycle")

        active = actors[0]
        class TestDB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False, isolation="read_committed"):
                async with conn.transaction(readonly=readonly, isolation=isolation):
                    await set_request_context(conn, actor)
                    yield conn
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database] = lambda: TestDB()
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=active)
        @app.exception_handler(OperationsError)
        async def operation_error(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)
        @app.exception_handler(IdempotencyConflict)
        async def idempotency_error(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=409)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            payload = objective_body() | {"title": "HTTP objective", "anchor_date": "2026-10-14"}
            payload.pop("period_start"); payload.pop("period_end")
            key = str(uuid4())
            first = await client.post("/api/v1/review/objectives", json=payload, headers={"Idempotency-Key": key})
            replay = await client.post("/api/v1/review/objectives", json=payload, headers={"Idempotency-Key": key})
            assert first.status_code == 200 and replay.json() == first.json(), (first.status_code, first.text, replay.text)
            row = first.json()
            read = await client.get("/api/v1/review/objectives/" + row["id"])
            assert read.status_code == 200 and read.json()["title"] == "HTTP objective"
            patch = {k: row[k] for k in ("title", "owner_user_id", "collaborator_user_ids", "parent_id", "status", "version_no")}
            update_key = str(uuid4())
            saved = await client.patch("/api/v1/review/objectives/" + row["id"], json=patch | {"title": "HTTP updated"},headers={"Idempotency-Key":update_key})
            stale = await client.patch("/api/v1/review/objectives/" + row["id"], json=patch)
            assert saved.status_code == 200 and stale.status_code == 409, (saved.text, stale.text)
            saved_replay=await client.patch("/api/v1/review/objectives/"+row["id"],json=patch | {"title":"HTTP updated"},headers={"Idempotency-Key":update_key})
            assert saved_replay.status_code==200 and saved_replay.json()==saved.json()
            wrong = await client.post("/api/v1/review/objectives", json=payload | {"amount": "100"})
            assert wrong.status_code == 422
            active = actors[1]
            hidden = await client.get("/api/v1/review/objectives/" + row["id"])
            assert hidden.status_code == 404
        checks.append("actual_ASGI_create_get_update_409_422_hidden404_idempotency_replay")
        await conn.execute("RESET ROLE")
        assert await conn.fetchval("SELECT count(*) FROM crm.customer_actual WHERE workspace_id=$1", a["ws"]) == 0
        assert await conn.fetchval("SELECT count(*) FROM workflow.notification WHERE workspace_id=$1", a["ws"]) == 1
        # New automatic progress sources are synthetic financial records, and
        # are read only through the same restricted API role as the actual app.
        customer, opportunity = uuid4(), uuid4()
        async with conn.transaction():
            await set_request_context(conn, actors[0])
            await conn.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,'Synthetic progress','synthetic progress',$3,$3,$4)", customer,a["ws"],a["owner"],a["team"])
            await conn.execute("INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,created_by_user_ref_id,owner_user_ref_id,owner_team_id,amount,currency,probability,stage_code) VALUES($1,$2,$3,'Synthetic forecast',$4,$4,$5,1000,'CNY',70,'proposal')",opportunity,a["ws"],customer,a["owner"],a["team"])
            await conn.execute("INSERT INTO crm.opportunity_forecast(workspace_id,opportunity_id,year,quarter,collection_amount,recognized_amount,collection_confidence) VALUES($1,$2,2026,4,12.34,56.78,'high')",a["ws"],opportunity)
            for kind,tax in (("collection","not_applicable"),("recognized","exclusive")):
                await conn.execute("INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,source_ref,request_id,confirmed_by_user_ref_id,tax_basis) VALUES($1,$2,$3,$4,7.89,'2026-10-01','SYNTHETIC',$5,$6,$7)",a["ws"],customer,opportunity,kind,uuid4(),a["owner"],tax)
        await conn.execute(f'SET ROLE "{api}"')
        async with tx():
            auto_kr = []
            for code in ("collection_ppl", "forecast_recognized", "collection", "recognized"):
                auto_kr.append(await repo.save(conn,"key_result",None,kbody | {"metric_code":code,"target_id":None}))
        selection = ReviewSelection(scope="team",team_id=a["team"],anchor_date=date(2026,10,1),period_type="quarter")
        async def projection_and_krs():
            async with conn.transaction(readonly=True,isolation="repeatable_read"):
                await set_request_context(conn,actors[0])
                projection=await read_metric_projection(conn,actors[0],selection)
                rows=(await read_key_results(conn,actors[0],objective_id=UUID(o["id"]))) ["items"]
                for card in projection["metrics"]:
                    matching=[r for r in rows if r["metric_code"]==card["metric_code"]]
                    assert matching
                    for row in matching:
                        for key in ("value","known_subtotal","coverage","missing_amount_count","unknown_eligibility_count","missing_reasons","source_refs"):
                            assert row["progress"][key]==card[key], (key,row["progress"],card)
                return rows
        async with conn.transaction(readonly=True,isolation="repeatable_read"):
            await set_request_context(conn,actors[0])
            denied_source=(await read_key_results(conn,actors[0],key_result_id=UUID(kr["id"]))) ["items"][0]
            assert denied_source["progress"]["reason"]=="forecast_source_permission_denied"
        await conn.execute("RESET ROLE")
        await conn.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2) ON CONFLICT DO NOTHING",a["ws"],a["owner"])
        await conn.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'work_report.read','allow','workspace')",a["ws"],a["owner"])
        await conn.execute(f'SET ROLE "{api}"')
        checks.append("target_read_does_not_grant_forecast_source_permission")
        rows=await projection_and_krs()
        for code in ("collection","recognized"):
            progress=next(r["progress"] for r in rows if r["metric_code"]==code)
            assert progress["value"] is not None and Decimal(progress["value"])==Decimal("7.89"), progress
            assert progress["source_kind"]=="actual" and progress["complete"]
        checks.append("all_three_forecast_KRs_equal_same_scope_card_projection_and_live_target")
        checks.append("actual_collection_recognized_use_existing_resolved_actual_source_not_forecasts")
        await conn.execute("RESET ROLE")
        async with conn.transaction():
            await set_request_context(conn, actors[0])
            await conn.execute("UPDATE crm.opportunity_forecast SET recognized_amount=NULL,collection_confidence=NULL WHERE opportunity_id=$1",opportunity)
            await conn.execute("UPDATE crm.customer_actual SET voided_at=clock_timestamp(),voided_by_user_ref_id=$2,void_reason='Synthetic correction' WHERE workspace_id=$1 AND kind='recognized'",a["ws"],a["owner"])
            await conn.execute("INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,source_ref,request_id,confirmed_by_user_ref_id,tax_basis) VALUES($1,$2,$3,'recognized',7.89,'2026-10-01','SYNTHETIC_CORRECTION',$4,$5,'unknown')",a["ws"],customer,opportunity,uuid4(),a["owner"])
        await conn.execute(f'SET ROLE "{api}"')
        changed=await projection_and_krs()
        assert next(r["progress"] for r in changed if r["metric_code"]=="forecast_recognized")["value"] is None
        assert next(r["progress"] for r in changed if r["metric_code"]=="forecast_collection")["value"] is None
        recognized=next(r["progress"] for r in changed if r["metric_code"]=="recognized")
        assert recognized["value"] is None and "tax_basis_unresolved" in recognized["missing_reasons"]
        checks.append("refresh_preserves_card_missing_coverage_and_actual_unknown_tax")
        await conn.execute("RESET ROLE")
        assert await conn.fetchval("SELECT count(*) FROM crm.customer_actual WHERE workspace_id=$1",a["ws"])==3
        await conn.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'target.read','deny')",a["ws"],a["owner"])
        await conn.execute(f'SET ROLE "{api}"')
        active=actors[0]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://synthetic") as client:
            revoked_create=await client.post("/api/v1/review/objectives",json=payload,headers={"Idempotency-Key":key})
            revoked_update=await client.patch("/api/v1/review/objectives/"+row["id"],json=patch | {"title":"HTTP updated"},headers={"Idempotency-Key":update_key})
            assert revoked_create.status_code==403 and revoked_update.status_code==404,(revoked_create.text,revoked_update.text)
        checks.append("cached_create_and_update_receipts_recheck_current_object_scope_after_revocation")
        print(json.dumps({"status": "passed", "checks": checks, "database": db, "cleanup": "finally_drop_own_database_and_roles",
                          "real_notifications_sent": False, "production_or_LOCAL_accessed": False}, ensure_ascii=False))
    finally:
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        for role in (api, worker):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


async def verify_counts():
    """New V243/count/source-scope proof only; intentionally skips the old suite."""
    from calendar import monthrange
    from sales_backend.services.review_okr import count_progress

    host = os.environ.get("PGHOST", "")
    if not host.startswith("/tmp/sb-review-pg."):
        raise ValueError("Only the explicitly created sb-review-pg temporary Unix socket is allowed")
    config = dict(host=host, port=int(os.environ["PGPORT"]), user=os.environ.get("PGUSER", "postgres"),
                  password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid4().hex[:12]
    db, api = "sb_okr_count_" + suffix, "sb_okr_count_api_" + suffix
    conn, checks = None, []
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        conn = await asyncpg.connect(database=db, **config)
        await migrate_through(conn, 242)
        await conn.execute(f'CREATE ROLE "{api}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grant_api(conn, api, stage="V242")
        await conn.execute("SELECT security.reconcile_runtime_grants()")
        for typename in ("json", "jsonb"):
            await conn.set_type_codec(typename, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        await conn.execute("SET row_security=on")
        ws, owner, member, team, next_team, empty_team = [uuid4() for _ in range(6)]
        await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic count')", ws, str(ws))
        await conn.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        for uid, role in ((owner, "operations"), (member, "sales")):
            await conn.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)", uid, ws, str(uid))
            await conn.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)", ws, uid, role, "workspace" if role == "operations" else "self")
        for tid in (team, next_team, empty_team):
            await conn.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)", tid, ws, str(tid))
        for uid, tid in ((owner, team), (member, next_team)):
            await conn.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", ws, uid, tid)
        await conn.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)", ws, owner)
        await conn.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'work_report.read','allow','workspace')", ws, owner)
        actor = ActorContext(workspace_id=str(ws), user_id=str(owner), role="operations", data_scope="workspace")
        repo = ReviewOkrRepository()
        today = await conn.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        start, end = today.replace(day=1), today.replace(day=monthrange(today.year, today.month)[1])
        async with conn.transaction():
            await set_request_context(conn, actor)
            target = (await conn.fetchval("SELECT security.save_target_batch('team',NULL,$1,'month',$2,$3,$4::jsonb,'Synthetic','sales')", team, start, end, [{"kind": "opportunity_count", "amount": "9", "version_no": None}]))["items"][0]
            await conn.fetchval("SELECT security.save_review_metric('opportunity_count',$1::jsonb,1)", {"active": False})
            before = dict(await conn.fetchrow("SELECT * FROM crm.review_metric WHERE workspace_id=$1 AND code='opportunity_count'", ws))
            target_before = dict(await conn.fetchrow("SELECT * FROM crm.sales_target WHERE id=$1", UUID(target["id"])))
        await apply(conn, item("V243", ROOT / "database/migrations/V243__review_count_metric_definitions.sql"), ROOT / "database")
        after = dict(await conn.fetchrow("SELECT * FROM crm.review_metric WHERE workspace_id=$1 AND code='opportunity_count'", ws))
        assert after["definition_version"] == 2 and after["name"] == "当前负责的本期新增商机数"
        assert all(after[key] == before[key] for key in ("active", "version_no", "updated_at", "updated_by_user_ref_id"))
        assert dict(await conn.fetchrow("SELECT * FROM crm.sales_target WHERE id=$1", UUID(target["id"]))) == target_before
        assert await conn.fetchval("SELECT count(*) FROM crm.review_metric_history WHERE workspace_id=$1 AND code='opportunity_count' AND version_no=2", ws) == 2
        future_ws = uuid4()
        await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'New count tenant')", future_ws, str(future_ws))
        assert await conn.fetchval("SELECT count(*) FROM crm.review_metric WHERE workspace_id=$1 AND code IN ('opportunity_count','visit_count') AND definition_version=2", future_ws) == 2
        checks.append("V243_preserves_target_active_maintenance_version_appends_definition_history_and_future_seed")
        async with conn.transaction():
            await set_request_context(conn, actor)
            enabled = await conn.fetchval("SELECT security.save_review_metric('opportunity_count',$1::jsonb,2)", {"active": True})
            assert enabled["version_no"] == 3 and enabled["definition_version"] == 2
            customer = uuid4()
            await conn.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,'Count customer','count customer',$3,$3,$4)", customer, ws, owner, team)
            form = await conn.fetchval("SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1")
            opportunity_ids = {}
            for label, meta in (
                ("lost", {"import_type": "crm_history", "source_fields": {"商机创建时间": start.isoformat()}}),
                ("invalid", {"import_type": "crm_history", "source_fields": {"商机创建时间": "invalid"}, "raw_fields": {"商机创建时间": start.isoformat()}}),
                ("unproven", {"import_type": "other_import"}),
                ("future", {"import_type": "crm_history", "source_fields": {"商机创建时间": (today + timedelta(days=1)).isoformat()}}),
                ("native", {}),
            ):
                oid = opportunity_ids[label] = uuid4()
                await conn.execute("SELECT set_config('app.feishu_historical_import',$1,true)", "on" if meta.get("import_type") == "crm_history" else "off")
                await conn.execute("SELECT set_config('app.native_opportunity_create',$1,true)", str(oid) if label == "native" else "")
                await conn.execute("INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,created_by_user_ref_id,source_code,status,stage_code,import_meta) VALUES($1,$2,$3,$4,$5,$6,$5,'manual',$7,'proposal',$8)", oid, ws, customer, label, owner, team, "lost" if label == "lost" else "open", meta)
            visit_ids = {}
            for label, status, when in (("confirmed", "confirmed", start.isoformat() + "T00:00:00+08:00"), ("archived", "archived", start.isoformat() + "T00:00:00+08:00"), ("pending", "pending_confirm", start.isoformat() + "T00:00:00+08:00"), ("recording", "recording", start.isoformat() + "T00:00:00+08:00"), ("before", "confirmed", (start - timedelta(days=1)).isoformat() + "T23:59:59+08:00"), ("future", "confirmed", (today + timedelta(days=1)).isoformat() + "T00:00:00+08:00")):
                vid = visit_ids[label] = uuid4()
                await conn.execute("INSERT INTO activity.visit(id,workspace_id,customer_id,recorder_user_ref_id,recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,follow_up_record,next_action) VALUES($1,$2,$3,$4,$5,$4,$6,$7,$8,'Synthetic count','Synthetic')", vid, ws, customer, owner, team, form, status, datetime.fromisoformat(when))
            # Customer-only formal visit remains valid; links and participants never multiply it.
            for oid in (opportunity_ids["native"], opportunity_ids["lost"]):
                await conn.execute("INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id) VALUES($1,$2,$3)", ws, visit_ids["confirmed"], oid)
            for uid in (owner, member):
                await conn.execute("INSERT INTO activity.visit_participant(workspace_id,visit_id,user_ref_id) VALUES($1,$2,$3)", ws, visit_ids["confirmed"], uid)
            objectives, results = {}, {}
            for label, tid in (("team", team), ("next", next_team), ("empty", empty_team)):
                o = await repo.save(conn, "objective", None, dict(title=label, scope_type="team", user_id=None, team_id=str(tid), department_code="sales", period_type="month", period_start=start.isoformat(), period_end=end.isoformat(), owner_user_id=str(owner), collaborator_user_ids=[], parent_id=None))
                objectives[label] = o
                for code in ("opportunity_count", "visit_count", "collection"):
                    results[label, code] = await repo.save(conn, "key_result", None, dict(objective_id=o["id"], title=code, metric_code=code, target_id=None, owner_user_id=str(owner), collaborator_user_ids=[], parent_id=None))
            await conn.execute("INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,source_ref,request_id,confirmed_by_user_ref_id,tax_basis) VALUES($1,$2,$3,'collection',7.89,$4,'Synthetic count actual',$5,$6,'not_applicable')", ws, customer, opportunity_ids["native"], start, uuid4(), owner)
        await conn.execute(f'SET ROLE "{api}"')

        class CountDatabase:
            @asynccontextmanager
            async def transaction(self, identity, **options):
                async with conn.transaction(readonly=options.get("readonly", False), isolation=options.get("isolation", "read_committed")):
                    await set_request_context(conn, identity)
                    yield conn

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)
        app.dependency_overrides[get_database] = lambda: CountDatabase()
        async def read(label, code):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
                response = await client.get("/api/v1/review/key-results/" + results[label, code]["id"])
                assert response.status_code == 200, response.text
                value = response.json()
                assert value["linkable"] and value["objective_status"] == "active"
                return value["progress"]
        opportunity = await read("team", "opportunity_count")
        assert opportunity["known_subtotal"] == 2 and opportunity["value"] is None and opportunity["unknown_date_count"] == 2, opportunity
        visit = await read("team", "visit_count")
        assert visit["value"] == 2 and len(visit["source_refs"]) == 2, visit
        assert (await read("empty", "visit_count"))["value"] == 0
        assert Decimal((await read("team", "collection"))["value"]) == Decimal("7.89")
        checks.append("restricted_API_HTTP_current_owner_lost_native_import_unknown_formal_visit_dedup_as_of_and_empty")
        await conn.execute("RESET ROLE")
        async with conn.transaction():
            await set_request_context(conn, actor)
            await conn.execute("UPDATE crm.opportunity SET owner_user_ref_id=$2,owner_team_id=$3 WHERE id=$1", opportunity_ids["native"], member, next_team)
        # The original typed insertion remains attributed to the creator.
        assert await conn.fetchval("SELECT (meeting_after->>'owner_id')::uuid=$2 FROM crm.opportunity_state_event WHERE opportunity_id=$1 AND mutation_kind='insert'", opportunity_ids["native"], owner)
        await conn.execute(f'SET ROLE "{api}"')
        assert (await read("team", "opportunity_count"))["known_subtotal"] == 1
        assert (await read("next", "opportunity_count"))["value"] == 1
        checks.append("current_owner_transfer_moves_count_without_rewriting_creation_event")
        await conn.execute("RESET ROLE")
        async with conn.transaction():
            await set_request_context(conn, actor)
            await conn.execute("UPDATE activity.visit SET recorder_team_id=NULL WHERE id=$1", visit_ids["confirmed"])
        await conn.execute(f'SET ROLE "{api}"')
        unknown_team = await read("team", "visit_count")
        assert unknown_team["value"] is None and unknown_team["known_subtotal"] == 1 and unknown_team["unknown_attribution_count"] == 1, unknown_team
        await conn.execute("RESET ROLE")
        permission_role = await conn.fetchval("SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='operations'", ws)
        for permission in ("actual.read", "visit.read"):
            await conn.execute("INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code,team_ids) VALUES($1,$2,$3,'self','{}') ON CONFLICT(role_id,permission_code) DO UPDATE SET scope_code='self',team_ids='{}'", ws, permission_role, permission)
        await conn.execute(f'SET ROLE "{api}"')
        assert (await read("team", "collection"))["value"] is None
        partial_visit = await read("team", "visit_count")
        assert partial_visit["value"] is None and partial_visit["authorization_coverage"] == "authorized_subset"
        await conn.execute("RESET ROLE")
        await conn.execute("UPDATE config.account_permission_override SET effect='deny',scope_code=NULL WHERE workspace_id=$1 AND user_ref_id=$2 AND permission_code='work_report.read'", ws, owner)
        await conn.execute(f'SET ROLE "{api}"')
        no_proof = await read("next", "opportunity_count")
        assert no_proof["value"] is None and "creation_provenance_permission_denied" in no_proof["missing_reasons"], no_proof
        await conn.execute("RESET ROLE")
        for permission in ("actual.read", "visit.read", "opportunity.read"):
            await conn.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,$3,'deny')", ws, owner, permission)
        await conn.execute(f'SET ROLE "{api}"')
        for code in ("collection", "opportunity_count", "visit_count"):
            denied = await read("team", code)
            assert denied["value"] is None and denied["coverage"] == "unavailable", denied
        checks.append("missing_recorded_team_and_partial_or_revoked_source_scope_never_complete_zero_or_actual_total")
        # Physical schema currently rejects NULL interaction_at. Exercise null
        # source coverage directly without altering source schema to fabricate it.
        missing = count_progress({"items": [{"id": "unknown-date", "version_no": 1, "status": "confirmed", "user_id": str(owner), "team_id": str(team), "interaction_at": None}], "scope_complete": True}, metric={"code": "visit_count", "definition_version": 2, "name": "本人记录的正式跟进数", "definition": "Data", "unit": "次"}, scope={"scope_type": "team", "team_id": str(team)}, period={"start": start.isoformat(), "end": end.isoformat()}, as_of=datetime.now().astimezone())
        assert missing["value"] is None and missing["unknown_date_count"] == 1
        print(json.dumps({"status": "passed", "checks": checks, "database": db, "mode": "counts_only", "cleanup": "finally_drop_own_database_and_role", "production_or_LOCAL_accessed": False}, ensure_ascii=False))
    finally:
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{api}"')
        await admin.close()


async def verify_archive_boundaries(output):
    """Only archived O/KR write boundaries; retain original migration bytes."""
    import hashlib
    import io
    import traceback
    from contextlib import redirect_stdout

    import sales_backend.api.review_okr as imported_api
    import sales_backend.repositories.review_okr as imported_repository
    import sales_backend.services.review_okr as imported_service

    result = {"status": "running", "mode": "archive_boundaries_only", "checks": [], "http_calls": []}
    origins = {key: str(Path(module.__file__).resolve()) for key, module in (
        ("api", imported_api), ("repository", imported_repository), ("service", imported_service))}
    assert all(Path(path).is_relative_to(ROOT / "backend/src") for path in origins.values()), origins
    assert ROOT / "backend/src" in [Path(p).resolve() for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    result["runtime_import"] = {"origins": origins, "PYTHONPATH": os.environ["PYTHONPATH"], "cwd": str(Path.cwd())}
    migration = ROOT / "database/migrations/V241__review_okr.sql"
    result["sql_sha256"] = hashlib.sha256(migration.read_bytes()).hexdigest()
    result["source_sha256"] = {key: hashlib.sha256(Path(path).read_bytes()).hexdigest() for key, path in origins.items()}
    host = os.environ.get("PGHOST", "")
    assert host.startswith("/tmp/sb-review-pg.")
    config = dict(host=host, port=int(os.environ["PGPORT"]), user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    suffix = uuid4().hex[:12]
    name, role = "sb_okr_archive_" + suffix, "sb_okr_archive_api_" + suffix
    conn = None
    try:
        result["stage"] = "migrate V241 and seed isolated API fixture"
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(conn, 241)
        await conn.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grant_api(conn, role, stage="V241")
        await conn.execute("SELECT security.reconcile_runtime_grants()")
        for typ in ("json", "jsonb"):
            await conn.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        await conn.execute("SET row_security=on")
        ws, user = uuid4(), uuid4()
        await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic archive boundary')", ws, str(ws))
        await conn.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        await conn.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)", user, ws, "archive" + user.hex[:8])
        await conn.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'operations','workspace')", ws, user)
        actor = ActorContext(workspace_id=str(ws), user_id=str(user), role="operations", data_scope="workspace")

        class DB:
            @asynccontextmanager
            async def transaction(self, current_actor, readonly=False, isolation="read_committed"):
                async with conn.transaction(readonly=readonly, isolation=isolation):
                    await conn.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(conn, current_actor)
                    assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                    yield conn

        db, repo = DB(), ReviewOkrRepository()
        async with db.transaction(actor):
            await conn.fetchval("SELECT security.save_review_metric('custom_archive_count',$1::jsonb,NULL)",
                dict(name="Synthetic archive count", unit="次", decimal_places=0, definition="Synthetic confirmed evidence", active=True))
            body = dict(title="Main active O", scope_type="person", user_id=str(user), team_id=None, department_code="sales",
                period_type="quarter", period_start="2026-10-01", period_end="2026-12-31", owner_user_id=str(user), collaborator_user_ids=[], parent_id=None)
            main = await repo.save(conn, "objective", None, body)
            parent_o = await repo.save(conn, "objective", None, body | {"title": "Parent O status probe"})
            kr_body = dict(objective_id=main["id"], title="Parent KR status probe", metric_code="custom_archive_count", target_id=None,
                owner_user_id=str(user), collaborator_user_ids=[], parent_id=None)
            parent_kr = await repo.save(conn, "key_result", None, kr_body)
            historical_child = await repo.save(conn, "key_result", None, kr_body | {"title": "Historical child", "parent_id": parent_kr["id"]})
            live_parent = await repo.save(conn, "key_result", None, kr_body | {"objective_id": parent_o["id"], "title": "Live KR under O status probe"})
            live_child = await repo.save(conn, "key_result", None, kr_body | {"title": "Existing O-status child", "parent_id": live_parent["id"]})
            free_child = await repo.save(conn, "key_result", None, kr_body | {"title": "Unlinked existing KR"})
            assert await conn.fetchval("SELECT security.review_key_result_readable($1::uuid)", parent_kr["id"])
            assert not await conn.fetchval("SELECT has_table_privilege(current_user,'crm.review_key_result','INSERT,UPDATE,DELETE')")

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_database] = lambda: db
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=actor)

        @app.exception_handler(OperationsError)
        async def operation_error(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)

        async def frozen_state():
            async with db.transaction(actor, readonly=True):
                state = {
                    "key_results": [dict(r) for r in await conn.fetch("SELECT id,status,parent_id,version_no FROM crm.review_key_result ORDER BY id")],
                    "progress": [dict(r) for r in await conn.fetch("SELECT * FROM crm.review_key_result_progress ORDER BY id")],
                }
            # Audit rows have no application SELECT grant. The isolated admin
            # verifies immutability without extending that production ACL.
            state["admin_audit_oracle"] = [dict(r) for r in await conn.fetch("SELECT * FROM crm.review_okr_history ORDER BY entity_id,version_no")]
            return state

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            async def call(method, path, body=None, expected=200, key=None):
                kwargs = {"json": body, "headers": {"Idempotency-Key": str(key or uuid4())}} if body is not None else {}
                response = await client.request(method, "/api/v1/review" + path, **kwargs)
                result["http_calls"].append({"method": method, "path": path, "status": response.status_code})
                assert response.status_code == expected, (path, response.status_code, response.text)
                return response.json()

            result["stage"] = "active confirmation and legal KR archive"
            progress_body = dict(version_no=1, value="3", evidence="Synthetic confirmed record", confirmed=True)
            progress_key = uuid4()
            confirmed = await call("POST", "/key-results/" + parent_kr["id"] + "/progress", progress_body, key=progress_key)
            assert confirmed["progress"]["value"] == "3.00" and confirmed["key_result"]["version_no"] == 2
            async with db.transaction(actor):
                mutable = {k: historical_child[k] for k in ("title", "owner_user_id", "collaborator_user_ids", "parent_id", "status")}
                archived_child = await repo.save(conn, "key_result", UUID(historical_child["id"]), mutable | {"status": "archived"}, 1)
                mutable = {k: parent_kr[k] for k in ("title", "owner_user_id", "collaborator_user_ids", "parent_id", "status")}
                archived_parent = await repo.save(conn, "key_result", UUID(parent_kr["id"]), mutable | {"status": "archived"}, 2)
                assert archived_child["parent_id"] == archived_parent["id"]
            before = await frozen_state()
            await call("POST", "/key-results/" + parent_kr["id"] + "/progress", progress_body | {"version_no": 3, "value": "4"}, expected=404)
            assert await call("POST", "/key-results/" + parent_kr["id"] + "/progress", progress_body, key=progress_key) == confirmed
            assert await frozen_state() == before
            result["checks"].append("active confirmation succeeds; archived KR rejects new confirmation; authorized original receipt replay adds no progress/history")

            result["stage"] = "archived parent KR rejects new parent edges"
            await call("POST", "/key-results", kr_body | {"title": "Must reject new archived-parent edge", "parent_id": parent_kr["id"]}, expected=404)
            update = {k: free_child[k] for k in ("title", "owner_user_id", "collaborator_user_ids", "parent_id", "status", "version_no")}
            await call("PATCH", "/key-results/" + free_child["id"], update | {"parent_id": parent_kr["id"]}, expected=404)
            assert await frozen_state() == before
            result["checks"].append("archived parent KR rejects both create and update parent edges without changing existing history")

            # Public O archive already rejects active KRs. This isolated admin
            # fixture deliberately models an inconsistent historical O state,
            # so the O.status guard is tested independently of KR.status.
            await conn.execute("UPDATE crm.review_objective SET status='archived' WHERE id=$1::uuid", parent_o["id"])
            before = await frozen_state()
            result["stage"] = "archived O independently rejects confirmation and parent edges"
            await call("POST", "/key-results/" + live_parent["id"] + "/progress", progress_body, expected=404)
            await call("POST", "/key-results", kr_body | {"title": "Must reject archived-parent-O edge", "parent_id": live_parent["id"]}, expected=404)
            await call("PATCH", "/key-results/" + free_child["id"], update | {"parent_id": live_parent["id"]}, expected=404)
            assert await frozen_state() == before
            result["checks"].append("active KR under isolated archived O rejects confirmation and both new parent-edge paths; no writes")

            historical = await call("GET", "/key-results/" + parent_kr["id"])
            child_history = await call("GET", "/key-results/" + historical_child["id"])
            o_parent = await call("GET", "/key-results/" + live_parent["id"])
            o_child = await call("GET", "/key-results/" + live_child["id"])
            archived_o = await call("GET", "/objectives/" + parent_o["id"])
            assert historical["status"] == "archived" and historical["progress"]["value"] == "3.00"
            assert child_history["parent_id"] == parent_kr["id"]
            assert o_parent["status"] == "active" and o_parent["objective_status"] == "archived" and not o_parent["linkable"]
            assert o_child["parent_id"] == live_parent["id"] and archived_o["status"] == "archived"
            result["checks"].append("archived O/KR and existing parent links/progress remain readable through API and RLS")
            result["samples"] = {"active_confirmation": confirmed, "archived_kr": historical,
                "archived_child": child_history, "active_kr_archived_o": o_parent, "existing_parent_o_link": o_child,
                "archived_o_fixture": archived_o, "persisted_state_after_rejections": before}
        result["migration_unchanged"] = result["sql_sha256"] == hashlib.sha256(migration.read_bytes()).hexdigest()
        assert result["migration_unchanged"]
        result["status"], result["stage"] = "passed", "complete"
        result["boundary"] = "New archive cases only, real limited API role/PostgreSQL/ASGI; isolated admin seeds O-only inconsistent state and compares immutable audit rows without granting their SELECT to API; no business SQL change, no old 16 groups, production or existing LOCAL."
    except Exception as exc:
        result["status"] = "failed"
        result["failure"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        raise
    finally:
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
        result["own_database_and_role_cleaned"] = True
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
        print(json.dumps({key: result[key] for key in ("status", "checks", "http_calls", "sql_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    if "--archive-boundaries-only" in sys.argv:
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--archive-boundaries-only", action="store_true")
        parser.add_argument("--output", required=True)
        args = parser.parse_args()
        asyncio.run(verify_archive_boundaries(args.output))
    else:
        asyncio.run(verify_counts() if "--counts-only" in sys.argv else verify())
