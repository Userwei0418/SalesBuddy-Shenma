# ruff: noqa: E402, E501, S108, S608 -- Private socket; long fixture SQL uses only fixed table names.
"""D03 V2 proof on an isolated V243 -> V244 DB, not a production release path."""

import argparse
import asyncio
import copy
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
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "database/tests"), str(ROOT / "database/scripts"), str(ROOT / "tools/installation")]
from migrate import apply, item
from runtime_roles import SCHEMAS, fingerprint, verify_service_roles
from upgrade_permissions import reconcile_service_roles
from verify_runtime_grants_postgres import migrate_through

from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.tasks import router
from sales_backend.contracts.task_alignment import ECMASCRIPT_WHITESPACE
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.services.idempotency import IdempotencyConflict


async def rls(c, roles):
    tables = [
        dict(r)
        for r in await c.fetch(
            """SELECT n.nspname||'.'||c.relname AS name,
      c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1""",
            SCHEMAS,
        )
    ]
    rows = [
        dict(r)
        for r in await c.fetch(
            """SELECT schemaname,tablename,policyname,
      permissive,roles::text[],cmd,qual,with_check FROM pg_policies
      WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3""",
            SCHEMAS,
        )
    ]
    for row in rows:
        row["roles"] = sorted(role for role in row["roles"] if role not in roles.values())
    return {"tables": tables, "policies": rows}


async def acl(c, roles):
    result = {}
    for service, role in roles.items():
        result[service] = {
            "tables": [
                dict(r)
                for r in await c.fetch(
                    """SELECT n.nspname||'.'||r.relname AS name,
              ARRAY(SELECT p FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) p
                WHERE has_table_privilege($1,r.oid,p)) AS privileges
              FROM pg_class r JOIN pg_namespace n ON n.oid=r.relnamespace
              WHERE n.nspname=ANY($2::text[]) AND r.relkind IN ('r','p','v','m','f') ORDER BY 1""",
                    role,
                    SCHEMAS,
                )
            ],
            "functions": [
                r["signature"]
                for r in await c.fetch(
                    """SELECT p.oid::regprocedure::text AS signature
              FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
              WHERE n.nspname=ANY($2::text[]) AND has_function_privilege($1,p.oid,'EXECUTE') ORDER BY 1""",
                    role,
                    SCHEMAS,
                )
            ],
        }
        result[service]["tables"] = [row for row in result[service]["tables"] if row["privileges"]]
    return result


async def exact(c, roles, policy, reconcile=False):
    async with c.transaction():
        for role in roles.values():
            await c.execute(f'ALTER ROLE "{role}" LOGIN')
        result = await (
            reconcile_service_roles(c, roles, policy) if reconcile else verify_service_roles(c, roles, policy)
        )
        for role in roles.values():
            await c.execute(f'ALTER ROLE "{role}" NOLOGIN')
        return result


