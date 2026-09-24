"""Three sales identities complete claims and their own visits under real RLS.

Only model output is a fixture; HTTP authorization, durable review and formal
archival use production implementations in the disposable PostgreSQL database.
"""

from uuid import uuid4

import asyncpg
import pytest
from tests.integration.feishu_fixtures import seed_fetchval

from sales_backend.config import get_settings
from sales_backend.contracts.visit_flow import canonical_fields, validate_stage_result
from sales_backend.db import set_request_context
from sales_backend.services.agent_run.handler import AgentRunHandler
from sales_backend.services.agent_run.persist import AgentRunStore
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor, create_customer
from tests.integration.test_profile_scores import business_login
from tests.integration.test_visit_entry_platform import TransactionDatabase

pytestmark = pytest.mark.asyncio


async def reviewed_visit(connection, client, subject, customer):
    database = TransactionDatabase(connection)
    handler, store = AgentRunHandler(database, get_settings()), AgentRunStore(database, get_settings())
    fields = canonical_fields(
        {
            "customer_name": customer["name"],
            "customer_type": "客户",
            "contact_name": "陈经理",
            "follow_up_record": "陈经理确认10条样本中8条通过，2条待补版本。",
            "next_action": "2026年9月18日前本人补齐样本并与陈经理复核",
            "interaction_at": "2026-09-15",
            "created_date": "2026-09-15",
        }
    )
    response = await client.post(
        "/api/v1/visit-flow/structure",
        json={
            "customer_id": customer["id"],
            "text": fields["follow_up_record"] + fields["next_action"],
        },
    )
    assert response.status_code == 202, response.text
    first = response.json()["run_id"]
    run = await handler._load_and_start(first, subject)
    facts = await handler.facts_loader.load(run)
    fields["created_date"] = facts["server_fields"]["created_date"]
    await store.persist_result(run, validate_stage_result({"fields": fields, "summary": "样本复核"}, facts), facts)
    # A structured result cannot be used as an approval to archive.
    rejected = await client.post(
        "/api/v1/visits",
        json={
            "customer_id": customer["id"],
            "fields": {
                **fields,
                "_quality_review_run_id": first,
            },
        },
    )
    assert rejected.status_code == 422, rejected.text
    response = await client.post(
        "/api/v1/visit-flow/quality",
        json={
            "customer_id": customer["id"],
            "source_run_id": first,
            "fields": fields,
            "summary": "样本复核",
        },
    )
    assert response.status_code == 202, response.text
    second = response.json()["run_id"]
    run = await handler._load_and_start(second, subject)
    facts = await handler.facts_loader.load(run)
    result = validate_stage_result(
        {
            "fields": fields,
            "summary": "样本复核",
            "quality_review": {
                "follow_up_score": 95,
                "suggestions": [],
                "next_action": {
                    "passed": True,
                    "time_found": True,
                    "goal_or_plan_found": True,
                    "suggestions": [],
                },
            },
        },
        facts,
    )
    await store.persist_result(run, result, facts)
    await set_request_context(connection, subject)
    assert (
        await connection.fetchval("SELECT count(*) FROM activity.visit WHERE customer_id=$1::uuid", customer["id"]) == 0
    )  # Neither Agent stage materializes a visit.
    body = {"customer_id": customer["id"], "fields": {**fields, "_quality_review_run_id": second}}
    response = await client.post("/api/v1/visits", json=body)
    assert response.status_code == 201, response.text
    visit = response.json()
    replay = await client.post("/api/v1/visits", json=body)
    assert replay.status_code == 201 and replay.json()["id"] == visit["id"]
    assert visit["creator_id"] == visit["recorder_id"] == subject.user_id
    assert visit["opportunity_id"] is None
    supplement = await client.patch(
        "/api/v1/visits/" + visit["id"],
        json={
            "version_no": visit["version_no"],
            "partner_name": "本人补充合作伙伴",
        },
    )
    assert supplement.status_code == 200, supplement.text
    assert supplement.json()["partner_name"] == "本人补充合作伙伴"
    return visit


