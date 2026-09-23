from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.operations_ai import OperationsAIRepository
from sales_backend.repositories.operations_customers import OperationsCustomerRepository
from sales_backend.repositories.operations_logs import OperationsLogRepository
from sales_backend.repositories.operations_opportunities import OperationsOpportunityRepository
from sales_backend.repositories.model_calls import ModelCallRepository
from sales_backend.services.operations_accounts import OperationsAccountService
from sales_backend.services.opportunities import save_opportunity
from sales_backend.auth.passwords import verify_password
from tests.integration.test_operations_claims_sql import actor, create_customer

pytestmark = pytest.mark.asyncio


async def test_account_lifecycle_roles_and_last_administrator(connection):
    ops = await actor(connection, "OPS001")
    service = OperationsAccountService()
    org = await OperationsAccountRepository().organization(connection)
    team = org["departments"][0]["id"]
    data = dict(
        account_code="SQL" + uuid4().hex[:12], display_name="隔离账号", team_id=team, roles=["sales", "supervisor"]
    )
    created = await service.create(connection, ops, data, "Isolated-New-2026")
    candidate = await connection.fetchval(
        "SELECT security.password_candidate('demo-sales-workspace',$1,'sales')", created["account_code"]
    )
    assert candidate and candidate["must_change_password"]
    assert verify_password("Isolated-New-2026", candidate["password_hash"])
    assert not await connection.fetchrow(
        "SELECT * FROM security.resolve_demo_actor('demo-sales-workspace',$1)", created["account_code"]
    )
    with pytest.raises(PermissionError):
        await service.create(connection, ops, {**data, "roles": ["administrator"]}, "Isolated-New-2026")
    await service.update(connection, ops, created["id"], {**data, "status": "inactive", "version_no": 1})
    assert not await connection.fetchval(
        "SELECT security.password_candidate('demo-sales-workspace',$1,NULL)", created["account_code"]
    )
    admin = await actor(connection, "ADMIN001")
    with pytest.raises(ValueError, match="至少一位"):
        await service.update(
            connection,
            admin,
            admin.user_id,
            dict(display_name="admin", team_id=team, roles=["administrator"], status="inactive", version_no=1),
        )


async def test_management_read_models_and_audit_immutability(connection):
    customer = await create_customer(connection)
    await actor(connection, "OPS001")
    repo = OperationsCustomerRepository()
    assert (await repo.list(connection, q=customer["name"]))["total"] == 1
    assert (await repo.summary(connection))["customers"] >= 1
    logs = OperationsLogRepository()
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = start + timedelta(days=2)
    entries = await logs.audit(connection, start=start, end=end)
    assert any(x["object_id"] == customer["id"] for x in entries["items"])
    row = await connection.fetchrow(
        "SELECT id,after_snapshot FROM ops.audit_log WHERE object_id=$1::uuid ORDER BY occurred_at LIMIT 1",
        customer["id"],
    )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with connection.transaction():
            await connection.execute("DELETE FROM ops.audit_log WHERE id=$1", row["id"])
    await actor(connection, "XS001")
    assert await connection.fetchval("SELECT count(*) FROM ops.audit_log") == 0


async def test_admin_opportunity_and_change_card_without_customer_ownership(connection):
    customer = await create_customer(connection)
    sales = await actor(connection, "XS001")
    assert not await connection.fetchval("SELECT security.has_customer_access($1::uuid)", customer["id"])
    ops = await actor(connection, "OPS001")
    data = dict(
        name="隔离商机" + uuid4().hex,
        amount=Decimal("200000"),
        probability=10,
        status="open",
        expected_close_date=date(2027, 3, 12),
        owner_user_ref_id=sales.user_id,
        follow_up_plan="下周核对试点范围",
        quarterly_forecasts=[dict(year=2027,quarter=1,recognized_amount=Decimal("10000"),collection_amount=None)],
    )
    saved = await save_opportunity(connection, ops, customer_id=customer["id"], data=data)
    repo = OperationsOpportunityRepository()
    assert (await repo.list(connection, customer=customer["id"]))["total"] == 1
    await repo.attach_quote(
        connection, ops, saved["id"], dict(reference_no="Q-001", title="外部报价", url=None, amount=None)
    )
    detail = await repo.detail(connection, saved["id"])
    assert detail["follow_up_plan"] == data["follow_up_plan"]
    assert detail["quarterly_forecasts"][0]["recognized_amount"] == 10000
    assert len(detail["quote_references"]) == 1 and detail["changes"]
    await actor(connection, "XS001")
    assert await connection.fetchval("SELECT security.has_opportunity_access($1::uuid)", saved["id"])
    updated = await save_opportunity(
        connection,
        sales,
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
    assert updated["changed"]
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE template_code='business_changed' AND object_id=$1::uuid",
            saved["id"],
        )
        == 2
    )
    await actor(connection, "XS002")
    assert not await connection.fetchval("SELECT security.has_opportunity_access($1::uuid)", saved["id"])


