"""V181 preserves V149 authorization while bounding participant candidates.

The oracle is the immutable previous SQL, including its nested opportunity check.
Owner access is used only to install the oracle/seed historical rows in disposable
PG fixtures. Every authorization and RLS assertion runs as the non-bypass role.
"""
from pathlib import Path
from uuid import uuid4

import pytest

from sales_backend.db import TenantInactive, set_request_context
from sales_backend.permission_context import current_feature
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_business_rankings import opportunity as make_opportunity
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import other_project, own_visit, fields_for
from tests.integration.test_operations_claims_sql import actor
from sales_backend.repositories.visits import VisitRepository

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "database/migrations"
OLD = "V149__authorization_direct_scope_fast_path.sql"
NEW = "V181__bounded_fde_participant_authorization.sql"


def definitions(filename):
    sql = (MIGRATIONS / filename).read_text()
    return sql.removeprefix("BEGIN;\n").removesuffix("\nCOMMIT;\n")


async def install_oracle(connection):
    runtime = await connection.fetchval("SELECT current_user")
    sql = definitions(OLD)
    # Keep the oracle's linked-opportunity check on the old implementation too.
    sql = sql.replace("security.has_opportunity_read_access(v.opportunity_id)",
                      "security.authorization_opportunity_before_v181('opportunity.read',v.opportunity_id)")
    for name in ("authorization_opportunity", "authorization_visit"):
        sql = sql.replace("FUNCTION security." + name + "(",
                          "FUNCTION security." + name + "_before_v181(")
    async with fixture_owner(connection):
        await connection.execute(sql)
        for name in ("authorization_opportunity", "authorization_visit"):
            signature = f"security.{name}_before_v181(text,uuid)"
            await connection.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
            await connection.execute(f'GRANT EXECUTE ON FUNCTION {signature} TO "{runtime}"')


async def fixture(connection):
    sales, project, people, team = await fde_fixture(connection)
    sibling = await other_project(connection, sales, project)
    unrelated = await make_opportunity(connection, "XS002", 50)
    first = await actor(connection, people["first"]["code"])
    lead = await actor(connection, people["lead"]["code"])
    second = await actor(connection, people["second"]["code"])
    admin = await actor(connection, "ADMIN001")
    other_sales = await actor(connection, "XS002")
    visits = [await own_visit(connection, sales, item) for item in (project, sibling)]
    await set_request_context(connection, sales)
    visits.append(await VisitRepository().create(connection, sales,
        customer_id=project["customer_id"], fields={**fields_for(project), "opportunity_id": None}))
    visits.append(await own_visit(connection, other_sales, unrelated))
    foreign, foreign_user = uuid4(), uuid4()
    async with fixture_owner(connection):
        await connection.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离权限公司')", foreign, str(foreign))
        await connection.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1", foreign)
        await connection.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) VALUES($1,$2,$3,'隔离管理员')", foreign_user, foreign, str(foreign_user))
        await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) VALUES($1,$2,'administrator','workspace')", foreign, foreign_user)
        foreign_customer = await connection.fetchval("INSERT INTO crm.customer(workspace_id,name,normalized_name,created_by_user_ref_id) VALUES($1,'隔离客户','隔离客户',$2) RETURNING id", foreign, foreign_user)
        foreign_project = await connection.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,created_by_user_ref_id) VALUES($1,$2,'隔离商机',$3,$3) RETURNING id", foreign, foreign_customer, foreign_user)
    foreign_actor = admin.model_copy(update={"workspace_id": str(foreign), "user_id": str(foreign_user)})
    return dict(sales=sales, project=project, sibling=sibling, unrelated=unrelated, first=first,
                second=second, lead=lead, admin=admin, other_sales=other_sales, team=team,
                visits=visits, foreign=foreign_actor, foreign_project=str(foreign_project))


async def bind_for_sql(connection, person):
    try:
        async with connection.transaction():
            await set_request_context(connection, person)
    except TenantInactive:
        # Request binding itself rejects an unknown/closed company before these
        # functions run. Compare their lower-level deny behavior with a cleared
        # context as well; never reuse the last successful actor's grants.
        await connection.execute("""SELECT set_config('app.workspace_id',$1,true),
          set_config('app.user_ref_id',$2,true),set_config('app.role_code',$3,true),
          set_config('app.team_ids',$4,true),set_config('app.authorization_context','',true),
          set_config('app.authorized_feature',$5,true)""",
          person.workspace_id, person.user_id, person.role.value, ','.join(person.team_ids),
          current_feature.get())


