from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from sales_backend.repositories.business_activity import BusinessActivityRepository
from sales_backend.repositories.operations_logs import OperationsLogRepository
from sales_backend.repositories.visit_imports import create_import
from sales_backend.repositories.visits import VisitRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata
from sales_backend.services.operations_accounts import OperationsAccountService
from sales_backend.services.opportunities import save_opportunity
from sales_backend.services.tasks import TaskService
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor, create_customer

pytestmark = pytest.mark.asyncio
repo = BusinessActivityRepository()


async def rows(connection, **filters):
    now = datetime.now(timezone.utc)
    return await repo.list(connection, start=now - timedelta(days=2), end=now + timedelta(days=2), **filters)


async def test_business_projection_skips_http_polling_and_enforces_management(connection):
    customer = await create_customer(connection)
    ops = await actor(connection, "OPS001")
    before = await rows(connection)
    for _ in range(3):
        await OperationsLogRepository().record_request(connection, ops, method="GET", path="/api/v1/tasks", status=200)
    assert (await rows(connection))["total"] == before["total"]
    customer_events = await rows(connection, q=customer["name"])
    assert [r["action_code"] for r in customer_events["items"]] == ["customer.create"]
    empty_page = await rows(connection, q=customer["name"], offset=50)
    assert empty_page["total"] == 1 and empty_page["items"] == []
    await actor(connection, "XS001")
    assert (await rows(connection))["total"] == 0
    with pytest.raises(LookupError):
        await repo.detail(connection, "customer:" + customer["id"])


async def test_upload_processing_archive_provenance_and_private_paths(connection):
    customer = await create_customer(connection)
    sales = await actor(connection, "XS001")
    import_id = str(uuid4())
    await create_import(connection, sales, import_id, "来源核对.md", "/private/hidden-path", 42)
    await connection.execute(
        "UPDATE activity.visit_import SET status='succeeded',extracted_text='测试材料' WHERE id=$1::uuid", import_id
    )
    fields = dict(
        interaction_at="2026-09-11",
        created_date="2026-09-11",
        contact_name="测试联系人",
        follow_up_record="客户同意先评估试点效果",
        next_action="9月15日由销售发送方案",
        _follow_up_quality_score=85,
        source_import_id=import_id,
    )
    visit = await VisitRepository().create(connection, sales, customer_id=customer["id"], fields=fields)
    await actor(connection, "OPS001")
    upload = await repo.detail(connection, "upload:" + import_id)
    assert upload["result_label"] == "已关联归档" and len(upload["details"]["visits"]) == 1
    assert "/private/hidden-path" not in str(upload)
    archived = await repo.detail(connection, "visit:" + visit["id"])
    assert (
        archived["action_label"] == "确认归档拜访" and archived["details"]["materials"][0]["filename"] == "来源核对.md"
    )
    material_events = await rows(connection, q="来源核对.md")
    processing = next(r for r in material_events["items"] if r["action_code"] == "material.process")
    assert processing["execution_kind"] == "system"
    assert len({r["event_id"] for r in material_events["items"]}) == len(material_events["items"])


async def test_claim_repeat_approval_and_release_are_single_business_events(connection):
    customer = await create_customer(connection)
    await actor(connection, "XS001")
    request = await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    await connection.fetchval("SELECT security.claim_customer($1::uuid)", customer["id"])
    await actor(connection, "OPS001")
    await connection.fetchval(
        "SELECT security.review_customer_claim($1::uuid,'approved','核实通过')", request["request_id"]
    )
    version = await connection.fetchval(
        "SELECT version_no FROM crm.customer_ownership WHERE customer_id=$1::uuid", customer["id"]
    )
    await connection.fetchval("SELECT security.release_customer($1::uuid,$2,'运营交接')", customer["id"], version)
    found = await rows(connection, q=customer["name"], category="claim")
    assert sorted(r["action_code"] for r in found["items"]) == ["claim.approved", "claim.released", "claim.request"]


