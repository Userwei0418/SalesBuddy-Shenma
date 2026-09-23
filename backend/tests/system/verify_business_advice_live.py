"""Real dedicated Agents + original API against a disposable PostgreSQL workspace.

Run through an operator-owned EnvironmentFile launch, never read/print secrets.
Only synthetic business text in this self-created DB reaches the model. No
production service, rollout, database, account or worker is changed. Fail fast.
"""

# ruff: noqa: E402 -- standalone runner first exposes the repository's local packages.

import argparse
import asyncio
import json
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/src"), str(Path(__file__).parent)]
from run_integration_postgres import main

from sales_backend.config import get_settings
from sales_backend.db import Database, _initialize_connection, set_request_context
from sales_backend.domain.advice import AdviceRequest
from sales_backend.main import app
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.advice import AdviceService
from sales_backend.services.operations_accounts import OperationsAccountService
from sales_backend.worker import Worker
from tests.integration.test_business_rankings import opportunity


def report(case, **value):
    print(json.dumps({"case": case, **value}, ensure_ascii=False, default=str), flush=True)


async def run(config, name, role):
    assert name.startswith("salegent_verify_integration_") and re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role)
    base = get_settings()
    publication = json.loads((ROOT / "backend/agent_platform/business_advice/publication.json").read_text())
    for cap in publication:
        assert getattr(base, "agent_fde_" + cap + "_id") == publication[cap]["id"]
        assert getattr(base, "agent_fde_" + cap + "_api_key")
    assert base.senseaudio_api_key and base.agent_fde_base_url
    pool = await asyncpg.create_pool(database=name, **config, min_size=1, max_size=5, init=_initialize_connection)

    class ScopedPool:
        @asynccontextmanager
        async def acquire(self):
            async with pool.acquire() as c:
                await c.execute(f'SET ROLE "{role}"')
                try:
                    yield c
                finally:
                    await c.execute("RESET ROLE")

    db = Database(replace(base, database_url="", agent_fde_pilot_path=""), pool=ScopedPool())
    client = None
    # Diagnostics are limited to schema/reference metadata from this synthetic workspace.
    import sales_backend.services.advice as advice_module

    original_validate = advice_module.validate_advice

    def traced_validate(value, facts):
        try:
            return original_validate(value, facts)
        except ValueError as exc:
            report(
                "contract_rejected",
                subject_kind=facts["subject_kind"],
                error_type=type(exc).__name__,
                fields=list(value) if isinstance(value, dict) else None,
                references=[
                    item.get("evidence_refs") for item in value.get("suggestions", []) if isinstance(item, dict)
                ]
                if isinstance(value, dict) and isinstance(value.get("suggestions"), list)
                else None,
                schema_errors=[{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
                if hasattr(exc, "errors")
                else [],
            )
            raise

    advice_module.validate_advice = traced_validate
    try:
        async with db.connection() as c:
            async with c.transaction():
                row = await c.fetchrow(
                    "SELECT * FROM security.resolve_account_actor('demo-sales-workspace','XS001',NULL)"
                )
                who = IdentityRepository._actor(row).context
                ops = IdentityRepository._actor(
                    await c.fetchrow(
                        "SELECT * FROM security.resolve_account_actor('demo-sales-workspace','OPS001',NULL)"
                    )
                ).context
                await set_request_context(c, ops)
                version = await c.fetchval("SELECT version_no FROM platform.user_ref WHERE id=$1::uuid", who.user_id)
                await OperationsAccountService().reset_password(
                    c, ops, who.user_id, version, "Isolated-Live-Advice-2026"
                )
                await set_request_context(c, who)
                op = await opportunity(c, "XS001", 250000)
                visit = await VisitRepository().create(
                    c,
                    who,
                    customer_id=op["customer_id"],
                    fields={
                        "opportunity_id": op["id"],
                        "interaction_at": datetime.now(UTC).date().isoformat(),
                        "created_date": datetime.now(UTC).date().isoformat(),
                        "contact_name": "林悦",
                        "follow_up_record": (
                            "林悦负责客服业务，团队有12名客服，每天处理约80条咨询。现有知识分散在三个系统。"
                            "演示的5个问题中4个得到正确答案，退款规则仍缺最新资料。"
                            "客户同意先评估两周试点，预算25万元是初步估算，尚未签约。"
                        ),
                        "next_action": (
                            "由销售在下周二前发送两周试点方案及5条验收标准，"
                            "林悦提供40条脱敏咨询样本，之后共同确认试点范围。"
                        ),
                        "_follow_up_quality_score": 85,
                    },
                )
                assert not await c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                # Fixture setup queues unrelated assessment jobs; this isolated run verifies advice only.
                await c.execute(
                    "UPDATE ops.job SET status='cancelled' WHERE job_type<>'business.advice' AND status='queued'"
                )
        bindings = {
            who.workspace_id: {
                cap: {
                    "enabled": True,
                    "execution_mode": "filtered_facts",
                    "agent_id": v["id"],
                    "expected_snapshot_id": v["snapshot_id"],
                }
                for cap, v in publication.items()
            }
        }

        def select(mode):
            caps = {
                cap: (
                    {"enabled": True, "rollout": "production"}
                    if mode == "normal"
                    else {
                        "enabled": mode != "off",
                        "user_ids": [who.user_id],
                        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                        "block_platform_requests": mode == "blocked",
                    }
                )
                for cap in publication
            }
            db.settings = replace(
                base,
                database_url="",
                access_token_secret="isolated-live-advice-signing-secret-not-production",
                agent_platform_bindings_json=json.dumps(bindings),
                agent_fde_pilot_path="",
                agent_fde_pilot_json=json.dumps({who.workspace_id: {"capabilities": caps}}),
            )
            app.state.database = db
            app.state.settings = db.settings

        select("normal")
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid", timeout=65)
        login = await client.post(
            "/api/v1/auth/password/login",
            json={
                "account_code": "XS001",
                "password": "Isolated-Live-Advice-2026",
                "workspace": "demo-sales-workspace",
            },
        )
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        changed = await client.post(
            "/api/v1/auth/password",
            json={"old_password": "Isolated-Live-Advice-2026", "new_password": "Isolated-Live-Changed-2026"},
        )
        assert changed.status_code == 200, changed.text
        report("native_password_login_and_first_change", passed=True, isolated_database=True, rls_bypass=False)
        subjects = {"customer": op["customer_id"], "opportunity": op["id"], "visit": visit["id"]}
        suggestions = []
        for mode, section in [("normal", "overview"), ("blocked", "tasks"), ("off", "visits")]:
            select(mode)
            for kind, subject in subjects.items():
                if mode == "normal" and kind not in NORMAL_KINDS:
                    continue
                if mode == "off" and kind != "visit":
                    continue
                reply = await client.post(
                    "/api/v1/advice", json={"subject_kind": kind, "subject_id": subject, "section": section}
                )
                assert reply.status_code == 200, reply.text
                value = reply.json()
                service = AdviceService(db)
                worker = Worker(db)
                assert await worker.run_once(), "native queue did not claim the advice"
                ready = await client.get("/api/v1/advice/" + value["id"])
                assert ready.status_code == 200, ready.text
                ready = ready.json()
                async with db.transaction(who, readonly=True) as c:
                    row = await c.fetchrow(
                        "SELECT inference_trace FROM insight.business_advice WHERE id=$1::uuid", value["id"]
                    )
                    trace = row["inference_trace"] or {}
                    calls = await c.fetch(
                        "SELECT provider_code,status FROM agent.model_invocation WHERE operation_id=$1::uuid",
                        trace.get("operation_id"),
                    )
                    job = await c.fetchrow(
                        "SELECT j.status,EXISTS(SELECT 1 FROM ops.job_effect e WHERE e.job_id=j.id) AS effect "
                        "FROM ops.job j WHERE j.aggregate_type='business_advice' AND j.aggregate_id=$1::uuid "
                        "ORDER BY j.created_at DESC LIMIT 1",
                        value["id"],
                    )
                report(
                    kind + "_" + mode,
                    status=ready["status"],
                    suggestion_count=len(ready["suggestions"]),
                    trace=trace,
                    calls=[dict(r) for r in calls],
                    job=dict(job) if job else None,
                )
                assert ready["status"] == "succeeded"
                assert job["status"] == "succeeded" and job["effect"]
                expected = "agent_platform" if mode == "normal" else "senseaudio"
                assert trace.get("provider") == expected, (kind, mode, "unexpected route", trace.get("provider"))
                cached = await service.request(
                    who, AdviceRequest(subject_kind=kind, subject_id=subject, section=section)
                )
                assert cached["id"] == value["id"] and cached["status"] == "succeeded"
                if ready["suggestions"]:
                    suggestions.append((kind, ready["suggestions"][0]))
                async with db.transaction(ops, readonly=True) as c:
                    audit = await c.fetchval(
                        "SELECT record FROM security.agent_operation_rows($1,$2) WHERE operation_id=$3::uuid",
                        datetime.now(UTC) - timedelta(hours=1),
                        datetime.now(UTC) + timedelta(minutes=1),
                        trace["operation_id"],
                    )
                    assert audit["advice_id"] == value["id"] and audit["business_status"] == "waiting_human"
                    assert audit["job_effect_recorded"]
        assert suggestions
        # Reuse the final current visit advice: explicit human-confirmed form, idempotency replay, durable task/source.
        kind, s = suggestions[-1]
        body = {
            "decision": "adopted",
            "version_no": s["version_no"],
            "task": {
                "description": "人工确认：请整理试点验收标准，并与林悦核对范围。",
                "target_position": "self",
                "due_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
                "priority_code": "medium",
            },
        }
        url = "/api/v1/advice/suggestions/" + s["id"] + "/decision"
        headers = {"Idempotency-Key": str(uuid4())}
        saved = await client.post(url, json=body, headers=headers)
        assert saved.status_code == 200, saved.text
        replay = await client.post(url, json=body, headers=headers)
        assert replay.json() == saved.json()
        task = saved.json()["task"]
        assert task["source_suggestion_id"] == s["id"]
        report("human_adoption_and_idempotent_task", passed=True, task_id=task["id"], suggestion_id=s["id"])
        async with db.transaction(who, readonly=True) as c:
            assert (
                await c.fetchval("SELECT count(*) FROM workflow.task WHERE source_suggestion_id=$1::uuid", s["id"]) == 1
            )
            assert (
                await c.fetchval("SELECT count(*) FROM workflow.notification WHERE object_id=$1::uuid", task["id"]) == 1
            )
        report("complete", passed=True, production_writes=0)
    finally:
        advice_module.validate_advice = original_validate
        if client:
            await client.aclose()
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--normal-kinds",
        nargs="+",
        choices=["customer", "opportunity", "visit"],
        default=["customer", "opportunity", "visit"],
    )
    NORMAL_KINDS = parser.parse_args().normal_kinds
    asyncio.run(main(serve=run))
