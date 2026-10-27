# ruff: noqa: E402, E501, S108, S608 -- Private socket, synthetic identifiers, fixed SQL.
"""V260 owner-only follow-up: red/green real PostgreSQL and shared ASGI acceptance.

Structure/quality jobs use explicit synthetic provider artifacts. No worker or
provider runs, no saved DSN, no shared database. All business commands run as the
real NOBYPASSRLS API role; fixture/worker-result installation uses test admin only.
"""

import argparse
import asyncio
import io
import json
import sys
import traceback
from contextlib import asynccontextmanager, redirect_stdout
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend/scripts"), str(ROOT / "tools/installation")]
from runtime_roles import fingerprint, load_policy, provision_new
from runtime_roles import verify as verify_runtime_roles
from verify_review_targets_postgres import migrate_through, rls_contract

from sales_backend.api import business, opportunity_access, visit_flow
from sales_backend.api.dependencies import get_database, get_identity
from sales_backend.api.idempotency import idempotency_conflict_handler
from sales_backend.api.permission_gate import enforce_route_permission
from sales_backend.contracts.visit_flow import canonical_fields, validate_stage_result
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.idempotency import IdempotencyConflict


async def verify(args):
    socket = str(Path(args.socket).resolve())
    if not socket.startswith(("/tmp/sb122-collab-pg.", "/private/tmp/sb122-collab-pg.")):
        raise ValueError("Requires dedicated scratch socket")
    config = dict(host=socket, port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    suffix = uuid4().hex[:12]
    name, role = "sb122_collab_" + suffix, "sb122_collab_api_" + suffix
    roles = {service: "sb122_collab_" + service + "_" + suffix for service in ("api", "worker", "platform", "feishu")}
    result = dict(status="started", checks=[], http=[], schema="V258", provider="synthetic artifacts",
                  real_provider_tested=False, production_accessed=False, network_http=False)
    admin = await asyncpg.connect(database="postgres", **config)
    c = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 258)
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        ledger = dict(await c.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
        rls = await rls_contract(c)
        metadata = dict(schema="V258", migration_fingerprint=fingerprint(ledger), rls_fingerprint=fingerprint(rls))
        Path(args.output).with_name("RUNTIME_METADATA.json").write_text(json.dumps(
            dict(metadata=metadata, ledger=ledger, rls=rls), ensure_ascii=False, indent=2) + "\n")
        policy = load_policy(ROOT / "tools/installation/runtime_permissions.json")
        assert {key: policy[key] for key in metadata} == metadata, "Runtime policy metadata must match actual V258 ledger/RLS"
        # Use the real fresh-install provisioner and exact verifier for ALL four
        # services. Generated credentials are ephemeral and never emitted/saved.
        await provision_new(c, database=name, roles=roles, policy=policy)
        result["runtime_roles"] = await verify_runtime_roles(c, roles, policy)
        result["checks"].append("strict_four_service_runtime_roles_and_actual_V258_ledger_RLS_fingerprints")
        await c.execute("SET row_security=on")
        ws, foreign_ws, east, north = [uuid4() for _ in range(4)]
        people = {key: uuid4() for key in ("owner", "helper", "stranger", "manager", "foreign", "fde")}
        for tenant in (ws, foreign_ws):
            await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Synthetic collaboration')", tenant, str(tenant))
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", tenant)
            await c.execute("""UPDATE config.permission_role_grant g SET scope_code='self',team_ids='{}'
             FROM config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=$1 AND r.builtin_role_code='sales'
             AND g.permission_code IN ('customer.reference','customer.read','opportunity.read','visit.read')""", tenant)
        for team in (east, north):
            await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)", team, ws, str(team))
        actors = {}
        for label, user in people.items():
            tenant = foreign_ws if label == "foreign" else ws
            team = east if label == "owner" else north
            job = label if label in {"manager", "fde"} else "sales"
            if label == "foreign":
                team = uuid4()
                await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)", team, tenant, str(team))
            await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)", user, tenant, label)
            await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)", tenant, user, job, "workspace" if job == "manager" else "self", team)
            await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)", tenant, user, team, "fde" if job == "fde" else "sales")
            actors[label] = ActorContext(workspace_id=str(tenant), user_id=str(user), role=job,
                                        data_scope="workspace" if job == "manager" else "self", team_ids=[str(team)])
        customer, opportunity, sibling = uuid4(), uuid4(), uuid4()
        await c.execute("INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id) VALUES($1,$2,'Scoped customer','Scoped customer',$3,$3,$4)", customer, ws, people["owner"], east)
        async with c.transaction():
            await set_request_context(c, actors["owner"])
            for oid in (opportunity, sibling):
                await c.execute("SELECT set_config('app.native_opportunity_create',$1,true)", str(oid))
                await c.execute("""INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
                 amount,currency,probability,stage_code,status,sales_channel,created_by_user_ref_id)
                 VALUES($1,$2,$3,$4,$5,$6,1000,'CNY',70,'qualified','open','direct',$5)""",
                                oid, ws, customer, "Target" if oid == opportunity else "Sibling", people["helper"] if oid == opportunity else people["owner"], north if oid == opportunity else east)
        before_opportunity = dict(await c.fetchrow("SELECT * FROM crm.opportunity WHERE id=$1", opportunity))
        before_customer = dict(await c.fetchrow("SELECT * FROM crm.customer WHERE id=$1", customer))
        who = actors["owner"]
        request_connection = ContextVar("collaboration_test_connection", default=c)

        @asynccontextmanager
        async def transaction(actor, **kwargs):
            request_c = request_connection.get()
            async with request_c.transaction(isolation=kwargs.get("isolation"), readonly=kwargs.get("readonly", False)):
                await request_c.execute(f'SET LOCAL ROLE "{role}"')
                await set_request_context(request_c, actor)
                assert not await request_c.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                yield request_c

        database = SimpleNamespace(transaction=transaction)
        app = FastAPI(dependencies=[Depends(enforce_route_permission)])
        app.state.database = database
        app.state.settings = SimpleNamespace()

        @app.middleware("http")
        async def synthetic_identity(request, call_next):
            # Real feature gate and DB scope, but explicit synthetic authentication.
            request.state.verified_identity = SimpleNamespace(actor=who, client_channel="wechat-mini-program")
            return await call_next(request)

        @app.exception_handler(PermissionError)
        async def denied(request, exc):
            return JSONResponse(status_code=403, content={"detail": str(exc)})

        for router in (opportunity_access.router, visit_flow.router, business.router):
            app.include_router(router)
        app.add_exception_handler(IdempotencyConflict, idempotency_conflict_handler)
        app.dependency_overrides[get_identity] = lambda: SimpleNamespace(actor=who)
        app.dependency_overrides[get_database] = lambda: database

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            async def request(method, path, expected=200, body=None, key=None):
                response = await client.request(method, "/api/v1" + path, json=body,
                                                headers={"Idempotency-Key": str(key)} if key else {})
                result["http"].append({"method": method, "path": path, "status": response.status_code})
                assert response.status_code == expected, (path, response.status_code, response.text)
                return response.json()

            who = actors["helper"]
            structure_body = {"customer_id": str(customer), "opportunity_id": str(opportunity),
                              "text": "合成主责跟进：确认试用范围，下周提交方案。"}
            async with transaction(who):
                assert await c.fetchval("SELECT security.authorization_opportunity('opportunity.read',$1)", opportunity)
                assert await c.fetchval("SELECT security.opportunity_follow_up_reference($1,$2)", customer, opportunity) is None
            await request("POST", "/visit-flow/structure", 403, structure_body, uuid4())
            result["checks"].append("red_V258_current_owner_without_customer_access_cannot_follow_up")
            with redirect_stdout(io.StringIO()):
                await migrate_through(c, 260)
            final_ledger = dict(await c.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
            # V260 replaces one function and adds no RLS policy or grant. Keep
            # the reviewed RLS fingerprint; verify strips installed role names.
            final_policy = dict(policy, schema="V260", migration_fingerprint=fingerprint(final_ledger))
            result["runtime_roles_after"] = await verify_runtime_roles(c, roles, final_policy)
            result["scope"] = "V258 baseline plus V260 only; V259 transfer integrated separately"
            result["migration_count"] = len(final_ledger)
            result["checks"].append("forward_migration_preserves_exact_four_service_effective_permissions")

            async def target(permission, *, selected_customer=customer, selected_opportunity=opportunity,
                             recorder=None, actor=None):
                current = actor or who
                async with transaction(current):
                    return bool(await c.fetchval(
                        "SELECT security.authorization_visit_target($1,$2,$3,$4,$5)", permission,
                        selected_customer, selected_opportunity, recorder or people["helper"], north))

            async with transaction(who):
                reference = await c.fetchval("SELECT security.opportunity_follow_up_reference($1,$2)", customer, opportunity)
                assert reference == {"id": str(customer), "name": "Scoped customer"}, reference
                assert await c.fetchval("SELECT security.customer_reference($1)", customer) is None
                assert await c.fetchval("SELECT count(*) FROM crm.customer WHERE id=$1", customer) == 0
                assert await c.fetchval("SELECT count(*) FROM crm.opportunity WHERE id=$1", sibling) == 0
            assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM crm.opportunity_collaborator WHERE opportunity_id=$1)", opportunity)
            assert (await request("GET", f"/opportunities/{opportunity}/access"))["capabilities"]["can_follow_up"]
            options = await request("GET", "/opportunities/follow-up-options")
            assert [x["opportunity_id"] for x in options["items"]] == [str(opportunity)]
            result["checks"].append("green_minimal_reference_and_list_without_customer_or_sibling_or_collaborator_access")
            actions = ("visit.read", "visit.create", "visit.structure", "visit.quality_review")
            for action in actions:
                assert await target(action), action
                assert not await target(action, selected_customer=uuid4())
                assert not await target(action, selected_opportunity=sibling)
                assert not await target(action, selected_opportunity=None)
                assert not await target(action, recorder=people["owner"])
                assert not await target(action, actor=actors["foreign"], recorder=people["foreign"])
            for action in ("visit.first_visit", "visit.attendance_manage", "visit.supplement", "opportunity.update"):
                assert not await target(action), action
            result["checks"].append("four_actions_exact_pair_self_recorder_tenant_and_no_extra_actions")
            await c.execute("INSERT INTO crm.opportunity_collaborator(workspace_id,opportunity_id,user_ref_id,added_by_user_ref_id) VALUES($1,$2,$3,$4)", ws, sibling, people["helper"], people["owner"])
            for action in actions:
                assert await target(action, selected_opportunity=sibling)
            await c.execute("DELETE FROM crm.opportunity_collaborator WHERE opportunity_id=$1", sibling)
            assert not await target("visit.create", selected_opportunity=sibling)
            result["checks"].append("existing_explicit_collaborator_path_unchanged_and_removal_immediate")
            await c.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)", ws, people["helper"])
            for action in (*actions, "opportunity.read"):
                await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,$3,'deny',NULL)", ws, people["helper"], action)
                assert not await target("visit.create" if action == "opportunity.read" else action)
                await c.execute("DELETE FROM config.account_permission_override WHERE user_ref_id=$1 AND permission_code=$2", people["helper"], action)
            scope_test = c.transaction()
            await scope_test.start()
            try:
                # Allows are additive. Remove the fixture's builtin allow so
                # the unrelated-team override is its only current action grant.
                await c.execute("DELETE FROM config.permission_role_grant g USING config.permission_role r WHERE r.id=g.role_id AND r.workspace_id=$1 AND r.builtin_role_code='sales' AND g.permission_code='visit.create'", ws)
                await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code,team_ids) VALUES($1,$2,'visit.create','allow','teams',$3)", ws, people["helper"], [east])
                assert not await target("visit.create"), "unrelated team scope must not pass via authorization_has"
            finally:
                await scope_test.rollback()
            result["checks"].append("every_current_action_deny_and_unrelated_team_scope_block_owner_path")
            await c.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", people["helper"])
            assert not await target("visit.create")
            await c.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", people["helper"])
            modules = await c.fetch("SELECT DISTINCT module_code FROM control.permission_module WHERE permission_code IN ('visit.create','opportunity.read')")
            assert len(modules) == 2
            for row in modules:
                module = row["module_code"]
                await c.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code=$2", ws, module)
                assert not await target("visit.create"), module
                await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code=$2", ws, module)
            result["checks"].append("disabled_account_and_closed_modules_fail_immediately")

            structure = await request("POST", "/visit-flow/structure", 202, structure_body, uuid4())
            async def load_run(run_id):
                row = await c.fetchrow("SELECT r.*,m.text_content FROM agent.run r JOIN agent.message m ON m.id=r.trigger_message_id WHERE r.id=$1", run_id)
                return RunInput(run_id, str(row["conversation_id"]), row["text_content"], "visit_entry", str(customer), who,
                                permission_version=row["identity_context"]["permission_version"], opportunity_id=str(opportunity),
                                visit_request=row["business_context"]["visit_request"])

            async def synthetic_result(run, payload):
                await c.execute("UPDATE agent.run SET status='waiting_human' WHERE id=$1", run.run_id)
                await c.execute("INSERT INTO agent.artifact(workspace_id,conversation_id,run_id,artifact_type,schema_code,schema_version,status,payload,created_by_user_ref_id) VALUES($1,$2,$3,'visit_entry','visit_entry.v1',1,'pending_confirm',$4,$5)", ws, run.conversation_id, run.run_id, payload, people["helper"])

            run = await load_run(structure["run_id"])
            facts = await AgentFactsLoader(database).load(run)
            assert not set(facts).intersection({"contacts", "opportunities", "visits", "customer_profile"})
            fields = canonical_fields({"customer_name": "Scoped customer", "customer_type": "客户", "opportunity_name": "Target",
                "follow_up_record": "确认试用范围及交付负责人", "next_action": "2026-10-25提交试用方案", "contact_name": "合成联系人",
                "interaction_at": facts["server_fields"]["created_date"], "created_date": facts["server_fields"]["created_date"]})
            structured = validate_stage_result({"fields": fields, "summary": "合成结构化结果"}, facts)
            await synthetic_result(run, structured)
            quality = await request("POST", "/visit-flow/quality", 202,
                dict(customer_id=str(customer), opportunity_id=str(opportunity), source_run_id=run.run_id,
                     fields=structured["fields"], summary="合成结构化结果", opportunity_mutation=None), uuid4())
            quality_run = await load_run(quality["run_id"])
            quality_facts = await AgentFactsLoader(database).load(quality_run)
            reviewed = validate_stage_result({"quality_review": {"follow_up_score": 90, "suggestions": [],
                "next_action": {"passed": True, "time_found": True, "goal_or_plan_found": True, "suggestions": []}}}, quality_facts)
            await synthetic_result(quality_run, reviewed)
            archive_body = {"customer_id": str(customer), "fields": {**structured["fields"],
                "opportunity_id": str(opportunity), "_quality_review_run_id": quality_run.run_id}}
            archive_key = uuid4()
            archived = await request("POST", "/visits", 201, archive_body, archive_key)
            reread = await request("GET", f"/visits/{archived['id']}")
            assert reread["opportunity_id"] == str(opportunity)
            assert await request("POST", "/visits", 201, archive_body, archive_key) == archived
            assert dict(await c.fetchrow("SELECT * FROM crm.customer WHERE id=$1", customer)) == before_customer
            assert dict(await c.fetchrow("SELECT * FROM crm.opportunity WHERE id=$1", opportunity)) == before_opportunity
            result["checks"].append("actual_structure_quality_archive_readback_replay_with_synthetic_artifacts_no_ownership_change")
            await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'visit.create','deny',NULL)", ws, people["helper"])
            await request("POST", "/visits", 403, archive_body, archive_key)
            result["checks"].append("archive_receipt_replay_rechecks_current_owner_action_permission")
        result["status"] = "passed"
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__, error=str(exc), traceback=traceback.format_exc())
        raise
    finally:
        if c:
            await c.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for runtime_role in roles.values():
            await admin.execute(f'DROP ROLE IF EXISTS "{runtime_role}"')
        await admin.close()
        result["disposable_database_and_role_removed"] = True
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, default=55432)
    parser.add_argument("--output", required=True)
    asyncio.run(verify(parser.parse_args()))
