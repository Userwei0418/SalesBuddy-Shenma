# ruff: noqa: E402, E501, S108, S608 -- Private socket, synthetic identifiers, fixed SQL.
"""V256 restricted PostgreSQL + in-process HTTP collaboration acceptance.

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
from sales_backend.contracts.opportunity_access import OpportunityCollaboratorsUpdate
from sales_backend.contracts.visit_flow import canonical_fields, validate_stage_result
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.opportunity_access import OpportunityAccessError
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.repositories.collaboration import archive_fde_collaboration
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.idempotency import IdempotencyConflict
from sales_backend.services.opportunity_access import set_collaborators


async def verify(args):
    socket = str(Path(args.socket).resolve())
    if not socket.startswith(("/tmp/sb122-collab-pg.", "/private/tmp/sb122-collab-pg.")):
        raise ValueError("Requires dedicated scratch socket")
    config = dict(host=socket, port=args.port, user="postgres", password="", passfile="/dev/null", ssl=False)
    suffix = uuid4().hex[:12]
    name, role = "sb122_collab_" + suffix, "sb122_collab_api_" + suffix
    roles = {service: "sb122_collab_" + service + "_" + suffix for service in ("api", "worker", "platform", "feishu")}
    result = dict(status="started", checks=[], http=[], schema="V256", provider="synthetic artifacts",
                  real_provider_tested=False, production_accessed=False, network_http=False)
    admin = await asyncpg.connect(database="postgres", **config)
    c = None
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        c = await asyncpg.connect(database=name, **config)
        with redirect_stdout(io.StringIO()):
            await migrate_through(c, 256)
        for typ in ("json", "jsonb"):
            await c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
        ledger = dict(await c.fetch("SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))
        rls = await rls_contract(c)
        metadata = dict(schema="V256", migration_fingerprint=fingerprint(ledger), rls_fingerprint=fingerprint(rls))
        Path(args.output).with_name("RUNTIME_METADATA.json").write_text(json.dumps(
            dict(metadata=metadata, ledger=ledger, rls=rls), ensure_ascii=False, indent=2) + "\n")
        policy = load_policy(ROOT / "tools/installation/runtime_permissions.json")
        assert {key: policy[key] for key in metadata} == metadata, "Runtime policy metadata must match actual V256 ledger/RLS"
        # Use the real fresh-install provisioner and exact verifier for ALL four
        # services. Generated credentials are ephemeral and never emitted/saved.
        await provision_new(c, database=name, roles=roles, policy=policy)
        result["runtime_roles"] = await verify_runtime_roles(c, roles, policy)
        result["checks"].append("strict_four_service_runtime_roles_and_actual_V256_ledger_RLS_fingerprints")
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
                                oid, ws, customer, "Target" if oid == opportunity else "Sibling", people["owner"], east)
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

            path = f"/opportunities/{opportunity}/collaborators"
            version = before_opportunity["version_no"]
            payload = {"version_no": version, "member_ids": [str(people["helper"])]}
            who = actors["helper"]
            assert (await request("GET", "/opportunities/follow-up-options"))["items"] == []
            await request("GET", f"/opportunities/{opportunity}/access", 403)
            who = actors["owner"]
            async with transaction(who, readonly=True):
                assert (await c.fetchval("SELECT security.opportunity_access($1)", opportunity))["capabilities"]["can_manage_collaborators"]
            async with transaction(who):
                try:
                    async with c.transaction():
                        await set_collaborators(c, who, opportunity, OpportunityCollaboratorsUpdate(
                            version_no=version, member_ids=[people["foreign"]]), uuid4())
                except asyncpg.InvalidParameterValueError:
                    pass
                else:
                    raise AssertionError("Foreign target must fail database validation")
            await request("PUT", path, 422, payload)
            await request("PUT", path, 422, payload | {"member_ids": [str(people["foreign"])]}, uuid4())
            first_key = uuid4()
            changed = await request("PUT", path, body=payload, key=first_key)
            assert changed["changed"] and changed["event_id"] and changed["version_no"] == version + 1
            assert await request("PUT", path, body=payload, key=first_key) == changed
            await request("PUT", path, 409, payload | {"member_ids": []}, first_key)
            await request("PUT", path, 409, payload, uuid4())
            noop = await request("PUT", path, body=payload | {"version_no": version + 1}, key=uuid4())
            assert not noop["changed"] and noop["event_id"] is None and noop["version_no"] == version + 1
            assert await c.fetchval("SELECT count(*) FROM crm.opportunity_access_event WHERE opportunity_id=$1", opportunity) == 1
            result["checks"].append("owner_write_atomic_version_audit_required_key_replay_conflict_and_noop")
            for label in ("helper", "manager", "stranger", "foreign"):
                who = actors[label]
                await request("PUT", path, 403, {"version_no": version + 1, "member_ids": []}, uuid4())
            result["checks"].append("only_owner_can_manage_even_with_manager_workspace_edit")
            who = actors["helper"]
            access = await request("GET", f"/opportunities/{opportunity}/access")
            assert access["customer_reference"] == {"id": str(customer), "name": "Scoped customer"}
            assert access["capabilities"] == {"can_edit": False, "can_manage_collaborators": False, "can_transfer": False, "can_follow_up": True}
            options = await request("GET", "/opportunities/follow-up-options")
            assert [x["opportunity_id"] for x in options["items"]] == [str(opportunity)] and options["total"] == 1
            await request("GET", f"/opportunities/{sibling}/access", 403)
            async with transaction(who, readonly=True):
                assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM crm.customer WHERE id=$1)", customer)
                assert not await c.fetchval("SELECT security.authorization_opportunity('opportunity.update',$1)", opportunity)
                assert await c.fetchval("SELECT security.customer_reference($1)", customer) is None
                assert await c.fetchval("SELECT count(*) FROM crm.opportunity WHERE customer_id=$1", customer) == 1
            async with transaction(who):
                try:
                    async with c.transaction():
                        await c.execute("INSERT INTO crm.opportunity_collaborator VALUES($1,$2,$3,$3,clock_timestamp())", ws, sibling, people["helper"])
                except asyncpg.InsufficientPrivilegeError:
                    pass
                else:
                    raise AssertionError("direct collaboration DML escaped guarded function")
            result["checks"].append("cross_team_discovery_minimal_customer_no_sibling_customer_edit_or_direct_relation_write")

            # Existing explicit membership cannot override deny, inactive identity,
            # or tenant module availability. Admin changes are synthetic fixtures.
            await c.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2) ON CONFLICT DO NOTHING", ws, people["helper"])
            await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'opportunity.read','deny',NULL)", ws, people["helper"])
            await request("GET", f"/opportunities/{opportunity}/access", 403)
            await request("GET", "/opportunities/follow-up-options", 403)
            await c.execute("DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2", ws, people["helper"])
            await c.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1", people["helper"])
            await request("GET", f"/opportunities/{opportunity}/access", 403)
            who = actors["owner"]
            candidates = await request("GET", f"/opportunities/{opportunity}/access-candidates?purpose=collaborate")
            assert str(people["helper"]) not in {x["user_id"] for x in candidates["items"]}
            await request("PUT", path, 422, payload | {"version_no": version + 1}, uuid4())
            await c.execute("UPDATE platform.user_ref SET status='active' WHERE id=$1", people["helper"])
            who = actors["helper"]
            await c.execute("UPDATE control.tenant_module SET enabled=false WHERE workspace_id=$1 AND module_code='opportunity'", ws)
            await request("GET", f"/opportunities/{opportunity}/access", 403)
            await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1 AND module_code='opportunity'", ws)
            assert (await request("GET", f"/opportunities/{opportunity}/access"))["capabilities"]["can_follow_up"]
            result["checks"].append("explicit_deny_inactive_member_and_closed_module_override_collaboration")

            structure_body = {"customer_id": str(customer), "opportunity_id": str(opportunity), "text": "合成协作跟进：确认试用范围，下周提交方案。"}
            await request("POST", "/visit-flow/structure", 403, structure_body | {"is_first_visit": True}, uuid4())
            structure_key = uuid4()
            structure = await request("POST", "/visit-flow/structure", 202, structure_body, structure_key)

            async def load_run(run_id):
                row = await c.fetchrow("SELECT r.*,m.text_content FROM agent.run r JOIN agent.message m ON m.id=r.trigger_message_id WHERE r.id=$1", run_id)
                return RunInput(run_id, str(row["conversation_id"]), row["text_content"], "visit_entry", str(customer), who,
                                permission_version=row["identity_context"]["permission_version"], opportunity_id=str(opportunity),
                                visit_request=row["business_context"]["visit_request"])

            async def synthetic_result(run, payload):
                await c.execute("UPDATE agent.run SET status='waiting_human' WHERE id=$1", run.run_id)
                await c.execute("""INSERT INTO agent.artifact(workspace_id,conversation_id,run_id,artifact_type,schema_code,
                  schema_version,status,payload,created_by_user_ref_id) VALUES($1,$2,$3,'visit_entry','visit_entry.v1',1,'pending_confirm',$4,$5)""",
                                ws, run.conversation_id, run.run_id, payload, people["helper"])

            source_run = await load_run(structure["run_id"])
            facts = await AgentFactsLoader(database).load(source_run)
            assert facts["scope"]["selection"]["opportunity_id"] == str(opportunity)
            assert not set(facts).intersection({"contacts", "opportunities", "visits", "customer_profile"})
            fields = canonical_fields({"customer_name": "Scoped customer", "customer_type": "客户", "opportunity_name": "Target",
                "follow_up_record": "确认试用范围及交付负责人", "next_action": "2026-10-25提交试用方案", "contact_name": "合成联系人",
                "interaction_at": facts["server_fields"]["created_date"], "created_date": facts["server_fields"]["created_date"]})
            structure_result = validate_stage_result({"fields": fields, "summary": "合成结构化结果"}, facts)
            await synthetic_result(source_run, structure_result)
            quality_body = {"customer_id": str(customer), "opportunity_id": str(opportunity), "source_run_id": source_run.run_id,
                            "fields": structure_result["fields"], "summary": "合成结构化结果", "opportunity_mutation": None}
            forbidden_mutation = {"action": "update", "opportunity_id": str(opportunity), "name": "Changed by collaborator",
                                  "expected_close_date": "2026-12-31", "amount": "2000", "probability": 70, "version_no": version + 1}
            await request("POST", "/visit-flow/quality", 403, quality_body | {"opportunity_mutation": forbidden_mutation}, uuid4())
            quality_key = uuid4()
            quality = await request("POST", "/visit-flow/quality", 202, quality_body, quality_key)
            quality_run = await load_run(quality["run_id"])
            quality_facts = await AgentFactsLoader(database).load(quality_run)
            quality_result = validate_stage_result({"quality_review": {"follow_up_score": 90, "suggestions": [],
                "next_action": {"passed": True, "time_found": True, "goal_or_plan_found": True, "suggestions": []}}}, quality_facts)
            await synthetic_result(quality_run, quality_result)
            archive_body = {"customer_id": str(customer), "fields": {**quality_body["fields"], "opportunity_id": str(opportunity),
                            "_quality_review_run_id": quality_run.run_id}}
            archive_key = uuid4()
            await request("POST", "/visits", 403,
                          archive_body | {"fields": archive_body["fields"] | {"_opportunity_mutation": forbidden_mutation}}, uuid4())
            archived = await request("POST", "/visits", 201, archive_body, archive_key)
            visit_id = archived["id"]
            reread = await request("GET", f"/visits/{visit_id}")
            assert reread["opportunity_id"] == str(opportunity) and reread["recorder_id"] == str(people["helper"])
            assert await request("POST", "/visits", 201, archive_body, archive_key) == archived
            async with transaction(who, readonly=True):
                assert not await c.fetchval("SELECT security.authorization_visit('visit.supplement',$1::uuid)", visit_id)
            await request("PATCH", f"/visits/{visit_id}", 403, {"follow_up_record": "Injected supplement"}, uuid4())
            after = dict(await c.fetchrow("SELECT * FROM crm.opportunity WHERE id=$1", opportunity))
            for column in set(before_opportunity) - {"version_no", "updated_at"}:
                assert after[column] == before_opportunity[column], column
            assert dict(await c.fetchrow("SELECT * FROM crm.customer WHERE id=$1", customer)) == before_customer
            result["checks"].append("real_structure_quality_archive_reopen_and_receipt_without_opportunity_mutation_synthetic_provider")

            # A fixture attendee triggers the real repository notification path,
            # but must not grant a persistent FDE or explicit collaborator seat.
            await c.execute("INSERT INTO activity.visit_participant(visit_id,user_ref_id,workspace_id,participant_role,team_id_at_event,role_code_at_event,created_by_user_ref_id) VALUES($1,$2,$3,'fde',$4,'fde',$5)",
                            visit_id, people["fde"], ws, north, people["helper"])
            async with transaction(who):
                await archive_fde_collaboration(c, who, reread | {"customer_name": "Scoped customer"}, [str(people["fde"])])
            assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM crm.opportunity_participant WHERE opportunity_id=$1 AND user_ref_id=$2)", opportunity, people["fde"])
            assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM crm.opportunity_collaborator WHERE opportunity_id=$1 AND user_ref_id=$2)", opportunity, people["fde"])
            assert await c.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", opportunity) == version + 1
            assert await c.fetchval("SELECT count(*) FROM workflow.notification WHERE object_id=$1 AND recipient_user_ref_id=$2 AND template_code='visit_archived'", visit_id, people["fde"]) == 1
            result["checks"].append("single_visit_fde_attendance_notifies_without_persistent_membership_or_version_change")

            # Exercise the actual three HTTP entrypoints under concurrent removal
            # after their initial guards, while they wait on an existing receipt.
            for operation, endpoint, replay_body, replay_key in (
                ("visit-flow.structure", "/visit-flow/structure", structure_body, structure_key),
                ("visit-flow.quality", "/visit-flow/quality", quality_body, quality_key),
                ("visits.archive", "/visits", archive_body, archive_key),
            ):
                replay_c = await asyncpg.connect(database=name, **config)
                for typ in ("json", "jsonb"):
                    await replay_c.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
                task = None
                async def replay_http(replay_c=replay_c, endpoint=endpoint, replay_body=replay_body, replay_key=replay_key):
                    token = request_connection.set(replay_c)
                    try:
                        return await request("POST", endpoint, 403, replay_body, replay_key)
                    finally:
                        request_connection.reset(token)

                try:
                    async with c.transaction():
                        lock = f"mutation:{ws}:{people['helper']}:{operation}:{replay_key}"
                        await c.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", lock)
                        task = asyncio.create_task(replay_http())
                        for _ in range(500):
                            if await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND NOT granted)", replay_c.get_server_pid()):
                                break
                            if task.done():
                                await task
                                raise AssertionError("HTTP replay did not wait on its receipt")
                            await asyncio.sleep(0.01)
                        else:
                            raise AssertionError("HTTP replay never reached its receipt lock")
                        live_version = await c.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", opportunity)
                        async with transaction(actors["owner"]):
                            await set_collaborators(c, actors["owner"], opportunity,
                                OpportunityCollaboratorsUpdate(version_no=live_version, member_ids=[]), uuid4())
                    await asyncio.wait_for(task, timeout=10)
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    await replay_c.close()
                who = actors["owner"]
                await request("PUT", path, body={"version_no": live_version + 1, "member_ids": [str(people["helper"])]}, key=uuid4())
                who = actors["helper"]
                result["checks"].append(operation + "_http_replay_rejects_collaboration_removed_during_receipt_wait")

            prior_scope = quality_facts["scope"]["permission_version"]
            who = actors["owner"]
            current_version = await c.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1", opportunity)
            removed = await request("PUT", path, body={"version_no": current_version, "member_ids": []}, key=uuid4())
            assert removed["version_no"] == current_version + 1
            who = actors["helper"]
            assert (await request("GET", "/opportunities/follow-up-options"))["items"] == []
            await request("GET", f"/opportunities/{opportunity}/access", 403)
            await request("POST", "/visit-flow/structure", 403, structure_body, uuid4())
            await request("POST", "/visit-flow/quality", 403, quality_body, uuid4())
            await request("POST", "/visits", 403, archive_body, archive_key)
            await request("GET", f"/visits/{visit_id}", 404)
            async with transaction(who, readonly=True):
                assert (await AuthorizationRepository().snapshot(c))["permission_version"] != prior_scope
            result["checks"].append("removal_invalidates_async_scope_and_discovery_structure_quality_save_receipt_reopen")

            # Removing one grant must preserve an independent workspace grant.
            who = actors["owner"]
            manager_added = await request("PUT", path, body={"version_no": removed["version_no"], "member_ids": [str(people["manager"])]}, key=uuid4())
            manager_removed = await request("PUT", path, body={"version_no": manager_added["version_no"], "member_ids": []}, key=uuid4())
            who = actors["manager"]
            assert (await request("GET", f"/opportunities/{opportunity}/access"))["capabilities"]["can_edit"]
            result["checks"].append("removing_explicit_relation_preserves_independent_workspace_access")

            # Two independent API-role transactions share a version: one business
            # write + receipt commits, the loser rolls back with no extra audit.
            async def contender(member):
                other = await asyncpg.connect(database=name, **config)
                try:
                    for typ in ("json", "jsonb"):
                        await other.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
                    async with other.transaction():
                        await other.execute(f'SET LOCAL ROLE "{role}"')
                        await set_request_context(other, actors["owner"])
                        return await set_collaborators(other, actors["owner"], opportunity,
                            OpportunityCollaboratorsUpdate(version_no=manager_removed["version_no"], member_ids=[member]), uuid4())
                finally:
                    await other.close()

            audit_before = await c.fetchval("SELECT count(*) FROM crm.opportunity_access_event WHERE opportunity_id=$1", opportunity)
            receipts_before = await c.fetchval("SELECT count(*) FROM ops.mutation_receipt WHERE operation=$1", f"opportunity.collaborators:{opportunity}")
            races = await asyncio.gather(contender(people["helper"]), contender(people["stranger"]), return_exceptions=True)
            assert sum(isinstance(x, dict) and x.get("changed") for x in races) == 1, races
            assert sum(isinstance(x, asyncpg.SerializationError) for x in races) == 1, races
            assert await c.fetchval("SELECT count(*) FROM crm.opportunity_access_event WHERE opportunity_id=$1", opportunity) == audit_before + 1
            assert await c.fetchval("SELECT count(*) FROM ops.mutation_receipt WHERE operation=$1", f"opportunity.collaborators:{opportunity}") == receipts_before + 1
            result["checks"].append("concurrent_same_version_one_commit_one_conflict_atomic_audit_and_receipt")

            # Hold the receipt lock, start an authorized replay, revoke owner
            # update permission while it waits, then release. Replay must reject.
            await c.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2) ON CONFLICT DO NOTHING", ws, people["owner"])
            waiting = await asyncpg.connect(database=name, **config)
            for typ in ("json", "jsonb"):
                await waiting.set_type_codec(typ, schema="pg_catalog", encoder=json.dumps, decoder=json.loads, format="text")
            ready = asyncio.Event()
            async def replay_waiter():
                try:
                    async with waiting.transaction():
                        await waiting.execute(f'SET LOCAL ROLE "{role}"')
                        await set_request_context(waiting, actors["owner"])
                        ready.set()
                        return await set_collaborators(waiting, actors["owner"], opportunity,
                            OpportunityCollaboratorsUpdate(**payload), first_key)
                finally:
                    await waiting.close()

            task = None
            try:
                async with c.transaction():
                    lock = f"mutation:{ws}:{people['owner']}:opportunity.collaborators:{opportunity}:{first_key}"
                    await c.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", lock)
                    waiter_pid = waiting.get_server_pid()
                    task = asyncio.create_task(replay_waiter())
                    await asyncio.wait_for(ready.wait(), timeout=5)
                    for _ in range(100):
                        if await c.fetchval("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND NOT granted)", waiter_pid):
                            break
                        await asyncio.sleep(0.01)
                    else:
                        raise AssertionError("Replay never reached competing receipt lock")
                    await c.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect,scope_code) VALUES($1,$2,'opportunity.update','deny',NULL)", ws, people["owner"])
                replay_outcome = (await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=10))[0]
                assert isinstance(replay_outcome, OpportunityAccessError) and replay_outcome.status == 403, replay_outcome
            finally:
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                if not waiting.is_closed():
                    await waiting.close()
            result["checks"].append("revocation_during_receipt_lock_wait_blocks_previously_committed_replay")
            async with c.transaction():
                await set_request_context(c, actors["manager"])
                await c.execute("UPDATE crm.opportunity SET owner_user_ref_id=NULL,ownership_resolution='unassigned' WHERE id=$1", sibling)
            who = actors["manager"]
            unresolved = await request("GET", f"/opportunities/{sibling}/access")
            assert unresolved["owner"]["user_id"] is None
            assert unresolved["capabilities"]["can_manage_collaborators"] is False
            assert unresolved["capabilities"]["can_transfer"] is False
            result["checks"].append("unassigned_legacy_owner_remains_null_and_cannot_manage_or_transfer")
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
