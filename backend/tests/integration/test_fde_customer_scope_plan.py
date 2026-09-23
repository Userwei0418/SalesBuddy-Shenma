"""The optimized customer read predicate remains the V069 authorization rule.

Only the outer customer predicate changed. The reference invokes the unchanged
is_fde_actor / fde_opportunity_in_scope functions on the complete customer project
IDs read by the administrator, avoiding a reference accidentally filtered by the
new customer RLS predicate itself. Every fixture is transaction-local.
"""

import asyncio
import os
import re
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("plan_mode", ["force_custom_plan", "force_generic_plan"])
async def test_customer_scope_plan_reuse_rechecks_identity_and_live_grants(connection, plan_mode):
    async with asyncio.timeout(60):
        await connection.execute("SET LOCAL statement_timeout='20s'")
        await connection.execute("SET LOCAL plan_cache_mode=" + plan_mode)
        sales, project, people, team = await fde_fixture(connection)
        admin = await actor(connection, "ADMIN001")
        accounts = OperationsAccountRepository()
        other_team = await accounts.save_department(connection, admin, None, {
            "code": "OTHER-" + uuid4().hex[:8], "name": "Unrelated FDE department",
            "status": "active", "parent_team_id": None,
        })
        outsiders = []
        for role in ("fde", "fde_lead"):
            code = "FD" + uuid4().hex[:12].upper()
            await accounts.create(connection, admin, {
                "account_code": code, "display_name": "Outside department",
                "roles": [role], "team_id": other_team["id"],
            })
            outsiders.append(await actor(connection, code))
            await set_request_context(connection, admin)
        first = await actor(connection, people["first"]["code"])
        second = await actor(connection, people["second"]["code"])
        lead = await actor(connection, people["lead"]["code"])
        other_sales = await actor(connection, "XS002")
        identities = [first, lead, sales, other_sales, *outsiders, second,
                      lead.model_copy(update={"workspace_id": str(uuid4())})]
        prepared = await connection.prepare("""
          SELECT security.fde_customer_in_scope($1::uuid) AS actual,
            (security.is_fde_actor() AND EXISTS (
              SELECT 1 FROM unnest($2::uuid[]) project_id
              WHERE security.fde_opportunity_in_scope(project_id))) AS reference
        """)

        async def check(scenario):
            await set_request_context(connection, admin)
            ids = [str(row["id"]) for row in await connection.fetch(
                "SELECT id FROM crm.opportunity WHERE workspace_id=$1::uuid AND customer_id=$2::uuid "
                "AND deleted_at IS NULL", admin.workspace_id, project["customer_id"])]
            # Alternate identities on the same prepared statement, twice. A
            # reused plan must not retain the preceding actor's granted result.
            for person in identities + list(reversed(identities)):
                await set_request_context(connection, person)
                result = await prepared.fetchrow(project["customer_id"], ids)
                assert result["actual"] == result["reference"], (scenario, person.role, result)
                if person in outsiders or person.workspace_id != admin.workspace_id:
                    assert result["actual"] is False
            await set_request_context(connection, first)
            assert not await connection.fetchval("SELECT security.fde_customer_in_scope($1)", uuid4())

        await check("active")
        await connection.execute("SELECT set_config('app.role_code','',true),set_config('app.user_ref_id','',true)")
        assert not await connection.fetchval("SELECT security.fde_customer_in_scope($1::uuid)", project["customer_id"])
        mutations = [
            ("actor inactive", "UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", [lead.user_id]),
            ("actor deleted", "UPDATE platform.user_ref SET deleted_at=clock_timestamp() WHERE id=$1::uuid", [lead.user_id]),
            ("members inactive", "UPDATE platform.user_ref SET status='inactive' WHERE id=ANY($1::uuid[])", [[first.user_id, second.user_id]]),
            ("department inactive", "UPDATE platform.team SET status='inactive' WHERE id=$1::uuid", [team]),
            ("department deleted", "UPDATE platform.team SET deleted_at=clock_timestamp() WHERE id=$1::uuid", [team]),
            ("department expired", "UPDATE platform.team SET valid_from=clock_timestamp()-interval '2 days',valid_to=clock_timestamp()-interval '1 day' WHERE id=$1::uuid", [team]),
            ("department future", "UPDATE platform.team SET valid_from=clock_timestamp()+interval '1 day' WHERE id=$1::uuid", [team]),
            ("role expired", "UPDATE platform.role_binding SET valid_from=clock_timestamp()-interval '2 days',valid_to=clock_timestamp()-interval '1 day' WHERE user_ref_id=$1::uuid", [lead.user_id]),
            ("role future", "UPDATE platform.role_binding SET valid_from=clock_timestamp()+interval '1 day' WHERE user_ref_id=$1::uuid", [lead.user_id]),
            ("membership expired", "UPDATE platform.team_membership SET valid_from=clock_timestamp()-interval '2 days',valid_to=clock_timestamp()-interval '1 day' WHERE team_id=$1::uuid", [team]),
            ("membership future", "UPDATE platform.team_membership SET valid_from=clock_timestamp()+interval '1 day' WHERE team_id=$1::uuid", [team]),
            ("role department mismatch", "UPDATE platform.role_binding SET team_id=$2::uuid WHERE user_ref_id=$1::uuid", [lead.user_id, other_team["id"]]),
            ("leader membership mismatch", "UPDATE platform.team_membership SET membership_role='fde' WHERE user_ref_id=$1::uuid", [lead.user_id]),
            ("unscoped role binding", "UPDATE platform.role_binding SET team_id=NULL WHERE user_ref_id=$1::uuid", [lead.user_id]),
            ("participants removed", "UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='test' WHERE opportunity_id=$1::uuid", [project["id"]]),
            ("participants future", """WITH ended AS (
                UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='test'
                WHERE opportunity_id=$1::uuid RETURNING workspace_id,opportunity_id,user_ref_id)
                INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,
                  participant_role,source_code,assigned_by_user_ref_id,valid_from)
                SELECT workspace_id,opportunity_id,user_ref_id,'fde','manual',common.current_user_ref_id(),
                  clock_timestamp()+interval '1 day' FROM ended""", [project["id"]]),
            ("project deleted", "UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1::uuid", [project["id"]]),
            # The helper itself deliberately does not check customer.deleted_at;
            # the surrounding customer/opportunity read predicates still do.
            ("customer deleted", "UPDATE crm.customer SET deleted_at=clock_timestamp() WHERE id=$1::uuid", [project["customer_id"]]),
        ]
        for scenario, sql, args in mutations:
            transaction = connection.transaction()
            await transaction.start()
            try:
                await set_request_context(connection, admin)
                if scenario in {"project deleted", "customer deleted"}:
                    # The runtime write policy correctly disallows producing
                    # these deleted rows. Only the disposable test DB owner
                    # seeds historical soft-deletion; all assertions below run
                    # as the original non-bypass role. Never do this on a DSN
                    # without the isolated integration bootstrap.
                    runtime = os.environ.get("SALES_TEST_ROLE", "")
                    assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", runtime)
                    assert re.fullmatch(r"salegent_verify_integration_[a-f0-9]+",
                                        await connection.fetchval("SELECT current_database()"))
                    await connection.execute("RESET ROLE")
                    try:
                        await connection.execute(sql, *args)
                    finally:
                        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
                    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
                else:
                    await connection.execute(sql, *args)
                await check(scenario)
            finally:
                await transaction.rollback()
            await check("restored " + scenario)
