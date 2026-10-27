"""Opt-in M1 regression boundaries using a disposable private PostgreSQL database.

Only synthetic fixtures are written. HTTP handlers execute with the runtime API
grants and real RLS; the trusted identity dependency is supplied by this harness.
"""

import argparse
import asyncio
import hashlib
import io
import json
from contextlib import asynccontextmanager, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from verify_review_targets_postgres import ROOT, digest, grants, migrate_through

from sales_backend.api import operations_targets, review, targets
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.operations import OperationsError

LEGACY_KINDS = {"collection", "recognized", "acv", "opportunity_count", "visit_count", "demo_count"}
SOURCE_FILES = (
    "backend/src/sales_backend/api/review.py",
    "backend/src/sales_backend/contracts/review.py",
    "backend/src/sales_backend/repositories/targets.py",
    "backend/src/sales_backend/services/review.py",
    "backend/src/sales_backend/services/targets.py",
    "backend/src/sales_backend/services/operations.py",
)


async def verify(args):
    socket = Path(args.socket).resolve()
    assert str(socket).startswith(("/private/tmp/sb-review-pg.", "/tmp/sb-review-pg."))  # noqa: S108
    config = dict(host=str(socket), port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    admin = await asyncpg.connect(database="postgres", **config)
    db = "sb_review_m1_boundary_" + uuid4().hex[:12]
    role = "sb_review_m1_api_" + uuid4().hex[:12]
    connection = None
    checks, samples, http_calls = [], {}, []
    result = None
    try:
        await admin.execute(f'CREATE DATABASE "{db}" TEMPLATE template0')
        connection = await asyncpg.connect(database=db, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(connection, 240)
        await connection.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await grants(connection, role)
        for typ in ("json", "jsonb"):
            await connection.set_type_codec(
                typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
            )
        await connection.execute("SET row_security=on")

        ws, operations, person, other_person, team, other_team = [uuid4() for _ in range(6)]
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)",
            ws,
            "m1-boundary-" + uuid4().hex,
        )
        await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
        for team_id, code in ((team, "selected"), (other_team, "other")):
            await connection.execute(
                "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)",
                team_id,
                ws,
                code,
            )
        for uid, role_code, team_id in (
            (operations, "operations", None),
            (person, "sales", team),
            (other_person, "sales", other_team),
        ):
            await connection.execute(
                "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) "
                "VALUES($1,$2,$3,$3,$3)",
                uid,
                ws,
                "synthetic-" + uuid4().hex[:12],
            )
            await connection.execute(
                "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
                "VALUES($1,$2,$3,$4,$5)",
                ws,
                uid,
                role_code,
                "workspace" if team_id is None else "self",
                team_id,
            )
            if team_id:
                await connection.execute(
                    "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
                    "VALUES($1,$2,$3,'sales')",
                    ws,
                    uid,
                    team_id,
                )
        actor = ActorContext(workspace_id=str(ws), user_id=str(operations), role="operations", data_scope="workspace")
        actor_snapshot = actor.model_dump(mode="json")
        anchor = await connection.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date")
        start = date(anchor.year, (anchor.month - 1) // 3 * 3 + 1, 1)

        class DB:
            @asynccontextmanager
            async def transaction(self, current_actor, readonly=False):
                async with connection.transaction(readonly=readonly):
                    await connection.execute(f'SET LOCAL ROLE "{role}"')
                    await set_request_context(connection, current_actor)
                    assert not await connection.fetchval(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
                    )
                    yield connection

        database = DB()
        app = FastAPI()
        for router in (review.router, targets.router, operations_targets.router):
            app.include_router(router)
        app.dependency_overrides[get_database] = lambda: database
        identity = SimpleNamespace(actor=actor)
        app.dependency_overrides[get_identity] = lambda: identity
        app.dependency_overrides[get_management_identity] = lambda: identity

        @app.exception_handler(OperationsError)
        async def operation_error(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=exc.status)

        @app.exception_handler(IdempotencyConflict)
        async def idem_error(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=409)

        params = dict(scope="person", user_id=str(person), period_type="quarter", anchor_date=str(anchor))
        base_body = {**params, "reason": "合成 M1 边界验证"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:

            async def call(method, path, *, body=None, query=None, key=None, status=200):
                response = await client.request(
                    method, path, json=body, params=query, headers={"Idempotency-Key": key} if key else {}
                )
                http_calls.append({"method": method, "path": path, "status": response.status_code})
                assert response.status_code == status, (method, path, response.status_code, response.text)
                return response.json()

            # Precision comes from the metric definition, never its spelling.
            for code, decimals in (("custom_invoice_count", 2), ("custom_amount", 0)):
                await call(
                    "POST",
                    "/api/v1/review/metrics",
                    body={
                        "code": code,
                        "name": code,
                        "unit": "个",
                        "decimal_places": decimals,
                        "definition": "合成指标精度反例",
                    },
                )
            precision_body = {**base_body, "user_id": str(other_person)}
            invoice = await call(
                "POST",
                "/api/v1/review/targets",
                body={
                    **precision_body,
                    "kind": "custom_invoice_count",
                    "amount": "123.45",
                },
            )
            assert invoice["target"]["amount"] == "123.45"
            amount_error = await call(
                "POST",
                "/api/v1/review/targets",
                status=422,
                body={
                    **precision_body,
                    "kind": "custom_amount",
                    "amount": "1.5",
                },
            )
            await call(
                "POST",
                "/api/v1/review/targets",
                body={
                    **precision_body,
                    "kind": "custom_amount",
                    "amount": "2",
                },
            )
            for path in ("/api/v1/review/targets", "/api/v1/targets"):
                await call("POST", path, status=422, body={**precision_body, "kind": "visit_count", "amount": "1.5"})
            visit = await call("POST", "/api/v1/targets", body={**precision_body, "kind": "visit_count", "amount": "3"})
            assert Decimal(str(visit["target"]["amount"])) == Decimal("3")
            samples["precision"] = {
                "custom_invoice_count": invoice,
                "custom_amount_rejection": amount_error,
                "legacy_visit_count": visit,
            }
            checks.append(
                "dictionary decimal_places=2 accepts custom_invoice_count 123.45; "
                "decimal_places=0 rejects custom_amount 1.5; both target APIs retain integer-only visit_count"
            )

            # Older legacy rows are deliberately buried behind 110 new kinds.
            custom_kinds = [f"custom_page_{i:03d}" for i in range(110)]
            target_ids = {}
            async with connection.transaction():
                await set_request_context(connection, actor)
                await connection.executemany(
                    "INSERT INTO crm.review_metric(workspace_id,code,name,unit,decimal_places,definition,"
                    "source_kind,target_kind) "
                    "VALUES($1,$2,$2,'个',0,'合成分页边界','manual',$2)",
                    [(ws, code) for code in custom_kinds],
                )
                for code in ("collection", "recognized", *custom_kinds):
                    target_ids[code] = await connection.fetchval(
                        "INSERT INTO crm.sales_target(workspace_id,target_year,scope_type,user_ref_id,kind,amount,"
                        "updated_by_user_ref_id,period_type,period_start,period_end) "
                        "VALUES($1,$2,'person',$3,$4,100,$5,'quarter',$6,"
                        "($6::date+interval '3 months - 1 day')::date) RETURNING id",
                        ws,
                        start.year,
                        person,
                        code,
                        operations,
                        start,
                    )
                # The legacy request/mixed batch precede all custom requests.
                for code in ("collection", *custom_kinds):
                    await connection.execute(
                        "INSERT INTO crm.sales_target_change_request(workspace_id,target_id,applicant_user_ref_id,"
                        "base_version,previous_amount,proposed_amount) VALUES($1,$2,$3,1,100,200)",
                        ws,
                        target_ids[code],
                        person,
                    )

                def batch_item(code):
                    return {
                        "kind": code,
                        "target_id": str(target_ids[code]),
                        "base_version": 1,
                        "previous_amount": "100.00",
                        "proposed_amount": "200.00",
                    }

                mixed_items = [batch_item("recognized"), batch_item(custom_kinds[0])]
                batch_ids = []
                for items in (mixed_items, *[[batch_item(code)] for code in custom_kinds]):
                    batch_ids.append(
                        await connection.fetchval(
                            "INSERT INTO crm.sales_target_batch_request(workspace_id,scope_type,user_ref_id,"
                            "period_type,"
                            "period_start,period_end,applicant_user_ref_id,reason,items) "
                            "VALUES($1,'person',$2,'quarter',$3,($3::date+interval '3 months - 1 day')::date,$2,"
                            "'合成新旧混合分页',$4::jsonb) RETURNING id",
                            ws,
                            person,
                            start,
                            items,
                        )
                    )
            before_rows = await connection.fetchval(
                "SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM crm.sales_target t "
                "WHERE workspace_id=$1 AND user_ref_id=$2",
                ws,
                person,
            )
            legacy = await call("GET", "/api/v1/targets", query=params)
            assert legacy["total"] == 2 and len(legacy["items"]) == 2
            assert {row["kind"] for row in legacy["items"]} == {"collection", "recognized"}
            assert all(row["version_no"] == 1 and row["kind"] in LEGACY_KINDS for row in legacy["items"])
            # limit=1 proves totals and offsets refer to the filtered collection.
            legacy_second = await call("GET", "/api/v1/targets", query={**params, "limit": 1, "offset": 1})
            assert legacy_second["total"] == 2 and len(legacy_second["items"]) == 1
            assert legacy_second["items"][0]["kind"] in LEGACY_KINDS
            assert legacy["pending_total"] == 1 and len(legacy["pending_requests"]) == 1
            assert legacy["pending_requests"][0]["kind"] == "collection"
            assert legacy["pending_batch_total"] == 1
            for rows in (legacy["pending_batches"], legacy["recent_batches"]):
                assert len(rows) == 1 and rows[0]["id"] == str(batch_ids[0])
                assert rows[0]["items"] == [mixed_items[0]]
            review_pages = [
                await call("GET", "/api/v1/review/targets", query={**params, "limit": 100, "offset": offset})
                for offset in (0, 100)
            ]
            assert all(page["total"] == 112 for page in review_pages)
            assert {row["kind"] for page in review_pages for row in page["items"]} == {
                "collection",
                "recognized",
                *custom_kinds,
            }
            console_pages = [
                await call(
                    "GET", "/api/v1/console/target-batches", query={"status": "pending", "limit": 100, "offset": offset}
                )
                for offset in (0, 100)
            ]
            assert all(page["total"] == 111 for page in console_pages)
            console_rows = [row for page in console_pages for row in page["items"]]
            assert len(console_rows) == 111
            assert next(row for row in console_rows if row["id"] == str(batch_ids[0]))["items"] == mixed_items
            assert any(row["items"][0]["kind"] in custom_kinds for row in console_pages[0]["items"])
            console_requests = await call(
                "GET", "/api/v1/console/target-requests", query={"status": "pending", "limit": 100}
            )
            assert console_requests["total"] == 111 and len(console_requests["items"]) == 100
            assert all(row["kind"] in custom_kinds for row in console_requests["items"])
            after_rows = await connection.fetchval(
                "SELECT jsonb_agg(to_jsonb(t) ORDER BY t.id) FROM crm.sales_target t "
                "WHERE workspace_id=$1 AND user_ref_id=$2",
                ws,
                person,
            )
            assert before_rows == after_rows
            samples["pagination"] = {
                "fixture": {
                    "custom_targets": 110,
                    "legacy_targets": 2,
                    "custom_pending_requests": 110,
                    "legacy_pending_requests": 1,
                    "custom_only_batches": 110,
                    "mixed_batches": 1,
                },
                "legacy_first_page": legacy,
                "legacy_second_item": legacy_second,
                "review_totals": [page["total"] for page in review_pages],
                "review_page_sizes": [len(page["items"]) for page in review_pages],
                "console_totals": [page["total"] for page in console_pages],
                "console_mixed_batch": next(row for row in console_rows if row["id"] == str(batch_ids[0])),
                "target_rows_before_sha256": digest(before_rows),
                "target_rows_after_sha256": digest(after_rows),
            }
            checks.append(
                "legacy GET targets filters before count/limit/offset, preserving only the six old kinds; "
                "Review still paginates all 112 target kinds; all stored rows and versions unchanged"
            )
            checks.append(
                "110 new-kind requests and batches cannot crowd out legacy pending/recent results; "
                "mixed legacy projection retains only old kinds while console approval lists "
                "preserve all 111 complete batches"
            )

            # Prepare genuine successful receipts before narrowing DB grants.
            metric_body = {
                "code": "custom_replay",
                "name": "合成重放",
                "unit": "个",
                "decimal_places": 0,
                "definition": "撤销具体范围后不能重放旧成功响应",
            }
            create_key, target_key, update_key = [str(uuid4()) for _ in range(3)]
            created = await call("POST", "/api/v1/review/metrics", body=metric_body, key=create_key)
            assert await call("POST", "/api/v1/review/metrics", body=metric_body, key=create_key) == created
            target_body = {**base_body, "kind": "custom_replay", "amount": "10"}
            saved = await call("POST", "/api/v1/review/targets", body=target_body, key=target_key)
            assert await call("POST", "/api/v1/review/targets", body=target_body, key=target_key) == saved
            update_body = {"active": False, "version_no": created["version_no"]}
            updated = await call("PATCH", "/api/v1/review/metrics/custom_replay", body=update_body, key=update_key)
            assert (
                await call("PATCH", "/api/v1/review/metrics/custom_replay", body=update_body, key=update_key) == updated
            )
            receipt_before = await connection.fetchval(
                "SELECT count(*) FROM ops.mutation_receipt WHERE workspace_id=$1", ws
            )
            permission_role = await connection.fetchval(
                "SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code='operations'",
                ws,
            )
            # V209 deliberately treats a teams grant as a department aggregate
            # grant. Retain manage on the actor's own subject and submit on the
            # other team; this actually revokes dictionary and selected writes.
            for permission, scope, teams in (("target.manage", "self", []), ("target.submit", "teams", [other_team])):
                await connection.execute(
                    "INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,"
                    "scope_code,team_ids) VALUES($1,$2,$3,$4,$5::uuid[]) ON CONFLICT(role_id,permission_code) "
                    "DO UPDATE SET scope_code=excluded.scope_code,team_ids=excluded.team_ids",
                    ws,
                    permission_role,
                    permission,
                    scope,
                    teams,
                )
            async with database.transaction(actor, readonly=True) as scoped:
                authorization = dict(
                    await scoped.fetchrow(
                        "SELECT security.authorization_has('target.manage') AS generic_manage, "
                        "security.authorization_has('target.submit') AS generic_submit, "
                        "security.target_scope_write('person',$1,NULL,'sales') AS selected_write, "
                        "security.target_scope_write('person',$2,NULL,'sales') AS other_write, "
                        "security.authorization_subject('target.manage','department',NULL,NULL) AS dictionary_write",
                        person,
                        other_person,
                    )
                )
            assert authorization == {
                "generic_manage": True,
                "generic_submit": True,
                "selected_write": False,
                "other_write": True,
                "dictionary_write": False,
            }, authorization
            denied = []
            for method, path, body, key in (
                ("POST", "/api/v1/review/metrics", metric_body, create_key),
                ("POST", "/api/v1/review/targets", target_body, target_key),
                ("PATCH", "/api/v1/review/metrics/custom_replay", update_body, update_key),
            ):
                denied.append(await call(method, path, body=body, key=key, status=403))
            assert actor.model_dump(mode="json") == actor_snapshot
            assert (
                await connection.fetchval("SELECT count(*) FROM ops.mutation_receipt WHERE workspace_id=$1", ws)
                == receipt_before
            )
            samples["replay_after_scope_revoked"] = {
                "same_actor_context": True,
                "authorization": authorization,
                "denials": denied,
                "normal_replay_statuses": [200, 200, 200],
                "revoked_replay_statuses": [403, 403, 403],
                "receipt_count_before": receipt_before,
                "receipt_count_after": receipt_before,
            }
            checks.append(
                "same ActorContext and idempotency keys replay normally as 200, then target/create-metric/"
                "update-metric all deny 403 after exact scope revocation despite retained generic "
                "manage/submit and other-subject rights"
            )

        result = {
            "status": "passed_isolated_postgres_and_asgi",
            "check_count": len(checks),
            "checks": checks,
            "http_calls": http_calls,
            "response_samples": samples,
            "samples_are_synthetic": True,
            "registered_versions": [
                row["version"]
                for row in await connection.fetch("SELECT version FROM ops.schema_migration ORDER BY version")
            ],
            "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES},
            "migration_sha256": hashlib.sha256(
                (ROOT / "database/migrations/V240__review_metric_dictionary.sql").read_bytes()
            ).hexdigest(),
            "verification_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scope": "synthetic disposable database; actual HTTP handlers and restricted API role/RLS; "
            "only trusted identity is overridden",
        }
        assert result["registered_versions"][-1] == "V240"
    finally:
        if connection:
            await connection.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        database_gone = not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", db)
        role_gone = not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=$1)", role)
        await admin.close()
        assert database_gone and role_gone
    result["cleanup"] = {"own_database_dropped": database_gone, "own_role_dropped": role_gone, "cluster_stopped": False}
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
