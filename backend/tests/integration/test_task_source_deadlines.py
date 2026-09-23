"""V076 permits historic source deadlines without weakening manual tasks or tenancy."""

import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.domain.tasks import TaskConflict
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.tasks import TaskService

from .provision import create_owned_customer

pytestmark = pytest.mark.asyncio


async def test_deadline_constraint_and_source_tenant_boundary(connection, sales_actor):
    customer = await create_owned_customer(connection, sales_actor, data={
        "name": "【测试】来源期限" + uuid4().hex, "industry": "软件", "customer_type": "潜在客户",
        "level_code": "Tier-2", "source": "销售线索", "target_team": "南区", "partner_name": "",
        "contact_name": "测试联系人", "contact_title": "经理", "contact_role": "决策者",
    })
    visit = await VisitRepository().create(connection, sales_actor, customer_id=customer["id"], fields={
        "interaction_at": "2026-09-13", "created_date": "2026-09-13", "contact_name": "测试联系人",
        "follow_up_record": "确认方案要求", "next_action": "2026-09-10 10:00提供方案",
        "_follow_up_quality_score": 85,
    })
    past, future = datetime(2000, 1, 1, tzinfo=UTC), datetime.now(UTC) + timedelta(days=2)

    async def insert(kind, due, source=None, workspace=None):
        return await connection.fetchval(
            """INSERT INTO workflow.task(workspace_id,task_type,title,description,creator_user_ref_id,
               status,due_at,source_visit_id,source_code)
               VALUES($1::uuid,$2,'测试期限','测试期限',$3::uuid,'pending_execution',$4,$5::uuid,'import')
               RETURNING id""", workspace or sales_actor.workspace_id, kind, sales_actor.user_id, due, source,
        )

    # Database rule does not rely on a provider label. A valid source is enough;
    # full source authority/state is covered by the real handler integration.
    saved = await insert("visit_follow_up", past, visit["id"])
    assert await connection.fetchval("SELECT due_at FROM workflow.task WHERE id=$1", saved) == past
    for kind, source in (("management", None), ("management", visit["id"]), ("visit_follow_up", None)):
        with pytest.raises(asyncpg.CheckViolationError) as error:
            async with connection.transaction():
                await insert(kind, past, source)
        assert error.value.constraint_name == "task_check"
    assert await insert("management", future)
    with pytest.raises(TaskConflict, match="TASK_DUE_AT_MUST_BE_FUTURE"):
        await TaskService().create(connection, actor=sales_actor, description="手动过期任务",
                                   assignee_account_code="XS001", due_at=past, priority_code="normal")

    # Privileged fixture setup lets the FK itself prove cross-workspace rejection,
    # independently from the RLS layer. Restore the non-bypass runtime afterwards.
    runtime = await connection.fetchval("SELECT current_user")
    assert re.fullmatch(r"salegent_verify_role_[a-f0-9]+", runtime)
    await connection.execute("RESET ROLE")
    try:
        other_workspace = uuid4()
        await connection.execute(
            "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'隔离外部空间')",
            other_workspace, "deadline-test-" + uuid4().hex,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError) as error:
            async with connection.transaction():
                await insert("visit_follow_up", past, visit["id"], str(other_workspace))
        assert error.value.constraint_name == "task_source_visit_workspace_fkey"
    finally:
        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
    assert not await connection.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
