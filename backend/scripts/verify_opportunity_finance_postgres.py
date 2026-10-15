"""Opt-in local, disposable PG integration proof; never reads a saved DSN.

PGHOST must be a local socket. Synthetic random DB/NOLOGIN runtime role are
removed in finally. Uses V195 + V197..V201, intentionally without V196.
"""

# ruff: noqa: E402
import asyncio
import json
import os
import sys
from datetime import date
from decimal import Decimal
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "database/tests"))
sys.path.insert(0, str(ROOT / "database/scripts"))
from migrate import apply, item
from verify_feishu_dependency_sync_postgres import fixture
from verify_runtime_grants_postgres import migrate_through

from sales_backend.api.dependencies import RequestIdentity, get_database, get_identity
from sales_backend.api.management_dependencies import get_management_identity
from sales_backend.api.opportunity_finance import router
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, RoleCode
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.domain.opportunity_finance import financial_overview
from sales_backend.repositories.opportunity_finance import OpportunityFinanceRepository
from sales_backend.repositories.customer_assets import CustomerAssetRepository
from sales_backend.repositories.opportunity_mutations import forecasts, write_forecasts
from sales_backend.domain.opportunity_finance import financial_projection
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.operations import OperationsError
from sales_backend.services.opportunity_finance import confirm_source_idempotently


async def grant_api(conn, role):
    rules = json.loads((ROOT / "tools/installation/runtime_permissions.json").read_text())["services"]["api"]
    for schema in rules["schemas"]:
        await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
    for table, privileges in rules["tables"].items():
        await conn.execute(f'GRANT {",".join(privileges)} ON TABLE {table} TO "{role}"')
    for table, grants in rules["columns"].items():
        for privilege, columns in grants.items():
            await conn.execute(f'GRANT {privilege}({",".join(columns)}) ON TABLE {table} TO "{role}"')
    for signature in rules["functions"]:
        if await conn.fetchval("SELECT to_regprocedure($1) IS NOT NULL", signature):
            await conn.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"')


