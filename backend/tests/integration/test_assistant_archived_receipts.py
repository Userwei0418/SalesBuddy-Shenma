"""Customer-linked home receipts must tolerate partner-only imported history.

The seeds model approved historical records; HTTP/repository reads use the
non-bypass runtime role and every record rolls back with the test transaction.
"""

from uuid import uuid4

import pytest

from sales_backend.contracts.models import ArchivedVisitSummary
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.visits import VisitRepository
from tests.integration.feishu_fixtures import assert_restricted, fixture_owner
from tests.integration.provision import create_owned_customer, create_partner
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def customer_for(connection, person):
    return await create_owned_customer(connection, person, data={
        "name": "首页回执隔离客户-" + uuid4().hex,
        "industry": "", "customer_type": "潜在客户", "level_code": "Tier-2",
        "source": "销售线索", "target_team": "南区", "partner_name": "",
        "contact_name": "隔离联系人", "contact_title": "经理", "contact_role": "使用者",
    })


async def history(connection, person, *, customer_id=None, partner_id=None, ordinal=0, importer=None,
                  next_action="历史下一步计划", archived_fields=None):
    previous_import = await connection.fetchval("SELECT current_setting('app.feishu_historical_import',true)")
    try:
        async with fixture_owner(connection):
            if importer is None:
                importer = await actor(connection, "OPS001")
            else:
                await set_request_context(connection, importer)
            assert importer.workspace_id == person.workspace_id
            await connection.execute("SELECT set_config('app.feishu_historical_import','on',true)")
            assert await connection.fetchval("SELECT security.management_actor()")
            return await connection.fetchval(
                """INSERT INTO activity.visit(
          workspace_id,customer_id,partner_id,recorder_user_ref_id,recorder_team_id,
          created_by_user_ref_id,form_version_id,status,interaction_at,archived_at,
          follow_up_record,next_action,archived_fields,import_meta)
        VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$7::uuid,
          (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
          'archived','2026-09-01T00:00Z'::timestamptz,
          '2026-09-27T00:00Z'::timestamptz+$6::int*interval '1 second',
          '已确认的历史跟进正文',$8,$9::jsonb,'{"import_type":"crm_history"}')
        RETURNING id::text""",
                person.workspace_id, customer_id, partner_id, person.user_id,
                person.team_ids[0], ordinal, importer.user_id, next_action, archived_fields or {},
            )
    finally:
        await connection.execute("SELECT set_config('app.feishu_historical_import',$1,true)", previous_import or "")
        await set_request_context(connection, person)


async def test_home_excludes_partner_only_history_before_limit_without_losing_history(connection, sales_actor):
    customer = await customer_for(connection, sales_actor)
    partner = await create_partner(connection, sales_actor, "首页历史伙伴-" + uuid4().hex)
    customers, partners = [], []
    for n in range(25):
        customers.append(await history(connection, sales_actor, customer_id=customer["id"], ordinal=n * 2))
        partners.append(await history(connection, sales_actor, partner_id=partner["id"], ordinal=n * 2 + 1))
    await assert_restricted(connection)
    # This is legitimate readable history, not corrupt rows to delete or reparent.
    historical = await VisitRepository().detail(connection, partners[-1])
    assert historical["customer_id"] is None and historical["partner_id"] == str(partner["id"])
    assert historical["partner_name"] == partner["name"]

    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get("/api/v1/assistant/home")
        assert response.status_code == 200, response.text
        receipts = response.json()["archived_visits"]
        assert [row["id"] for row in receipts] == list(reversed(customers))[:20]
        assert all(ArchivedVisitSummary.model_validate(row).customer_id == str(customer["id"]) for row in receipts)
        assert all(row["customer_name"] == customer["name"] for row in receipts)
        # The original detail URL still resolves the same partner-only record.
        detail = await client.get("/api/v1/visits/" + partners[-1])
        assert detail.status_code == 200, detail.text
        assert detail.json()["customer_id"] is None
        assert detail.json()["partner_id"] == str(partner["id"])
    await set_request_context(connection, sales_actor)
    assert await connection.fetchval(
        "SELECT count(*) FROM activity.visit WHERE id=ANY($1::uuid[])", partners,
    ) == 25


async def test_home_receipts_stay_scoped_to_recorder_even_when_other_history_is_readable(connection, sales_actor):
    customer = await customer_for(connection, sales_actor)
    mine = await history(connection, sales_actor, customer_id=customer["id"])
    colleague = await actor(connection, "XS002")
    theirs = await history(connection, colleague, customer_id=customer["id"], ordinal=1)
    manager = await actor(connection, "ZJL001")
    assert await VisitRepository().detail(connection, mine)
    assert await VisitRepository().detail(connection, theirs)
    assert await AssistantRepository().archived_visits(connection, manager) == []
    for person, expected in [(sales_actor, mine), (colleague, theirs)]:
        await set_request_context(connection, person)
        receipts = await AssistantRepository().archived_visits(connection, person)
        assert [row["id"] for row in receipts] == [expected]