async def test_opportunity_changes_and_tasks_have_human_business_semantics(connection):
    customer = await create_customer(connection)
    sales = await actor(connection, "XS001")
    ops = await actor(connection, "OPS001")
    from tests.integration.provision import authorize_opportunity_operations
    await authorize_opportunity_operations(connection, ops)
    data = dict(
        name="活动商机" + uuid4().hex,
        amount=Decimal("200000"),
        probability=10,
        status="open",
        expected_close_date=date(2027, 3, 12),
        owner_user_ref_id=sales.user_id,
    )
    saved = await save_opportunity(connection, ops, customer_id=customer["id"], data=data)
    await save_opportunity(
        connection,
        ops,
        customer_id=customer["id"],
        data={
            **data,
            "action": "update",
            "opportunity_id": saved["id"],
            "version_no": saved["version_no"],
            "probability": 50,
            "quarterly_forecasts": [dict(year=2027,quarter=1,recognized_amount=Decimal('10000'),collection_amount=Decimal('0'))],
        },
    )
    found = await rows(connection, q=data["name"])
    assert sorted(r["action_code"] for r in found["items"]) == ["opportunity.create", "opportunity.update"]
    assert any("50%" in r["summary"] for r in found["items"])
    supervisor = await actor(connection, "ZJ001")
    task = await TaskService().create(
        connection,
        actor=supervisor,
        description="活动流转" + uuid4().hex,
        assignee_account_code="XS001",
        due_at=datetime.now(timezone.utc) + timedelta(days=3),
        priority_code="medium",
    )
    sales = await actor(connection, "XS001")
    await TaskService().apply_event(
        connection, actor=sales, task_id=task["id"], event_type="reject", note="测试资源不足"
    )
    await actor(connection, "OPS001")
    found = await rows(connection, category="task")
    events = [r for r in found["items"] if str(r["object_id"]) == task["id"]]
    assert sorted(r["action_label"] for r in events) == sorted(["下发任务", "拒绝任务"])


async def test_account_grouping_snapshot_and_export_http(connection):
    token = request_metadata.set(RequestMetadata(str(uuid4())))
    try:
        ops = await actor(connection, "OPS001")
        account = await OperationsAccountService().create(
            connection,
            ops,
            dict(
                account_code="ACT" + uuid4().hex[:12],
                display_name="记录核对账号",
                team_id=ops.team_ids[0],
                roles=["sales"],
            ),
            "Isolated-Audit-2026",
        )
        found = await rows(connection, category="account", q="记录核对账号")
        assert found["total"] == 1 and found["items"][0]["action_label"] == "开通账号"
        assert found["items"][0]["actor_name"] == "OPS001" and found["items"][0]["actor_department"] == "南区"
        assert "password_hash" not in str(found)
        assert str(found["items"][0]["object_id"]) == account["id"]
        await connection.execute(
            "UPDATE platform.user_ref SET display_name='运营新姓名' WHERE id=$1::uuid", ops.user_id
        )
        assert (await rows(connection, category="account", q="记录核对账号"))["items"][0]["actor_name"] == "OPS001"
    finally:
        request_metadata.reset(token)
    async with await client_for(connection) as client:
        await sign_in(client)
        response = await client.get("/api/v1/console/activities/export?category=account&q=记录核对账号")
        assert response.status_code == 200 and response.headers["x-export-count"] == "1"
        found = (await client.get("/api/v1/console/activities?category=data")).json()
        exported = next(r for r in found["items"] if r["object_name"] == "业务操作记录")
        assert "导出条数：1" in exported["summary"] and "记录核对账号" in exported["summary"]
        assert (await client.get("/api/v1/console/activities?category=invalid")).status_code == 422


async def test_result_and_current_department_filters(connection):
    sales = await actor(connection, "XS001")
    import_id = str(uuid4())
    await create_import(connection, sales, import_id, "失败材料.md", "/isolated/unread", 12)
    await connection.execute(
        "UPDATE activity.visit_import SET status='failed',error_message='无法提取文字' WHERE id=$1::uuid", import_id
    )
    await actor(connection, "OPS001")
    failed = await rows(connection, category="material", outcome="failed", department=sales.team_ids[0])
    assert len(failed["items"]) == 2
    assert all(r["result"] == "failed" for r in failed["items"])
    assert (await rows(connection, category="material", outcome="success"))["total"] == 0
    assert (await rows(connection, category="material", department=uuid4()))["total"] == 0