async def compare(connection, case, label):
    identities = [case[name] for name in ("first", "lead", "second", "sales", "other_sales", "admin", "foreign")]
    identities += [case["first"].model_copy(update={"workspace_id": str(uuid4())})]
    objects = {
        "opportunity": ([case[n]["id"] for n in ("project", "sibling", "unrelated")] +
                        [case["foreign_project"], str(uuid4())],
                        ["opportunity.read", "opportunity.update", "battle_map.read", "overview.read"]),
        "visit": ([v["id"] for v in case["visits"]] + [str(uuid4())],
                  ["visit.read", "visit.supplement", "battle_map.read", "overview.read"]),
    }
    for person in identities + list(reversed(identities)):
        await bind_for_sql(connection, person)
        for kind, (ids, permissions) in objects.items():
            rows = await connection.fetch(f"""
              SELECT id,permission,security.authorization_{kind}(permission,id) actual,
                security.authorization_{kind}_before_v181(permission,id) reference
              FROM unnest($1::uuid[]) id CROSS JOIN unnest($2::text[]) permission
            """, ids, permissions)
            for row in rows:
                assert row["actual"] == row["reference"], (label, person.role, dict(row))
            if person.workspace_id != case["admin"].workspace_id:
                # Foreign admin can see only the foreign object; an unknown
                # workspace sees none. Neither may inherit the preceding actor.
                assert all(not r["actual"] for r in rows if str(r["id"]) != case["foreign_project"])
    await assert_restricted(connection)


@pytest.mark.parametrize("plan_mode", ["force_custom_plan", "force_generic_plan"])
async def test_same_customer_participant_plan_matches_legacy_scopes_and_live_revocation(connection, plan_mode):
    await connection.execute("SET LOCAL statement_timeout='20s'")
    await connection.execute("SET LOCAL plan_cache_mode=" + plan_mode)
    await install_oracle(connection)
    case = await fixture(connection)
    token = current_feature.set("battle_map.read")
    try:
        await compare(connection, case, "active")
        await set_request_context(connection, case["first"])
        assert await connection.fetchval("SELECT security.authorization_opportunity('opportunity.read',$1::uuid)", case["sibling"]["id"])
        assert not await connection.fetchval("SELECT security.authorization_opportunity('opportunity.read',$1::uuid)", case["unrelated"]["id"])
        assert await connection.fetchval("SELECT security.authorization_visit('visit.read',$1::uuid)", case["visits"][1]["id"])
        mutations = [
            ("participant expired", "UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='test' WHERE opportunity_id=$1::uuid", [case["project"]["id"]]),
            ("FDE role expired", "UPDATE platform.role_binding SET valid_from=clock_timestamp()-interval '2 days',valid_to=clock_timestamp()-interval '1 day' WHERE user_ref_id=$1::uuid", [case["first"].user_id]),
            ("FDE role future", "UPDATE platform.role_binding SET valid_from=clock_timestamp()+interval '1 day' WHERE user_ref_id=$1::uuid", [case["first"].user_id]),
            ("FDE account inactive", "UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", [case["first"].user_id]),
            ("FDE membership expired", "UPDATE platform.team_membership SET valid_from=clock_timestamp()-interval '2 days',valid_to=clock_timestamp()-interval '1 day' WHERE team_id=$1::uuid", [case["team"]]),
            ("FDE department inactive", "UPDATE platform.team SET status='inactive' WHERE id=$1::uuid", [case["team"]]),
            ("participant project deleted", "UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1::uuid", [case["project"]["id"]]),
            ("shared project deleted", "UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1::uuid", [case["sibling"]["id"]]),
            ("customer deleted", "UPDATE crm.customer SET deleted_at=clock_timestamp() WHERE id=$1::uuid", [case["project"]["customer_id"]]),
            ("visit deleted", "UPDATE activity.visit SET deleted_at=clock_timestamp() WHERE id=$1::uuid", [case["visits"][1]["id"]]),
            ("company closed", "UPDATE platform.workspace SET status='closed' WHERE id=$1::uuid", [case["admin"].workspace_id]),
            ("company suspended", "UPDATE platform.workspace SET status='suspended' WHERE id=$1::uuid", [case["admin"].workspace_id]),
        ]
        for label, sql, args in mutations:
            transaction = connection.transaction()
            await transaction.start()
            try:
                async with fixture_owner(connection):
                    await connection.execute(sql, *args)
                await compare(connection, case, label)
            finally:
                await transaction.rollback()
        # Display identity remains FDE, but an additional sales appointment is
        # still part of the permission union. No result is cached by display role.
        async with fixture_owner(connection):
            await connection.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1::uuid,$2::uuid,'sales','self',$3::uuid)", case["admin"].workspace_id, case["first"].user_id, case["sales"].team_ids[0])
            await connection.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1::uuid,$2::uuid,$3::uuid,'sales')", case["admin"].workspace_id, case["first"].user_id, case["sales"].team_ids[0])
        await compare(connection, case, "sales plus FDE union")
        await grant(connection, case["admin"], case["first"].user_id, [
            dict(permission="opportunity.read", effect="deny"),
            dict(permission="visit.read", effect="deny"),
        ])
        await compare(connection, case, "explicit deny over union")
        await set_request_context(connection, case["first"])
        assert not await connection.fetchval("SELECT security.authorization_opportunity('opportunity.read',$1::uuid)", case["project"]["id"])
        assert not await connection.fetchval("SELECT security.authorization_visit('visit.read',$1::uuid)", case["visits"][0]["id"])
    finally:
        current_feature.reset(token)