@pytest.mark.parametrize("next_action", [None, "", "  9月30日由销售确认客户试点范围。  "])
async def test_home_preserves_legacy_string_contract_without_filling_historical_facts(
    connection, sales_actor, next_action,
):
    customer = await customer_for(connection, sales_actor)
    visit_id = await history(connection, sales_actor, customer_id=customer["id"], next_action=next_action)
    await assert_restricted(connection)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get("/api/v1/assistant/home")
        assert response.status_code == 200, response.text
        receipt = next(item for item in response.json()["archived_visits"] if item["id"] == visit_id)
        assert receipt["next_action"] == ("" if next_action is None else next_action)
        assert receipt["score"] is None and receipt["grade"] == "已归档"
        ArchivedVisitSummary.model_validate(receipt)
        detail = await client.get("/api/v1/visits/" + visit_id)
        assert detail.status_code == 200, detail.text
        assert detail.json()["next_action"] == next_action
        assert detail.json()["quality_review"] is None and detail.json()["follow_up_score"] is None
    await set_request_context(connection, sales_actor)
    persisted = await connection.fetchrow(
        "SELECT next_action,follow_up_score,quality_review FROM activity.visit WHERE id=$1::uuid", visit_id,
    )
    assert dict(persisted) == {"next_action": next_action, "follow_up_score": None, "quality_review": None}


@pytest.mark.parametrize("snapshot_name", [None, "归档时的客户名称"])
async def test_home_customer_name_tolerates_reference_revocation_and_preserves_snapshot(
    connection, sales_actor, snapshot_name,
):
    customer = await customer_for(connection, sales_actor)
    archived_fields = {} if snapshot_name is None else {"customer_name": snapshot_name}
    visit_id = await history(connection, sales_actor, customer_id=customer["id"], archived_fields=archived_fields)
    before = await AssistantRepository().archived_visits(connection, sales_actor)
    assert before[0]["customer_name"] == (customer["name"] if snapshot_name is None else snapshot_name)
    admin = await actor(connection, "ADMIN001")
    await grant(connection, admin, sales_actor.user_id, [dict(permission="customer.reference", effect="deny")])
    await set_request_context(connection, sales_actor)
    await assert_restricted(connection)
    assert await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer["id"]) is None
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get("/api/v1/assistant/home")
        assert response.status_code == 200, response.text
        receipt = next(item for item in response.json()["archived_visits"] if item["id"] == visit_id)
        assert receipt["customer_id"] == str(customer["id"])
        assert receipt["customer_name"] == ("" if snapshot_name is None else snapshot_name)
        ArchivedVisitSummary.model_validate(receipt)
        detail = await client.get("/api/v1/visits/" + visit_id)
        assert detail.status_code == 200, detail.text
        assert detail.json()["archived_fields"] == archived_fields
    await set_request_context(connection, sales_actor)
    assert await connection.fetchval("SELECT archived_fields FROM activity.visit WHERE id=$1::uuid", visit_id) == archived_fields
    assert await connection.fetchval("SELECT security.customer_reference($1::uuid)", customer["id"]) is None


async def test_home_receipts_do_not_cross_tenant_boundary(connection, sales_actor):
    customer = await customer_for(connection, sales_actor)
    mine = await history(connection, sales_actor, customer_id=customer["id"])
    workspace, team, user, customer_id, admin_id = (uuid4() for _ in range(5))
    async with fixture_owner(connection):
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'其他隔离公司')",
            workspace, "receipt-" + uuid4().hex,
        )
        await connection.execute(
            "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'south','南区')", team, workspace,
        )
        await connection.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) "
            "VALUES($1,$2,'XS001','其他公司销售','XS001')", user, workspace,
        )
        await connection.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) "
            "VALUES($1,$2,'sales','self',$3)", workspace, user, team,
        )
        await connection.execute(
            "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) "
            "VALUES($1,$2,'ADMIN','其他公司管理员','ADMIN')", admin_id, workspace,
        )
        await connection.execute(
            "INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code) "
            "VALUES($1,$2,'administrator','workspace')", workspace, admin_id,
        )
        await connection.execute(
            "INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) "
            "VALUES($1,$2,$3,'sales')", workspace, user, team,
        )
        await connection.execute(
            "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,"
            "created_by_user_ref_id,level_code) VALUES($1,$2,'其他公司客户','其他公司客户',$3,$4,$3,'Tier-2')",
            customer_id, workspace, user, team,
        )
    foreign = ActorContext(workspace_id=str(workspace), user_id=str(user), role=RoleCode.SALES,
                           data_scope=DataScope.SELF, team_ids=(str(team),))
    importer = ActorContext(workspace_id=str(workspace), user_id=str(admin_id), role=RoleCode.ADMINISTRATOR,
                            data_scope=DataScope.WORKSPACE)
    theirs = await history(connection, foreign, customer_id=str(customer_id), importer=importer)
    await assert_restricted(connection)
    for person, expected, excluded in [(sales_actor, mine, theirs), (foreign, theirs, mine)]:
        await set_request_context(connection, person)
        assert [row["id"] for row in await AssistantRepository().archived_visits(connection, person)] == [expected]
        with pytest.raises(LookupError):
            await VisitRepository().detail(connection, excluded)
    await set_request_context(connection, sales_actor)
    # Supplying a different actor argument cannot replace the SQL RLS identity.
    assert await AssistantRepository().archived_visits(connection, foreign) == []
