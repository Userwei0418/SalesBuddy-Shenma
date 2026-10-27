"""V297 -> V298 with exact runtime grants, real RLS and a private synthetic PG.

No provider, shared database or service is used. The existing temporary-cluster
fixture always stops PostgreSQL and removes its private Unix socket/data.
"""
from copy import deepcopy
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import asyncpg
import pytest

from tests.test_work_report_manager_team_eligibility import postgres  # noqa: F401
from tests.integration.test_fde_battle_map_reviews import answer, handler_for
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.domain.agent import ActorContext

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "database/scripts"), str(ROOT / "tools/installation")]
from migrate import migrate
from runtime_roles import SCHEMAS, fingerprint, load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles

WORKER, API = "map_test_worker", "map_test_api"
MIGRATION = ROOT / "database/migrations/V298__battle_map_self_score_write.sql"
ADDED = {"self_quadrant_insert", "self_quadrant_update"}


async def rls(c):
    tables = [dict(r) for r in await c.fetch("""SELECT n.nspname||'.'||c.relname AS name,
      c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY 1""", SCHEMAS)]
    policies = [dict(r) for r in await c.fetch("""SELECT schemaname,tablename,policyname,
      permissive,roles::text[],cmd,qual,with_check FROM pg_policies
      WHERE schemaname=ANY($1::text[]) ORDER BY 1,2,3""", SCHEMAS)]
    return {"tables": tables, "policies": policies}


async def denied(c, sql, *args):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with c.transaction():
            await c.fetch(sql, *args)


async def seed(c):
    actors, customers = [], []
    for n in range(2):
        w, u, t, customer = [uuid4() for _ in range(4)]
        await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Map RLS synthetic')", w, str(w))
        await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", w)
        await c.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'map-test','Synthetic team')", t, w)
        await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,'Synthetic sales')", u, w, str(u))
        await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'sales','self',$3)", w, u, t)
        await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'sales')", w, u, t)
        await c.execute("""INSERT INTO crm.customer(id,workspace_id,name,normalized_name,
          owner_user_ref_id,owner_team_id,created_by_user_ref_id,level_code)
          VALUES($1,$2,$3,$3,$4,$5,$4,'Tier-2')""", customer, w, f"Synthetic map customer {n}", u, t)
        await c.execute("INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2)", w, u)
        for permission in ("customer.update", "opportunity.update"):
            await c.execute("""INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect)
              VALUES($1,$2,$3,'deny')""", w, u, permission)
        actors.append(ActorContext(workspace_id=str(w), user_id=str(u), role="sales", data_scope="self", team_ids=(str(t),)))
        customers.append(str(customer))
    other = uuid4()
    await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2::uuid,$3,$3,'Other subject')", other, actors[0].workspace_id, str(other))
    return actors, customers, str(other)


def insert_sql():
    return """INSERT INTO insight.quadrant_score(workspace_id,customer_id,potential_score,
      relationship_score,quadrant_code,rule_set_id,subject_user_ref_id,input_snapshot)
      VALUES($1::uuid,$2::uuid,75,50,'customer_asset',$3::uuid,$4::uuid,'{}') RETURNING id"""


