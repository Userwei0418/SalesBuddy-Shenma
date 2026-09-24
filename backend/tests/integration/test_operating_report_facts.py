"""Execute report fact SQL with real RLS, including customer/owner separation."""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.services.agent_run.facts import AgentFactsLoader
from sales_backend.services.agent_run.models import RunInput

from .provision import create_owned_customer


def report_run(actor):
    return RunInput(str(uuid4()), str(uuid4()), "当前即时总结", "operating_report", None, actor)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER])
async def test_operating_report_executes_all_fact_queries(connection, actor_factory, role):
    actor = await actor_factory(role)
    facts = await AgentFactsLoader(None)._load_operating_report_facts(connection, report_run(actor))
    assert facts["scope"]["role"] == role.value
    assert all(isinstance(facts[key], list) for key in ("visits", "tasks", "risks", "opportunities"))


@pytest.mark.asyncio
async def test_operating_report_keeps_owned_opportunity_without_full_customer_access(connection, sales_actor):
    record = await IdentityRepository().find_actor_by_account(
        connection, workspace_external_id="demo-sales-workspace", account_code="XS002"
    )
    assert record
    customer = await create_owned_customer(
        connection,
        record.context,
        data={
            "name": "报告权限回归" + uuid4().hex,
            "industry": "软件",
            "customer_type": "潜在客户",
            "level_code": "Tier-2",
            "source": "销售线索",
            "target_team": "南区",
            "partner_name": "",
            "contact_name": "合成联系人",
            "contact_title": "经理",
            "contact_role": "决策者",
        },
    )
    await set_request_context(connection, sales_actor)
    assert not await connection.fetchval("SELECT id FROM crm.customer WHERE id=$1::uuid", customer["id"])
    own_id = str(uuid4())
    await connection.execute(
        """INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,created_by_user_ref_id,owner_team_id)
        VALUES($1::uuid,$2::uuid,$3::uuid,'合成本人商机',$4::uuid,$4::uuid,$5::uuid)""",
        own_id,
        sales_actor.workspace_id,
        customer["id"],
        sales_actor.user_id,
        sales_actor.team_ids[0],
    )
    await set_request_context(connection, record.context)
    other_id = str(uuid4())
    await connection.execute(
        """INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,owner_user_ref_id,created_by_user_ref_id,owner_team_id)
        VALUES($1::uuid,$2::uuid,$3::uuid,'合成他人商机',$4::uuid,$4::uuid,$5::uuid)""",
        other_id,
        record.context.workspace_id,
        customer["id"],
        record.context.user_id,
        record.context.team_ids[0],
    )
    await set_request_context(connection, sales_actor)
    facts = await AgentFactsLoader(None)._load_operating_report_facts(connection, report_run(sales_actor))
    by_id = {item["id"]: item for item in facts["opportunities"]}
    assert own_id in by_id and other_id not in by_id
    assert by_id[own_id]["customer_id"] == customer["id"]
    assert by_id[own_id]["customer_name"] == customer["name"]
    assert not await connection.fetchval("SELECT id FROM crm.customer WHERE id=$1::uuid", customer["id"])