async def verify():
    host = os.environ.get("PGHOST", "")
    if not host.startswith("/"):
        raise ValueError("Explicit local PGHOST socket required")
    config = dict(
        host=host,
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ.get("PGUSER", "postgres"),
        password="",
        passfile="/dev/null",
        ssl=False,
    )
    admin = await asyncpg.connect(database="postgres", **config)
    db, role = "sb_finance_api_" + uuid4().hex[:12], "sb_finance_api_" + uuid4().hex[:12]
    conn, checks = None, []
    try:
        await admin.execute(f'''CREATE DATABASE "{db}" TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' ''')
        conn = await asyncpg.connect(database=db, **config)
        await migrate_through(conn, 195)
        for version in (197, 198, 199):
            path = next((ROOT / "database/migrations").glob(f"V{version}__*.sql"))
            await apply(conn, item(f"V{version}", path), ROOT / "database")
        own, foreign = await fixture(conn, "FINANCE_A"), await fixture(conn, "FINANCE_B")
        await conn.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
            "VALUES($1,$2,'operations','workspace')",
            own["ws"],
            own["user"],
        )
        await conn.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", own["ws"])
        await conn.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        path = next((ROOT / "database/migrations").glob("V200__*.sql"))
        await apply(conn, item("V200", path), ROOT / "database")
        path = next((ROOT / "database/migrations").glob("V201__*.sql"))
        await apply(conn, item("V201", path), ROOT / "database")
        await grant_api(conn, role)
        for type_name in ("json", "jsonb"):
            await conn.set_type_codec(
                type_name, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
            )
        actor = ActorContext(
            workspace_id=str(own["ws"]), user_id=str(own["user"]), role="operations", data_scope="workspace"
        )
        await conn.execute("SET row_security=on")
        await conn.execute(f'SET ROLE "{role}"')
        assert not await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        repository = OpportunityFinanceRepository()

        @asynccontextmanager
        async def transaction(who=actor):
            async with conn.transaction():
                await set_request_context(conn, who)
                yield conn

        async with transaction():
            overview = await repository.read(conn, own["opportunity"])
            period = next(p for p in overview["periods"] if p["history"])
            assert overview["can_confirm"] and period["entry_amount"] == "10"
            assert period["status"] == "needs_confirmation" and period["amount"] is None
            assert "finance=" in overview["review_path"]
        checks.append("restricted_runtime_single_snapshot_conflict_and_deep_link")

        await conn.execute("RESET ROLE")
        raw = await conn.fetchval("SELECT ops.feishu_source($1,$2,$3)", own["sync"], "opportunity", own["opportunity"])
        worker_period = next(p for p in financial_overview(raw)["periods"] if p["history"])
        assert worker_period["source_fingerprint"] == period["source_fingerprint"]
        await conn.execute(f'SET ROLE "{role}"')
        checks.append("api_and_worker_share_exact_fingerprint")
        body = dict(
            year=period["year"],
            quarter=period["quarter"],
            kind=period["kind"],
            source="historical",
            selected_history_id=str(own["period_actual_snapshot"]),
            reason="Synthetic verified source",
            expected_fingerprint=period["source_fingerprint"],
            expected_version=0,
        )
        key = uuid4()

        async def mutate(data=body, request_key=key):
            async with transaction():
                return await confirm_source_idempotently(conn, actor, own["opportunity"], data, request_key)

        result = await mutate()
        assert result["totals"]["recognized"]["amount"] == "10"
        assert result["periods"][0]["decision"]["reason"] == body["reason"]
        replay = await mutate()
        assert replay == result
        async with transaction():
            assert await conn.fetchval("SELECT count(*) FROM crm.opportunity_actual_basis") == 1
        checks.append("confirm_exact_history_id_with_actor_reason_and_idempotent_replay")
        try:
            await mutate(body | {"reason": "changed same key"})
        except IdempotencyConflict:
            pass
        else:
            raise AssertionError("Idempotency conflict not rejected")
        checks.append("changed_intent_cannot_reuse_idempotency_key")

        async with transaction():
            await conn.execute(
                """UPDATE crm.customer_actual SET voided_at=clock_timestamp(),
                  voided_by_user_ref_id=$2,void_reason='Synthetic void'
              WHERE id=$1""",
                own["actual"],
                own["user"],
            )
        async with transaction():
            current = await repository.read(conn, own["opportunity"])
            assert current["periods"][0]["status"] == "stale_confirmation"
            assert current["totals"]["recognized"]["amount"] is None
        try:
            await mutate(body | {"expected_version": 1}, uuid4())
        except VersionConflict:
            pass
        else:
            raise AssertionError("Stale evidence accepted")
        checks.append("void_invalidates_confirmation_and_stale_post_conflicts")

        body = body | {"expected_version": 1, "expected_fingerprint": current["periods"][0]["source_fingerprint"]}
        result = await mutate(body, uuid4())
        assert result["totals"]["recognized"]["amount"] == "10"
        assert result["periods"][0]["decision"]["version_no"] == 2
        checks.append("explicit_reconfirmation_uses_current_fingerprint_and_version")
        for extra, error in (
            ({"selected_history_id": str(foreign["period_actual_snapshot"]), "expected_version": 2}, ValueError),
            ({"expected_version": 1}, VersionConflict),
        ):
            try:
                await mutate(body | extra, uuid4())
            except error:
                pass
            else:
                raise AssertionError("Foreign evidence or concurrent decision version accepted")
        checks.append("historical_evidence_must_match_bucket_and_concurrent_confirmation_conflicts")
        for target in (foreign["opportunity"], uuid4()):
            try:
                async with transaction():
                    await repository.read(conn, target)
            except (PermissionError, LookupError):
                pass
            else:
                raise AssertionError("Foreign/unknown opportunity exposed")
        checks.append("foreign_tenant_and_missing_object_read_denied")
        try:
            async with transaction(actor.model_copy(update={"role": RoleCode.MANAGER})):
                await confirm_source_idempotently(conn, actor, own["opportunity"], body, key)
        except PermissionError:
            pass
        else:
            raise AssertionError("Non-management actor could confirm")
        checks.append("manager_with_actual_create_cannot_confirm_or_replay_management_receipt")

        class TestDatabase:
            @asynccontextmanager
            async def transaction(self, who, **kwargs):
                async with transaction(who) as c:
                    yield c

        identity = RequestIdentity(actor, "local", None, "password", False)
        app = FastAPI()
        app.include_router(router)

        @app.exception_handler(OperationsError)
        async def operations_error(_request, exc):
            return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

        app.dependency_overrides[get_identity] = lambda: identity
        app.dependency_overrides[get_management_identity] = lambda: identity
        app.dependency_overrides[get_database] = lambda: TestDatabase()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid") as client:
            base = f"/api/v1/console/opportunities/{own['opportunity']}"
            response = await client.get(base + "/financial-overview")
            assert response.status_code == 200 and response.json()["can_confirm"]
            assert (await client.post(base + "/financial-sources/confirm", json=body)).status_code == 422
            assert (
                await client.post(
                    base + "/financial-sources/confirm",
                    json=body | {"amount": 999},
                    headers={"Idempotency-Key": str(uuid4())},
                )
            ).status_code == 422
            headers = {"Idempotency-Key": str(uuid4())}
            request_body = body | {"expected_version": 2, "reason": "Synthetic HTTP source review"}
            saved = await client.post(base + "/financial-sources/confirm", json=request_body, headers=headers)
            assert saved.status_code == 200, saved.text
            assert saved.json()["periods"][0]["decision"]["version_no"] == 3
            assert (
                await client.post(base + "/financial-sources/confirm", json=request_body, headers=headers)
            ).json() == saved.json()
            conflicted = await client.post(
                base + "/financial-sources/confirm", json=request_body, headers={"Idempotency-Key": str(uuid4())}
            )
            assert conflicted.status_code == 409, conflicted.text
        checks.append("http_response_contract_and_required_key_forbid_client_money")
        checks.append("http_confirm_replays_receipt_and_reports_stale_version_as_409")

        await conn.execute("RESET ROLE")
        assert (
            await conn.fetchval("SELECT count(*) FROM ops.audit_log WHERE object_type='opportunity_actual_basis'") >= 2
        )
        notices = await conn.fetchval(
            """SELECT count(*) FROM ops.feishu_event WHERE object_id=$1 AND object_kind='opportunity'
          AND (first_formal_create OR NOT historical OR notification_planned)
          AND created_at>=(SELECT min(created_at) FROM crm.opportunity_actual_basis)""",
            own["opportunity"],
        )
        assert notices == 0
        checks.append("persistent_decision_audit_and_no_finance_group_notification")
        await conn.execute(f'SET ROLE "{role}"')
        async with transaction():
            await write_forecasts(conn, actor, own["opportunity"], [dict(year=2026, quarter=4,
                recognized_amount=Decimal("0"), collection_amount=Decimal("0"), collection_confidence="high")])
            await write_forecasts(conn, actor, own["opportunity"], [dict(year=2026, quarter=4,
                recognized_amount=Decimal("1"), collection_amount=Decimal("2"))])
            saved = next(r for r in await forecasts(conn, own["opportunity"]) if r["quarter"] == 4)
            assert saved["collection_confidence"] == "high"
            data = dict(customer_id=own["customer"], opportunity_id=own["opportunity"], kind="recognized",
                amount=Decimal("0.01"), occurred_on=date(2026,1,1), source_ref="synthetic-explicit-tax",
                note="synthetic", request_id=uuid4(), tax_basis="exclusive", confirmed=True)
            assets = CustomerAssetRepository()
            created = await assets.create(conn, actor, data)
            assert not created["replayed"] and (await assets.create(conn, actor, data))["replayed"]
            overview = await repository.read(conn, own["opportunity"])
            q1 = next(p for p in overview["periods"] if p["year"] == 2026 and p["quarter"] == 1)
            assert q1["amount"] == "0.01" and q1["tax_basis"] == "exclusive"
        await conn.execute("RESET ROLE")
        raw = await conn.fetchval("SELECT ops.feishu_source($1,$2,$3)", own["sync"], "opportunity", own["opportunity"])
        assert financial_projection(raw, ["quarter_2026_q1_recognized_exclusive"])["quarter_2026_q1_recognized_exclusive"] == "0.000001"
        row = await conn.fetchval("SELECT ops.feishu_source($1,$2,$3)", own["sync"], "actual", __import__('uuid').UUID(created["id"]))
        assert row["tax_basis"] == "exclusive"
        checks.append("v201_restricted_actual_create_idempotency_and_api_worker_tax_parity")
        checks.append("legacy_forecast_write_preserves_confidence")

        return {
            "passed": len(checks),
            "skipped": 0,
            "checks": checks,
            "runtime_restricted": True,
            "isolated_database_only": True,
        }
    finally:
        if conn:
            await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(verify()), ensure_ascii=False, indent=2))