async def verify(args):
    socket = str(Path(args.socket).resolve())
    assert socket.startswith(("/tmp/sb-review-pg.", "/private/tmp/sb-review-pg."))
    config = dict(host=socket, port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    output = Path(args.output)
    assert not output.exists()
    suffix = uuid4().hex[:12]
    name = "sb_d03_" + suffix
    policy = json.loads((ROOT / "backend/tests/fixtures/review_release_contract.frozen.json").read_text())["policies"]["V243"]
    assert policy["schema"] == "V243"
    roles = {service: "sb_d03_" + service + "_" + suffix for service in sorted(policy["services"])}
    role = roles["api"]
    checks = []
    c = None
    admin = await asyncpg.connect(database="postgres", **config)
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 243)
        await c.execute(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC')
        await c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for service, identity in roles.items():
            await c.execute(
                f'CREATE ROLE "{identity}" NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION'
            )
            await c.execute(f'GRANT CONNECT ON DATABASE "{name}" TO "{identity}"')
            for parent in policy["services"][service]["memberships"]:
                await c.execute(f'GRANT "{parent}" TO "{identity}" WITH INHERIT FALSE, SET FALSE')
        await exact(c, roles, policy, reconcile=True)
        old_acl = await acl(c, roles)
        before = dict(await c.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        await c.execute("SET row_security=on")
        actors, workspaces, people, krs = {}, {}, {}, {}
        for label in ("own", "foreign"):
            ws = uuid4()
            workspaces[label] = ws
            await c.execute(
                "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)",
                ws,
                "alignment-" + str(ws),
            )
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", ws)
            # Explicitly narrow this fixture's sales grants; do not assume the
            # built-in role's target/task read scope is limited to self.
            await c.execute(
                """UPDATE config.permission_role_grant g SET scope_code='self',team_ids='{}'
              FROM config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=$1
              AND r.builtin_role_code='sales' AND g.permission_code IN ('target.read','task.read')""",
                ws,
            )
            team = uuid4()
            await c.execute(
                "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,'合成销售团队')",
                team,
                ws,
                "alignment_" + label,
            )
            for code, business_role in [("ops", "operations"), ("sales", "sales"), ("other", "sales")]:
                uid = uuid4()
                account = code + uuid4().hex[:8]
                people[label + "_" + code] = (uid, account)
                await c.execute(
                    "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)",
                    uid,
                    ws,
                    account,
                )
                await c.execute(
                    "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,$3,$4)",
                    ws,
                    uid,
                    business_role,
                    "workspace" if code == "ops" else "self",
                )
                if code != "ops":
                    await c.execute("UPDATE platform.role_binding SET team_id=$2 WHERE user_ref_id=$1", uid, team)
                    await c.execute(
                        "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')",
                        ws,
                        uid,
                        team,
                    )
                actors[label + "_" + code] = ActorContext(
                    workspace_id=str(ws),
                    user_id=str(uid),
                    role=business_role,
                    data_scope="workspace" if code == "ops" else "self",
                    team_ids=[str(team)] if code != "ops" else [],
                )
                if code != "ops":
                    objective = await c.fetchval(
                        """INSERT INTO crm.review_objective(workspace_id,title,scope_type,user_ref_id,department_code,
                      period_type,period_start,period_end,owner_user_id,created_by_user_id,updated_by_user_id)
                      VALUES($1,'合成季度目标','person',$2,'sales','quarter',date_trunc('quarter',current_date)::date,
                      (date_trunc('quarter',current_date)+interval '3 months - 1 day')::date,$2,$2,$2) RETURNING id""",
                        ws,
                        uid,
                    )
                    krs[label + "_" + code] = await c.fetchval(
                        """INSERT INTO crm.review_key_result(workspace_id,objective_id,title,metric_code,
                      owner_user_id,created_by_user_id,updated_by_user_id) VALUES($1,$2,'合成跟进KR','visit_count',$3,$3,$3) RETURNING id""",
                        ws,
                        objective,
                        uid,
                    )
                    if label == "own" and code == "sales":
                        krs["own_extra"] = await c.fetchval(
                            """INSERT INTO crm.review_key_result(workspace_id,objective_id,title,metric_code,
                          owner_user_id,created_by_user_id,updated_by_user_id) VALUES($1,$2,'第二个合成KR','visit_count',$3,$3,$3) RETURNING id""",
                            ws,
                            objective,
                            uid,
                        )
        targets = {}
        for label in ("own_sales", "own_other", "foreign_sales"):
            ws = actors[label].workspace_id
            uid = actors[label].user_id
            for month in (1, 4):
                targets[label + str(month)] = str(
                    await c.fetchval(
                        """INSERT INTO crm.sales_target(
                  workspace_id,target_year,scope_type,user_ref_id,kind,amount,updated_by_user_ref_id,period_type,period_start,period_end)
                  VALUES($1::uuid,2026,'person',$2::uuid,'collection',100,$2::uuid,'quarter',
                    make_date(2026,$3,1),(make_date(2026,$3,1)+interval '3 months - 1 day')::date) RETURNING id""",
                        ws,
                        uid,
                        month,
                    )
                )
        active = actors["own_sales"]

        class DB:
            @asynccontextmanager
            async def transaction(self, actor, readonly=False, **kwargs):
                async with c.transaction(readonly=readonly, **kwargs):
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

        @app.exception_handler(IdempotencyConflict)
        async def conflict(request, exc):
            return JSONResponse({"detail": str(exc)}, status_code=409)

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            http_count = 0

            async def call(method, path, body=None, status=200, key=None):
                nonlocal http_count
                http_count += 1
                response = await client.request(
                    method, path, json=body, headers={"Idempotency-Key": key} if key else {}
                )
                assert response.status_code == status, (method, path, response.status_code, response.text)
                return response.json()

            base = dict(
                description="合成任务核对跟进并汇报",
                assignee_account_code=people["own_sales"][1],
                due_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            )

            def v2(**alignment):
                return {"alignment_contract_version": 2, "alignment": alignment}

            async def create(values=None, status=201, key=None):
                return await call("POST", "/api/v1/tasks", {**base, **(values or {})}, status, key)

            async def patch(task, values, status=200, key=None):
                return await call(
                    "PATCH",
                    "/api/v1/tasks/" + task["id"] + "/alignment",
                    {"version_no": task["version_no"], **values},
                    status,
                    key,
                )

            async def event(task, kind, **extras):
                return await call("POST", "/api/v1/tasks/" + task["id"] + "/events", {"event_type": kind, **extras})

            cap = await call("GET", "/api/v1/tasks/alignment-capabilities")
            assert cap["supported_versions"] == [1]
            legacy = await create({"alignment": {"metric_codes": ["daily_routine"]}})
            unclassified = await create()
            old_row = dict(
                await c.fetchrow("SELECT * FROM workflow.task_alignment WHERE task_id=$1::uuid", legacy["id"])
            )
            sql = ROOT / "database/migrations/V244__task_alignment_object_basis.sql"
            await apply(c, item("V244", sql), ROOT / "database")
            after = dict(await c.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
            assert set(after) - set(before) == {"V244"} and all(after[k] == v for k, v in before.items())
            assert not {"V245", "V246"} & set(after)
            new_row = dict(
                await c.fetchrow("SELECT * FROM workflow.task_alignment WHERE task_id=$1::uuid", legacy["id"])
            )
            assert (
                new_row.pop("contract_version") is None and new_row.pop("other_reason") is None and new_row == old_row
            )
            assert await acl(c, roles) == old_acl
            stage_policy = copy.deepcopy(policy)
            stage_policy["schema"] = "V244"
            stage_policy["migration_fingerprint"] = fingerprint(after)
            stage_rls = await rls(c, roles)
            stage_policy["rls_fingerprint"] = fingerprint(stage_rls)
            verify_result = await exact(c, roles, stage_policy)
            await exact(c, roles, stage_policy, reconcile=True)
            assert await acl(c, roles) == old_acl
            assert (await call("GET", "/api/v1/tasks/alignment-capabilities"))["supported_versions"] == [1, 2]
            checks.append(
                "V243 -> isolated V244 only; original ledger and legacy rows unchanged; real four-role exact ACL unchanged"
            )
            selected = v2(
                target_ids=[targets["own_sales1"], targets["own_sales4"], targets["own_sales1"]],
                key_result_ids=[str(krs["own_sales"]), str(krs["own_extra"])],
                other_reason=None,
                expected_contribution={"metric_code": "collection", "value": "2.00"},
            )
            task = await create(selected)
            a = task["alignment"]
            assert a["contract_version"] == 2 and a["basis"] == "objects" and a["other_reason"] is None
            assert a["metric_codes"] == ["collection", "visit_count"] and a["direct_metric_codes"] == []
            assert a["target_ids"] == sorted([targets["own_sales1"], targets["own_sales4"]])
            assert sorted(t["period"]["start"] for t in a["targets"]) == ["2026-01-01", "2026-04-01"]
            assert len(a["key_results"]) == 2 and all(k["period"]["start"] for k in a["key_results"])
            assert a["expected_contribution"]["status"] == "planned"
            for path in ("/api/v1/tasks", "/api/v1/tasks?tab=all", "/api/v1/tasks/management"):
                page = await call("GET", path)
                assert next(x for x in page["items"] if x["id"] == task["id"])["alignment"] == a
            overview = await call("GET", "/api/v1/tasks/overview")
            assert overview["metrics"]["all_pending"] == 3
            assert (await call("GET", "/api/v1/tasks/" + unclassified["id"]))["alignment"] is None
            await patch(task, {"alignment": {"metric_codes": ["daily_routine"]}}, 409)
            checks.append(
                "Exact multi-period target IDs and KR periods read back; duplicate objects/metrics do not multiply global task count; V1 downgrade blocked"
            )
            batch = await call(
                "POST",
                "/api/v1/tasks/batch",
                {
                    "tasks": [
                        {**base, **v2(other_reason=" 批量必要原因 "), "assignee_account_code": people[person][1]}
                        for person in ("own_sales", "own_other")
                    ]
                },
                201,
            )
            assert len(batch["items"]) == 2
            assert all(row["alignment"]["other_reason"] == "批量必要原因" for row in batch["items"])
            # Target write functions explicitly advance the optimistic version; no table trigger does it.
            await c.execute(
                "UPDATE crm.sales_target SET amount=101,version_no=version_no+1 WHERE id=$1::uuid",
                targets["own_sales1"],
            )
            changed = await call("GET", "/api/v1/tasks/" + task["id"])
            assert changed["alignment"]["target_ids"] == a["target_ids"]
            assert (
                next(t for t in changed["alignment"]["targets"] if t["id"] == targets["own_sales1"])["version_no"] > 1
            )
            key = str(uuid4())
            reason = ECMASCRIPT_WHITESPACE + "需要补齐客户审计资料" + ECMASCRIPT_WHITESPACE
            other = await create(v2(other_reason=reason), key=key)
            replay = await create(v2(other_reason="需要补齐客户审计资料"), key=key)
            assert replay == other and other["alignment"]["other_reason"] == "需要补齐客户审计资料"
            assert other["alignment"]["metric_codes"] == [] and other["alignment"]["basis"] == "other"
            assert (
                await c.fetchval("SELECT other_reason FROM workflow.task_alignment WHERE task_id=$1::uuid", other["id"])
                == "需要补齐客户审计资料"
            )
            await create(v2(other_reason="另一个原因"), 409, key)
            for invalid in (
                {},
                {"other_reason": ECMASCRIPT_WHITESPACE},
                {"target_ids": [targets["own_sales1"]], "other_reason": "混合"},
                {"other_reason": "x" * 2001},
                {"metric_codes": ["collection"]},
            ):
                await create(v2(**invalid), 422)
            await create({"alignment_contract_version": 2}, 422)
            await create({"alignment": {"target_ids": [targets["own_sales1"]]}}, 422)
            upgraded = await patch(legacy, v2(other_reason=" 显式迁移原因 "))
            assert upgraded["alignment"]["other_reason"] == "显式迁移原因"
            # A direct SQL call cannot bypass the same ECMAScript trim or exclusive basis.
            async with db.transaction(active) as scoped:
                result = await scoped.fetchval(
                    "SELECT security.save_task_alignment($1::uuid,$2::jsonb,$3)",
                    other["id"],
                    {
                        "contract_version": 2,
                        "target_ids": [],
                        "key_result_ids": [],
                        "other_reason": reason,
                        "expected_contribution": None,
                    },
                    other["version_no"],
                )
                assert result["other_reason"] == "需要补齐客户审计资料"
            for body in (
                {"contract_version": 2, "target_ids": [], "key_result_ids": [], "other_reason": ECMASCRIPT_WHITESPACE},
                {
                    "contract_version": 2,
                    "target_ids": [targets["own_sales1"]],
                    "key_result_ids": [],
                    "other_reason": "不能混合",
                },
                {
                    "contract_version": 2,
                    "target_ids": [],
                    "key_result_ids": [],
                    "other_reason": "原因",
                    "expected_contribution": {"metric_code": "collection", "value": "1"},
                },
            ):
                try:
                    async with db.transaction(active) as scoped:
                        await scoped.fetchval(
                            "SELECT security.save_task_alignment($1::uuid,$2::jsonb,2)", other["id"], body
                        )
                except asyncpg.InvalidParameterValueError:
                    pass
                else:
                    raise AssertionError("SQL accepted invalid V2 basis")
            checks.append(
                "Canonical ECMAScript trim at API and SQL, persisted/read-back reasons, exact idempotency and strict exclusive basis"
            )
            other_accepted = await event(other, "accept")
            other_done = await event(other_accepted, "complete", note="旧事件完成其他任务")
            assert other_done["alignment"]["other_reason"] == "需要补齐客户审计资料"
            assert other_done["alignment"]["actual_contribution"] is None
            pending_id = str(
                await c.fetchval(
                    """INSERT INTO crm.sales_target_batch_request(
              workspace_id,scope_type,user_ref_id,period_type,period_start,period_end,applicant_user_ref_id,reason,items)
              VALUES($1,'person',$2,'quarter','2027-01-01','2027-03-31',$2,'合成待审目标',
              '[{"kind":"collection","proposed_amount":"200","base_version":null}]') RETURNING id""",
                    workspaces["own"],
                    people["own_sales"][0],
                )
            )
            task_count_before_invalid = await c.fetchval("SELECT count(*) FROM workflow.task")
            for target in (targets["own_other1"], targets["foreign_sales1"], pending_id, str(uuid4())):
                await create(v2(target_ids=[target]), 403)
            assert await c.fetchval("SELECT count(*) FROM workflow.task") == task_count_before_invalid
            for kr in (krs["own_other"], krs["foreign_sales"]):
                await create(v2(key_result_ids=[str(kr)]), 403)
            await c.execute(
                "UPDATE crm.review_metric SET active=false WHERE workspace_id=$1 AND code='collection'",
                workspaces["own"],
            )
            await create(v2(target_ids=[targets["own_sales1"]]), 403)
            await c.execute(
                "UPDATE crm.review_metric SET active=true WHERE workspace_id=$1 AND code='collection'",
                workspaces["own"],
            )
            await c.execute(
                "UPDATE crm.review_objective SET status='archived' WHERE id=(SELECT objective_id FROM crm.review_key_result WHERE id=$1)",
                krs["own_sales"],
            )
            await create(v2(key_result_ids=[str(krs["own_sales"])]), 403)
            await c.execute(
                "UPDATE crm.review_objective SET status='active' WHERE id=(SELECT objective_id FROM crm.review_key_result WHERE id=$1)",
                krs["own_sales"],
            )
            mixed = await create({**selected, "assignee_account_code": people["own_other"][1]})
            active = actors["own_other"]
            hidden = await call("GET", "/api/v1/tasks/" + mixed["id"])
            assert hidden["alignment"]["target_ids"] == [] and hidden["alignment"]["targets"] == []
            assert (
                hidden["alignment"]["restricted_target_count"] == 2
                and hidden["alignment"]["restricted_key_result_count"] == 2
            )
            await event(mixed, "accept")
            pending = await event(mixed, "complete", note="旧客户端完成，保留对象")
            assert pending["status"] == "pending_review"
            active = actors["own_sales"]
            complete = await event(pending, "approve_completion", version_no=pending["version_no"])
            assert complete["alignment"]["target_ids"] == a["target_ids"]
            checks.append(
                "Cross-tenant/hidden/disabled/archived objects rejected; target/KR metadata hidden; old recipient completion and creator review preserve V2"
            )
            financial_tables = (
                "crm.sales_target",
                "crm.review_key_result_progress",
                "crm.opportunity",
                "crm.opportunity_actual_confirmation",
                "crm.opportunity_actual_basis",
            )
            finance_before = {
                table: await c.fetchval(
                    "SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]')) FROM "
                    + table
                    + " t"
                )
                for table in financial_tables
            }
            accepted = await event(task, "accept")
            done = await event(
                accepted,
                "complete",
                version_no=accepted["version_no"],
                note="完成声明",
                actual_contribution={"metric_code": "collection", "value": "9999.99", "note": "仅声明"},
            )
            assert done["alignment"]["actual_contribution"]["status"] == "reported"
            assert done["alignment"]["counts_toward_financial_actuals"] is False
            finance_after = {
                table: await c.fetchval(
                    "SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text)::text,'[]')) FROM "
                    + table
                    + " t"
                )
                for table in financial_tables
            }
            assert finance_before == finance_after
            checks.append(
                "Planned/reported task money and completion leave all finance, target and KR progress rows byte-identical"
            )
            raced = await create(v2(other_reason="并发前"))

            async def contender(reason):
                connection = await asyncpg.connect(database=name, **config)
                try:
                    await connection.set_type_codec(
                        "jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text"
                    )
                    async with connection.transaction():
                        await connection.execute(f'SET LOCAL ROLE "{role}"')
                        await set_request_context(connection, active)
                        await connection.fetchval(
                            "SELECT security.save_task_alignment($1::uuid,$2::jsonb,1)",
                            raced["id"],
                            {"contract_version": 2, "target_ids": [], "key_result_ids": [], "other_reason": reason},
                        )
                    return "saved"
                except asyncpg.RaiseError:
                    return "stale"
                finally:
                    await connection.close()

            assert sorted(await asyncio.gather(contender("并发一"), contender("并发二"))) == ["saved", "stale"]
            guard = await create(v2(target_ids=[targets["own_sales1"]]))
            payload = v2(other_reason="切换为其他")
            request_key = str(uuid4())
            committed = await patch(guard, payload, key=request_key)
            assert await patch(guard, payload, key=request_key) == committed
            target_guard = await create(v2(target_ids=[targets["own_sales1"]]))
            await event(guard, "accept")
            await event(guard, "complete", note="完成后仅重放已提交收据")
            assert await patch(guard, payload, key=request_key) == committed
            await c.execute(
                "INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)",
                workspaces["own"],
                people["own_sales"][0],
            )
            await c.execute(
                "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,'target.read','deny')",
                workspaces["own"],
                people["own_sales"][0],
            )
            # Existing target links cannot be silently dropped once target visibility is revoked.
            await create(v2(other_reason="仍可创建其他"))
            await patch(target_guard, v2(other_reason="不得隐式丢弃"), 403)
            for permission in ("task.coordinate", "task.cancel"):
                await c.execute(
                    "INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2,$3,'deny')",
                    workspaces["own"],
                    people["own_sales"][0],
                    permission,
                )
            await patch(guard, payload, 403, request_key)
            await c.execute(
                "DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2",
                workspaces["own"],
                people["own_sales"][0],
            )
            checks.append(
                "Concurrent same-version writes yield one save; hidden target replacement and receipt replay after coordination revocation are denied"
            )
            # Runtime identities cannot directly select/mutate the new table or reconcile grants.
            denied = {}
            for service, identity in roles.items():
                denied[service] = []
                for query in (
                    "SELECT * FROM workflow.task_target_alignment",
                    "DELETE FROM workflow.task_target_alignment",
                    "SELECT security.reconcile_runtime_grants()",
                ):
                    try:
                        async with c.transaction():
                            await c.execute(f'SET LOCAL ROLE "{identity}"')
                            await c.execute(query)
                    except asyncpg.InsufficientPrivilegeError:
                        denied[service].append(query)
                    else:
                        raise AssertionError((service, "unexpected runtime authority", query))
            assert await acl(c, roles) == old_acl
            checks.append(
                "Four actual runtime roles cannot SELECT/DELETE the new relation or execute reconcile; original exact policy still passes"
            )
            result = {
                "status": "passed_isolated_D03_postgres_asgi",
                "checks": checks,
                "check_count": len(checks),
                "http_count": http_count,
                "migration": {
                    "path": str(sql.relative_to(ROOT)),
                    "sha256": hashlib.sha256(sql.read_bytes()).hexdigest(),
                },
                "ledger_before": before,
                "ledger_after": after,
                "rls": stage_rls,
                "migration_fingerprint": stage_policy["migration_fingerprint"],
                "rls_fingerprint": stage_policy["rls_fingerprint"],
                "exact_four_role_verifier": verify_result,
                "table_and_function_acl_unchanged": True,
                "column_note": "API existing task_alignment SELECT naturally includes the two new columns; policy table/EXEC grants unchanged",
                "runtime_denials": denied,
                "old_financial_rows_preserved": True,
                "roles": roles,
                "database": name,
                "business_auth": "trusted actor override with actual limited API role and RLS, not password login",
                "release_ready": False,
                "release_blocker": "requires parent source freeze, exact policy and production release gates; isolated validation is not release authorization",
                "production_or_existing_LOCAL_accessed": False,
                "model_calls": False,
            }
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for identity in roles.values():
            await admin.execute(f'DROP ROLE IF EXISTS "{identity}"')
        assert await admin.fetchval("SELECT count(*) FROM pg_database WHERE datname=$1", name) == 0
        assert (
            await admin.fetchval("SELECT count(*) FROM pg_roles WHERE rolname=ANY($1::text[])", list(roles.values()))
            == 0
        )
        await admin.close()
    result["cleanup"] = "own DB and four roles dropped, catalog counts both zero; cluster retained"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "status": result["status"],
                "check_count": len(checks),
                "http_count": http_count,
                "output": str(output),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(verify(parser.parse_args()))