async def test_actual_usage_missing_tokens_and_role_reporting(connection):
    sales = await actor(connection, "XS001")
    calls = ModelCallRepository()
    operation = str(uuid4())
    usage = dict(
        http_status=200, input_tokens=120, output_tokens=30, audio_seconds=None, characters=None, trace_id=None
    )
    for attempt in (1, 2):
        invocation = await calls.start(
            connection,
            sales,
            operation="visit.structure",
            operation_id=operation,
            run_id=None,
            endpoint="/v1/chat/completions",
            model="isolated-fixture",
            metadata={"message_count": 2},
            attempt=attempt,
            request_id=str(uuid4()),
            provider="test-provider",
        )
        await calls.finish(
            connection,
            invocation,
            {**usage, "input_tokens": None, "output_tokens": None} if attempt == 1 else usage,
            "failed" if attempt == 1 else "succeeded",
            "HTTP_503" if attempt == 1 else None,
        )
    await actor(connection, "OPS001")
    repo = OperationsAIRepository()
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = start + timedelta(days=2)
    report = await repo.report(connection, start=start, end=end, role="sales")
    assert report["summary"]["calls"] == 2 and report["summary"]["business_operations"] == 1
    assert report["summary"]["input_tokens"] == 120 and report["summary"]["unknown_token_calls"] == 1
    assert (await repo.calls(connection, start=start, end=end))["total"] == 2
    assert (await repo.report(connection, start=start, end=end, role="manager"))["summary"]["calls"] == 0
    await repo.save_rule(
        connection,
        await actor(connection, "OPS001"),
        None,
        dict(name="隔离测试阈值", role_code="sales", period="day", calls_limit=1, tokens_limit=None, enabled=True),
    )
    await connection.execute("SELECT ops.evaluate_ai_usage_alerts()")
    await connection.execute("SELECT ops.evaluate_ai_usage_alerts()")
    alerts = await repo.alerts(connection)
    assert len(alerts) == 1 and alerts[0]["calls_count"] == 2
    assert len(await repo.rules(connection)) == 1


async def test_audit_total_survives_empty_page_and_actor_switch(connection):
    await create_customer(connection)
    await actor(connection, 'OPS001')
    logs = OperationsLogRepository()
    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = start + timedelta(days=2)
    visible = await logs.audit(connection, start=start, end=end, limit=1)
    assert visible['total'] > 0
    empty_page = await logs.audit(connection, start=start, end=end, offset=visible['total'] + 1)
    assert empty_page['items'] == [] and empty_page['total'] == visible['total']
    await actor(connection, 'XS001')
    hidden = await logs.audit(connection, start=start, end=end)
    assert hidden == {'items': [], 'total': 0}
    assert (await logs.system_events(connection, start=start, end=end))['total'] == 0
    await actor(connection, 'OPS001')
    assert (await logs.audit(connection, start=start, end=end, limit=1))['total'] == visible['total']


async def test_email_rename_keeps_identity_password_and_disables_old_login(connection):
    ops = await actor(connection, "OPS001")
    service = OperationsAccountService()
    org = await OperationsAccountRepository().organization(connection)
    data = dict(account_code="BEFORE" + uuid4().hex[:12], display_name="隔离邮箱账号",
                team_id=org["departments"][0]["id"], roles=["sales"])
    created = await service.create(connection, ops, data, "Isolated-Email-2026")
    old = await connection.fetchval("SELECT security.password_candidate('demo-sales-workspace',$1,'sales')", data['account_code'])
    email = uuid4().hex[:12] + "@example.com"
    await service.update(connection, ops, created['id'], {**data, 'account_code': email, 'version_no': 1, 'status': 'active'})
    assert not await connection.fetchval("SELECT security.password_candidate('demo-sales-workspace',$1,'sales')", data['account_code'])
    new = await connection.fetchval("SELECT security.password_candidate('demo-sales-workspace',$1,'sales')", email)
    assert new['user_id'] == old['user_id'] == created['id']
    assert new['password_hash'] == old['password_hash']
    assert new['team_ids'] == old['team_ids']
    assert new['role_code'] == old['role_code']
    duplicate = {**data, 'account_code': email.upper()}
    with pytest.raises(asyncpg.UniqueViolationError):
        async with connection.transaction():
            await service.create(connection, ops, duplicate, 'Isolated-Email-2026')