@pytest.mark.asyncio
async def test_private_score_write_under_exact_worker_policy(postgres, tmp_path):
    config = dict(host=str(postgres), port=55439, database="postgres", password="", passfile="/dev/null", ssl=False)
    owner = await asyncpg.connect(user=getpass.getuser(), command_timeout=120, **config)
    # The reused cluster fixture uses the host locale; the product migrations
    # need UTF-8 even when initdb's --no-locale selected SQL_ASCII.
    await owner.execute("CREATE DATABASE map_rls_test TEMPLATE template0 ENCODING 'UTF8'")
    await owner.close()
    config["database"] = "map_rls_test"
    owner = await asyncpg.connect(user=getpass.getuser(), command_timeout=120, **config)
    worker = api = None
    checks = []
    try:
        for role in ("salegent_feishu_worker", "salegent_api", "salegent_worker", "salegent_platform"):
            await owner.execute(f"CREATE ROLE {role} NOLOGIN")
        old_root = tmp_path / "v297"
        (old_root / "migrations").mkdir(parents=True)
        for name in ("baseline", "seeds"):
            (old_root / name).symlink_to(ROOT / "database" / name, target_is_directory=True)
        for source in (ROOT / "database").glob("V*.sql"):
            (old_root / source.name).symlink_to(source)
        for source in (ROOT / "database/migrations").glob("V*.sql"):
            if source != MIGRATION:
                (old_root / "migrations" / source.name).symlink_to(source)
        await migrate(owner, old_root, adopt_existing=True)
        policy = load_policy()
        before = await rls(owner)
        old_policy = deepcopy(policy)
        old_policy.update(schema="V297", migration_fingerprint=fingerprint(dict(await owner.fetch(
            "SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))), rls_fingerprint=fingerprint(before))
        for role in (WORKER, API):
            await owner.execute(f"CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS")
        async with owner.transaction():
            await reconcile_service_roles(owner, {"worker": WORKER, "api": API}, old_policy)
            actors, customers, other = await seed(owner)
        worker = await asyncpg.connect(user=WORKER, command_timeout=30, **config)
        api = await asyncpg.connect(user=API, command_timeout=30, **config)
        for connection in (worker, api):
            await _initialize_connection(connection)
            assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
        actor, customer = actors[0], customers[0]
        handler = handler_for(worker)
        facts = await handler._load_facts(customer, actor)
        async with worker.transaction():
            await set_request_context(worker, actor)
            assert await worker.fetchval("SELECT security.has_customer_access($1::uuid)", customer)
            assert not await worker.fetchval("SELECT security.has_customer_write_access($1::uuid)", customer)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await handler._persist(customer, actor, facts, answer(handler, facts), {})
        checks.append("v297_readable_noneditable_customer_reproduces_real_worker_persist_42501")

        await migrate(owner, ROOT / "database", adopt_existing=True)
        after = await rls(owner)
        assert after["tables"] == before["tables"]
        assert [r for r in after["policies"] if not (r["schemaname"] == "insight" and r["tablename"] == "quadrant_score" and r["policyname"] in ADDED)] == before["policies"]
        added = [r for r in after["policies"] if r["policyname"] in ADDED]
        assert len(added) == 2 and {r["cmd"] for r in added} == {"INSERT", "UPDATE"}
        assert all(r["permissive"] == "PERMISSIVE" and r["roles"] == ["public"] for r in added)
        new_policy = deepcopy(policy)
        new_policy.update(schema="V298", migration_fingerprint=fingerprint(dict(await owner.fetch(
            "SELECT migration_key,checksum_sha256 FROM ops.migration_checksum"))), rls_fingerprint=fingerprint(after))
        assert new_policy == policy, "The checked-in installation fingerprints must match the real migration"
        # No grant reconciliation after V298: exact privileges must remain valid.
        await verify_service_roles(owner, {"worker": WORKER, "api": API}, new_policy)
        checks.append("only_two_self_score_policies_added_all_existing_rls_and_exact_runtime_acl_unchanged")

        await handler._persist(customer, actor, facts, answer(handler, facts), {})
        facts = await handler._load_facts(customer, actor)
        await handler._persist(customer, actor, facts, answer(handler, facts, 82), {})
        async with worker.transaction():
            await set_request_context(worker, actor)
            rows = await worker.fetch("SELECT id,potential_score,valid_to='infinity' AS current FROM insight.quadrant_score WHERE customer_id=$1::uuid", customer)
            assert len(rows) == 2 and sum(r["current"] for r in rows) == 1
            current = next(r for r in rows if r["current"])
            assert current["potential_score"] == 82
            assert not await worker.fetchval("SELECT security.has_customer_write_access($1::uuid)", customer)
            assert not await worker.fetchval("SELECT security.authorization_has('opportunity.update')")
            await denied(worker, "UPDATE crm.customer SET name='forbidden' WHERE id=$1::uuid RETURNING id", customer)
            await denied(worker, "UPDATE crm.opportunity SET amount=999 WHERE false RETURNING id")
        checks.append("real_handler_replaces_own_score_atomically_without_commercial_write_permissions")

        rule = facts["company_policy"]["id"] if facts["company_policy"] else await owner.fetchval(
            "SELECT id::text FROM config.rule_set WHERE rule_code='customer_quadrant' AND status='active' ORDER BY version_no DESC LIMIT 1")
        foreign_score = await owner.fetchval(insert_sql(), actors[1].workspace_id, customers[1], rule, actors[1].user_id)
        other_score = await owner.fetchval(insert_sql(), actor.workspace_id, customer, rule, other)
        async with worker.transaction():
            await set_request_context(worker, actor)
            await denied(worker, insert_sql(), actor.workspace_id, customer, rule, other)
            await denied(worker, insert_sql(), actors[1].workspace_id, customers[1], rule, actor.user_id)
            await denied(worker, insert_sql(), actor.workspace_id, customers[1], rule, actor.user_id)
            await denied(worker, "UPDATE insight.quadrant_score SET subject_user_ref_id=$2::uuid WHERE id=$1 RETURNING id", current["id"], other)
            for score in (other_score, foreign_score):
                assert await worker.execute("UPDATE insight.quadrant_score SET valid_to=clock_timestamp() WHERE id=$1", score) == "UPDATE 0"
            await denied(worker, "DELETE FROM insight.quadrant_score WHERE id=$1 RETURNING id", current["id"])
        async with api.transaction():
            await set_request_context(api, actor)
            await denied(api, insert_sql(), actor.workspace_id, customer, rule, actor.user_id)
        checks.append("foreign_workspace_subject_reassignment_and_other_subject_updates_denied_api_still_readonly")

        await owner.execute("""INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect)
          VALUES($1::uuid,$2::uuid,'customer.read','deny')""", actor.workspace_id, actor.user_id)
        with pytest.raises(PermissionError, match="权限已变化"):
            await handler._persist(customer, actor, facts, answer(handler, facts), {})
        async with worker.transaction():
            await set_request_context(worker, actor)
            assert not await worker.fetchval("SELECT security.has_customer_access($1::uuid)", customer)
            await denied(worker, insert_sql(), actor.workspace_id, customer, rule, actor.user_id)
            assert await worker.execute("UPDATE insight.quadrant_score SET valid_to=clock_timestamp() WHERE id=$1", current["id"]) == "UPDATE 0"
        assert await owner.fetchval("SELECT valid_to='infinity' FROM insight.quadrant_score WHERE id=$1", current["id"])
        checks.append("customer_read_revocation_blocks_direct_write_and_stale_inference_preserving_current_score")
        receipt = dict(status="passed", synthetic=True, running_business_data_touched=False,
            shared_or_production_database_accessed=False, external_model_called=False,
            checks=checks, check_count=len(checks), migration_sha256=hashlib.sha256(MIGRATION.read_bytes()).hexdigest(),
            migration_fingerprint=new_policy["migration_fingerprint"], rls_fingerprint=new_policy["rls_fingerprint"],
            policy_schema="V298", services_fingerprint=fingerprint(policy["services"]),
            policy_sha256=hashlib.sha256((json.dumps(new_policy, ensure_ascii=False, indent=2)+"\n").encode()).hexdigest())
        destination = os.environ.get("BATTLE_MAP_TEST_RECEIPT")
        if destination:
            Path(destination).write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n")
        print(json.dumps(receipt, ensure_ascii=False))
    finally:
        for connection in (api, worker, owner):
            if connection:
                await connection.close()
