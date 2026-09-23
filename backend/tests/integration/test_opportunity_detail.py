from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from sales_backend.repositories.customer_assets import CustomerAssetRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.opportunities import save_opportunity
from sales_backend.services.tasks import TaskService
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_customer_assets import fact
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def test_own_opportunity_detail_and_actuals_do_not_require_customer_ownership(connection):
    colleague_op = await opportunity(connection, "XS002", 900)
    customer_id = colleague_op["customer_id"]
    who = await actor(connection, "XS001")
    op = await save_opportunity(
        connection,
        who,
        customer_id=customer_id,
        data={
            "name": "另一部门的独立试点",
            "amount": Decimal(100),
            "probability": 10,
            "expected_close_date": date(2026, 12, 1),
        },
    )
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer_id)
    for i in range(22):
        await VisitRepository().create(
            connection,
            who,
            customer_id=customer_id,
            fields={
                "opportunity_id": op["id"],
                "interaction_at": "2026-09-12",
                "created_date": "2026-09-12",
                "contact_name": "独立项目联系人",
                "follow_up_record": "客户提出试点反馈" + str(i),
                "next_action": "9月20日由销售发送试点方案",
                "_follow_up_quality_score": 85,
            },
        )
    await VisitRepository().create(
        connection,
        who,
        customer_id=customer_id,
        fields={
            "interaction_at": "2026-09-12",
            "created_date": "2026-09-12",
            "contact_name": "客户通用联系人",
            "follow_up_record": "不关联商机的一次客户拜访",
            "next_action": "9月20日由销售回访",
            "_follow_up_quality_score": 85,
        },
    )
    task = await TaskService().create(
        connection,
        actor=who,
        description="请确认当前商机试点计划",
        due_at=datetime.now(UTC) + timedelta(days=1),
        priority_code="medium",
        target_position="self",
        customer_id=customer_id,
        opportunity_id=op["id"],
    )
    manager = await actor(connection, "ZJL001")
    assets = CustomerAssetRepository()
    customer = {"id": customer_id}
    await assets.create(connection, manager, fact(customer, amount=Decimal(9999)))
    await assets.create(
        connection, manager, fact(customer, opportunity_id=UUID(colleague_op["id"]), amount=Decimal(800))
    )
    await assets.create(connection, manager, fact(customer, opportunity_id=UUID(op["id"]), amount=Decimal(125)))
    who = await actor(connection, "XS001")
    assert await CustomerRepository().detail(connection, customer_id=customer_id) is None
    detail = await OpportunityRepository().detail(connection, who, op["id"])
    assert len(detail["opportunities"]) == 1 and len(detail["visits"]) == 22
    assert detail["opportunities"][0]["actuals"]["recognized_amount"] == 125
    assert detail["opportunities"][0]["owner_id"] == who.user_id
    assert detail["tasks"][0]["id"] == task["id"] and detail["tasks"][0]["requires_action"]
    assert len(detail["tasks"][0]["candidates"]) == 1
    assert colleague_op["id"] not in str(detail) and "不关联商机的一次客户拜访" not in str(detail)
    ledger = await assets.read(connection, customer_id=customer_id, opportunity_id=op["id"], period="all")
    assert ledger["summary"]["recognized_amount"] == 125 and len(ledger["items"]) == 1
    company_customer_view = await assets.read(connection, customer_id=customer_id, period="all")
    assert company_customer_view["summary"]["recognized_amount"] == 125
    assert await OpportunityRepository().detail(connection, who, colleague_op["id"]) is None
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get(f"/api/v1/opportunities/{op['id']}/detail")
        assert response.status_code == 200, response.text
        assert response.json()["opportunities"][0]["id"] == op["id"]
        assert (await client.get(f"/api/v1/opportunities/{colleague_op['id']}/detail")).status_code == 404
        assert (await client.get(f"/api/v1/customers/{customer_id}")).status_code == 404
    await actor(connection, "XS002")
    assert await OpportunityRepository().detail(connection, await actor(connection, "XS002"), op["id"]) is None


async def test_supervisor_nonprimary_team_receives_linked_opportunity_task(connection):
    op = await opportunity(connection, "XS001", 100)
    supervisor = await actor(connection, "ZJ001")
    manager = await actor(connection, "ZJL001")
    admin = await actor(connection, "ADMIN001")
    another_team = str(uuid4())
    await connection.execute(
        "INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1::uuid,$2::uuid,$3,$4)",
        another_team,
        admin.workspace_id,
        "test-" + uuid4().hex,
        "隔离第二部门",
    )
    await connection.execute(
        "UPDATE platform.team_membership SET is_primary=false WHERE user_ref_id=$1::uuid", supervisor.user_id
    )
    await connection.execute(
        "INSERT INTO platform.team_membership(workspace_id,team_id,user_ref_id,membership_role,is_primary) "
        "VALUES($1::uuid,$2::uuid,$3::uuid,'supervisor',true)",
        admin.workspace_id,
        another_team,
        supervisor.user_id,
    )
    manager = await actor(connection, "ZJL001")
    task = await TaskService().create(
        connection,
        actor=manager,
        description="主管确认本项目试点计划",
        assignee_account_code="ZJ001",
        customer_id=op["customer_id"],
        opportunity_id=op["id"],
        due_at=datetime.now(UTC) + timedelta(days=1),
        priority_code="medium",
    )
    assert task["assignees"][0]["team_id"] != another_team
    current = await actor(connection, "ZJ001")
    accepted = await TaskService().apply_event(
        connection, actor=current, task_id=task["id"], event_type="accept", note=None
    )
    assert accepted["status"] == "pending_execution"