@pytest.mark.parametrize("code", ["XS001", "ZJ001", "ZJL001"])
async def test_sales_identities_claim_approve_record_and_supplement(connection, code):
    customer = await create_customer(connection)
    administrator = await actor(connection, "ADMIN001")
    outside_team = str(uuid4())
    await connection.execute(
        "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1::uuid,$2::uuid,$3,$3)",
        outside_team,
        administrator.workspace_id,
        "独立业务团队" + uuid4().hex,
    )
    await connection.execute(
        "UPDATE crm.customer SET owner_team_id=$2::uuid WHERE id=$1::uuid", customer["id"], outside_team
    )
    other_owner = await connection.fetchval("SELECT id FROM platform.user_ref WHERE account_code='XS002'")
    other_opportunity = await seed_fetchval(
        connection, "INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,"
        "owner_team_id,created_by_user_ref_id) "
        "VALUES($1::uuid,$2::uuid,'他人的独立商机',$3,$4::uuid,$3) RETURNING id",
        administrator.workspace_id,
        customer["id"],
        other_owner,
        outside_team,
    )
    if code == "ZJL001":
        uid = await connection.fetchval("SELECT id FROM platform.user_ref WHERE account_code=$1", code)
        await connection.execute(
            "UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE user_ref_id=$1", uid
        )
        await connection.execute("UPDATE platform.role_binding SET team_id=NULL WHERE user_ref_id=$1", uid)
    subject = await actor(connection, code)
    if code == "ZJL001":
        assert subject.team_ids == ()
    else:
        assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer["id"])
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, code)
        home = await client.get("/api/v1/assistant/home")
        assert home.status_code == 200, home.text
        assert [item["code"] for item in home.json()["quick_actions"]] == [
            "customer_claim",
            "visit_entry",
            "management_task",
        ]
        directory = (await client.get("/api/v1/customers/claim-pool", params={"q": customer["name"]})).json()
        assert directory["items"][0]["can_claim"]
        response = await client.post("/api/v1/customers/" + customer["id"] + "/claims")
        assert response.status_code == 201, response.text
        request = response.json()
        assert request["status"] == "pending" and not request["claimed"]
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.fetchval(
                    "SELECT security.review_customer_claim($1::uuid,'approved','本人不得审批')", request["request_id"]
                )
        await actor(connection, "OPS001")
        assert await connection.fetchval(
            "SELECT owner_user_ref_id IS NULL FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"]
        )
        await connection.fetchval(
            "SELECT security.review_customer_claim($1::uuid,'approved','运营核实通过')", request["request_id"]
        )
        assert (
            str(await connection.fetchval("SELECT owner_team_id FROM crm.customer WHERE id=$1::uuid", customer["id"]))
            == outside_team
        )
        assert (
            str(
                await connection.fetchval(
                    "SELECT owner_user_ref_id FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"]
                )
            )
            == subject.user_id
        )
        detail = await client.get("/api/v1/customers/" + customer["id"])
        assert detail.status_code == 200, detail.text
        if code != "ZJL001":
            # Owning the customer does not grant another person's opportunity.
            assert not await connection.fetchval("SELECT id FROM crm.opportunity WHERE id=$1", other_opportunity)
        visit = await reviewed_visit(connection, client, subject, customer)
        if code != "ZJL001":
            await business_login(client, "ZJL001")
            denied = await client.patch(
                "/api/v1/visits/" + visit["id"],
                json={
                    "version_no": visit["version_no"],
                    "partner_name": "不可代他人补充",
                },
            )
            assert denied.status_code == 422 and "仅跟进人" in denied.text
    await actor(connection, "XS002")
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer["id"])


@pytest.mark.parametrize("code", ["XS001", "ZJ001", "ZJL001"])
async def test_claim_approval_rechecks_active_business_identity(connection, code):
    customer = await create_customer(connection)
    subject = await actor(connection, code)
    request = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    await actor(connection, "ADMIN001")
    await connection.execute(
        "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid", subject.user_id
    )
    await actor(connection, "OPS001")
    with pytest.raises(asyncpg.RaiseError, match="认领权限已失效"):
        async with connection.transaction():
            await connection.fetchval(
                "SELECT security.review_customer_claim($1::uuid,'approved','岗位已撤销')", request["request_id"]
            )
    assert (
        await connection.fetchval("SELECT state FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"])
        == "unclaimed"
    )


@pytest.mark.parametrize("person", ["first", "lead"])
async def test_fde_roles_cannot_claim_through_http_or_sql(connection, person):
    _, _, people, _ = await fde_fixture(connection)
    customer = await create_customer(connection)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people[person])
        denied = await client.post("/api/v1/customers/" + customer["id"] + "/claims")
        assert denied.status_code == 403, denied.text
        await actor(connection, people[person]["code"])
        assert not await connection.fetchval("SELECT security.can_claim_customer($1::uuid)", customer["id"])
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with connection.transaction():
                await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])


@pytest.mark.parametrize("code", ["XS001", "ZJ001", "ZJL001"])
@pytest.mark.parametrize("state", ["inactive", "foreign_workspace"])
async def test_claim_rejects_inactive_or_foreign_actor_without_formal_writes(connection, code, state):
    customer = await create_customer(connection)
    subject = await actor(connection, code)
    if state == "inactive":
        await actor(connection, "ADMIN001")
        await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", subject.user_id)
    else:
        subject = subject.model_copy(update={"workspace_id": str(uuid4())})
    await set_request_context(connection, subject)
    assert not await connection.fetchval("SELECT security.can_claim_customer($1::uuid)", customer["id"])
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    await actor(connection, "OPS001")
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM crm.customer_claim_request WHERE customer_id=$1::uuid", customer["id"]
        )
        == 0
    )