async def test_replacement_preserves_function_owner_signature_acl_and_security_settings(connection):
    metadata = """SELECT oid,proname,proowner,proacl,prosecdef,proconfig,provolatile,prorettype,proargtypes
      FROM pg_proc WHERE oid IN ('security.authorization_opportunity(text,uuid)'::regprocedure,
        'security.authorization_visit(text,uuid)'::regprocedure) ORDER BY proname"""
    before = await connection.fetch(metadata)
    async with fixture_owner(connection):
        await connection.execute(definitions(OLD))
        assert await connection.fetch(metadata) == before
        await connection.execute(definitions(NEW))
    assert await connection.fetch(metadata) == before
    assert all(row["prosecdef"] and row["provolatile"] in ("s", b"s") and row["proconfig"] == ["search_path=pg_catalog"] for row in before)


async def test_fde_validity_checks_do_not_scale_with_unrelated_project_participants(connection):
    await install_oracle(connection)
    case = await fixture(connection)
    async with fixture_owner(connection):
        await connection.execute("SET LOCAL track_functions='all'")
        # Unrelated clients must not multiply FDE checks for this one client's
        # project/visit. Use real memberships and unchanged authorization logic.
        await connection.execute("""WITH projects AS (
            INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,created_by_user_ref_id)
            SELECT $1::uuid,$2::uuid,'unrelated-'||n,$3::uuid,$4::uuid,$3::uuid
            FROM generate_series(1,200) n RETURNING id,workspace_id)
          INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,participant_role,source_code,assigned_by_user_ref_id)
          SELECT workspace_id,id,$5::uuid,'fde','manual',$3::uuid FROM projects""",
          case["admin"].workspace_id, case["unrelated"]["customer_id"], case["sales"].user_id,
          case["sales"].team_ids[0], case["first"].user_id)
        await connection.execute("ANALYZE crm.opportunity_participant")
        await connection.execute("ANALYZE crm.opportunity")
    await set_request_context(connection, case["first"])
    counts, outcomes = {}, {}
    for suffix in ("_before_v181", ""):
        stats = "SELECT COALESCE(sum(calls),0) FROM pg_stat_xact_user_functions WHERE funcname='fde_user_is_active'"
        before = await connection.fetchval(stats)
        outcomes[suffix] = list(await connection.fetchrow(f"""
          SELECT security.authorization_opportunity{suffix}('opportunity.read',$1::uuid),
            security.authorization_visit{suffix}('visit.read',$2::uuid)
        """, case["sibling"]["id"], case["visits"][1]["id"]))
        counts[suffix] = (await connection.fetchval(stats)) - before
    assert outcomes[""] == outcomes["_before_v181"] == [True, True]
    assert 0 < counts[""] < counts["_before_v181"] / 10, counts
    await assert_restricted(connection)
