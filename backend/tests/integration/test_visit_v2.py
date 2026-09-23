from tests.integration.provision import create_owned_customer
from uuid import uuid4

import pytest

from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.visits import VisitRepository

pytestmark = pytest.mark.asyncio


async def test_create_without_opportunity_and_supplement_roundtrip(connection, sales_actor):
    team = await connection.fetchval("SELECT name FROM platform.team WHERE id=$1::uuid", sales_actor.team_ids[0])
    before = await connection.fetchval("SELECT count(*) FROM crm.opportunity")
    customer = await create_owned_customer(
        connection,
        sales_actor,
        data={
            "name": f"事务回滚验收-{uuid4()}",
            "industry": "",
            "customer_type": "潜在客户",
            "level_code": "Tier-2",
            "source": "销售线索",
            "target_team": team,
            "partner_name": "",
            "contact_name": "王经理",
            "contact_title": "产品经理",
            "contact_role": "使用者",
        },
    )
    assert await connection.fetchval("SELECT count(*) FROM crm.opportunity") == before
    repo = VisitRepository()
    fields = dict(
        visit_goal="确认试点需求",
        contact_name="王经理",
        interaction_at="2026-09-10",
        created_date="2026-09-10",
        follow_up_record="客户同意先验证搜索准确率",
        next_action="周五由销售安排演示",
        _follow_up_quality_score=85,
        collaborator_ids=[sales_actor.user_id],
    )
    record = await repo.create(connection, sales_actor, customer_id=customer["id"], fields=fields)
    assert record["opportunity_id"] is None
    assert await connection.fetchval("SELECT count(*) FROM crm.opportunity") == before
    detail = await repo.detail(connection, record["id"])
    receipts = await AssistantRepository().archived_visits(connection, sales_actor)
    receipt = next(r for r in receipts if r["id"] == record["id"])
    assert receipt["score"] == 85 and receipt["grade"] == "良好"
    assert receipt["completed_count"] == record["completed_count"]
    assert receipt["opportunity_name"] is None
    assert len({r["id"] for r in receipts}) == len(receipts)
    assert detail["visit_goal"] == fields["visit_goal"]
    assert detail["visit_date"] == "2026-09-10"
    assert isinstance(detail["within_seven_days"], bool)
    assert detail["customer_type"] == "潜在客户"
    assert detail["creator_id"] == detail["recorder_id"] == sales_actor.user_id
    await connection.execute("UPDATE crm.customer SET customer_type_code='won' WHERE id=$1::uuid", customer["id"])
    assert (await repo.detail(connection, record["id"]))["customer_type"] == "潜在客户"
    updated = await repo.supplement(
        connection, sales_actor, record["id"], {"version_no": detail["version_no"], "partner_name": "演示伙伴"}
    )
    assert updated["partner_name"] == "演示伙伴"
    assert updated["created_at"] == detail["created_at"]
    dated = await repo.supplement(
        connection, sales_actor, record["id"], {"version_no": updated["version_no"], "interaction_at": "2025-12-31"}
    )
    assert dated["visit_date"] == "2025-12-31"
    assert dated["created_at"] == detail["created_at"]
    assert dated["creator_id"] == detail["creator_id"]
    with pytest.raises(ValueError, match="记录已更新"):
        await repo.supplement(
            connection, sales_actor, record["id"], {"version_no": detail["version_no"], "partner_name": "覆盖"}
        )
    with pytest.raises(ValueError, match="正文变化"):
        await repo.supplement(connection, sales_actor, record["id"], {"visit_goal": "改正文"})
    with pytest.raises(ValueError, match="商机不属于"):
        await repo.create(
            connection, sales_actor, customer_id=customer["id"], fields={**fields, "opportunity_id": str(uuid4())}
        )
    with pytest.raises(ValueError, match="协同人"):
        await repo.create(
            connection, sales_actor, customer_id=customer["id"], fields={**fields, "collaborator_ids": [str(uuid4())]}
        )


async def test_non_owned_customer_visit_remains_visible_to_recorder_only(connection):
    from tests.integration.test_operations_claims_sql import create_customer, actor
    customer = await create_customer(connection)
    sales = await actor(connection, "XS001")
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer["id"])
    repo = VisitRepository()
    fields = dict(interaction_at="2026-09-11", created_date="2026-09-11", contact_name="测试联系人", follow_up_record="客户确认需要先完成一轮试点再评估采购", next_action="9月15日由销售反馈验证方案", _follow_up_quality_score=85)
    record = await repo.create(connection, sales, customer_id=customer["id"], fields=fields)
    detail = await repo.detail(connection, record["id"])
    assert detail and detail["customer_name"] == customer["name"]
    receipts = await AssistantRepository().archived_visits(connection, sales)
    assert next(x for x in receipts if x["id"] == record["id"])["customer_name"] == customer["name"]
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer["id"])
    await actor(connection, "XS002")
    with pytest.raises(LookupError):
        await repo.detail(connection, record["id"])
