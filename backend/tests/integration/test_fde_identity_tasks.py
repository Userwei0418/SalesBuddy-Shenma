"""FDE task and identity lifecycle against PostgreSQL; every test rolls back its data."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.tasks import TaskForbidden, TaskNotFound
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.operations_accounts import OperationsAccountRepository
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.capabilities import capability_snapshot
from sales_backend.services.tasks import TaskService
from tests.integration.test_business_rankings import opportunity as create_opportunity
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def fde_fixture(connection, *, extra_members=0):
    opportunity = await create_opportunity(connection, "XS001", 100000)
    sales = await actor(connection, "XS001")
    admin = await actor(connection, "ADMIN001")
    repo = OperationsAccountRepository()
    department = await repo.save_department(
        connection,
        admin,
        None,
        {"code": "FDE-" + uuid4().hex[:8], "name": "FDE事务测试部门", "status": "active", "parent_team_id": None},
    )
    people = {}
    for label, role in [("first", "fde"), ("second", "fde"), ("lead", "fde_lead")] + [(f"extra{i}", "fde") for i in range(extra_members)]:
        code = "FD" + uuid4().hex[:12].upper()
        result = await repo.create(
            connection,
            admin,
            {"account_code": code, "display_name": label, "roles": [role], "team_id": department["id"]},
        )
        people[label] = {"code": code, "id": result["id"]}
        if role == "fde":
            await connection.execute(
                "INSERT INTO crm.opportunity_participant(workspace_id,opportunity_id,user_ref_id,participant_role,source_code,assigned_by_user_ref_id) "
                "VALUES($1::uuid,$2::uuid,$3::uuid,'fde','manual',$4::uuid)",
                admin.workspace_id,
                opportunity["id"],
                result["id"],
                admin.user_id,
            )
    return sales, opportunity, people, department["id"]


async def make_task(connection, sales, opportunity):
    await set_request_context(connection, sales)
    return await TaskService().create(
        connection,
        actor=sales,
        description="请FDE协助核实该商机的技术需求",
        due_at=datetime.now(UTC) + timedelta(days=2),
        priority_code="normal",
        target_position="fde",
        customer_id=opportunity["customer_id"],
        opportunity_id=opportunity["id"],
    )


async def test_fde_task_retains_action_after_project_removal_and_handover_after_account_revocation(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    service = TaskService()
    task = await make_task(connection, sales, opportunity)
    assert len(task["candidates"]) == 2
    first = await actor(connection, people["first"]["code"])
    accepted = await service.apply_event(connection, actor=first, task_id=task["id"], event_type="accept", note=None)
    assert accepted["owner_name"] == "first"
    second = await actor(connection, people["second"]["code"])
    assert not (await TaskRepository().detail(connection, task_id=task["id"]))["requires_action"]
    await actor(connection, "ADMIN001")
    await connection.execute(
        "UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='测试移出' "
        "WHERE opportunity_id=$1::uuid AND user_ref_id=$2::uuid AND valid_to='infinity'",
        opportunity["id"],
        first.user_id,
    )
    await set_request_context(connection, first)
    # Assignment grants the task and names only, regardless of project membership.
    retained = await TaskRepository().detail(connection, task_id=task["id"])
    assert retained and not retained["handover_required"] and retained["requires_action"]
    assert retained["opportunity_name"] and not retained["can_open_opportunity"]
    assert not await connection.fetchval("SELECT id FROM crm.opportunity WHERE id=$1::uuid", opportunity["id"])
    await actor(connection, "ADMIN001")
    await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", first.user_id)
    await set_request_context(connection, first)
    with pytest.raises((TaskForbidden, TaskNotFound)):
        await service.complete(connection, actor=first, task_id=task["id"], note="账号停用不能继续")
    lead = await actor(connection, people["lead"]["code"])
    detail = await TaskRepository().detail(connection, task_id=task["id"])
    assert detail["handover_required"] and detail["can_coordinate"]
    reassigned = await service.apply_event(
        connection,
        actor=lead,
        task_id=task["id"],
        event_type="reassign",
        note="交接给仍参与本项目的同事",
        expected_version=detail["version_no"],
        assignee_account_code=people["second"]["code"],
    )
    assert reassigned["status"] == "pending_confirm" and reassigned["owner_name"] == "second"
    assert len([p for p in reassigned["assignees"] if p["responsibility"] == "owner"]) == 1
    assert not reassigned["handover_required"]
    await set_request_context(connection, second)
    await service.apply_event(connection, actor=second, task_id=task["id"], event_type="accept", note=None)
    done = await service.complete(connection, actor=second, task_id=task["id"], note="技术需求已核对")
    assert done["status"] == "pending_review"
    await set_request_context(connection, sales)
    approved = await service.apply_event(connection, actor=sales, task_id=task["id"],
        event_type="approve_completion", note=None, expected_version=done["version_no"])
    assert approved["status"] == "completed"
    await actor(connection, "ADMIN001")
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM workflow.task_event WHERE task_id=$1::uuid AND event_type='reassign'", task["id"]
        )
        == 1
    )
    # Inbox SELECT is recipient-only even for administrators.
    await set_request_context(connection, second)
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE object_id=$1::uuid AND template_code='task_reassigned' AND recipient_user_ref_id=$2::uuid",
            task["id"],
            second.user_id,
        )
        == 1
    )


async def test_fde_department_login_default_capability_and_role_removal(connection):
    _, _, people, team = await fde_fixture(connection)
    lead = await actor(connection, people["lead"]["code"])
    refreshed = await IdentityRepository().find_actor_by_id(
        connection, workspace_id=lead.workspace_id, user_id=lead.user_id, role="fde_lead"
    )
    assert refreshed and refreshed.context == lead and lead.team_ids == (team,)
    snapshot = await capability_snapshot(connection, lead)
    assert snapshot["capabilities"]["team.view"] and snapshot["capabilities"]["visit.create"]
    await actor(connection, "ADMIN001")
    await connection.execute("UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid", lead.user_id)
    await set_request_context(connection, lead)
    assert (
        await IdentityRepository().find_actor_by_id(
            connection, workspace_id=lead.workspace_id, user_id=lead.user_id, role="fde_lead"
        )
        is None
    )


async def test_direct_accept_after_handover_creates_new_card_but_replay_does_not(connection):
    from sales_backend.services.idempotency import execute_mutation

    sales, opportunity, people, _ = await fde_fixture(connection)
    service = TaskService()
    await set_request_context(connection, sales)
    task = await service.create(
        connection, actor=sales, description="演示同一任务先后由两位FDE接收",
        due_at=datetime.now(UTC) + timedelta(days=2), priority_code="normal",
        assignee_account_code=people["first"]["code"],
        customer_id=opportunity["customer_id"], opportunity_id=opportunity["id"],
    )
    first = await actor(connection, people["first"]["code"])
    accepted = await service.apply_event(
        connection, actor=first, task_id=task["id"], event_type="accept", note=None,
        expected_version=task["version_no"],
    )
    await set_request_context(connection, sales)
    transferred = await service.apply_event(
        connection, actor=sales, task_id=task["id"], event_type="reassign", note="交接下一阶段验证",
        expected_version=accepted["version_no"], assignee_account_code=people["second"]["code"],
    )
    second = await actor(connection, people["second"]["code"])
    key = uuid4()
    payload = {"event_type": "accept", "version_no": transferred["version_no"]}

    async def accept_again():
        return await service.apply_event(
            connection, actor=second, task_id=task["id"], event_type="accept", note=None,
            expected_version=transferred["version_no"],
        )

    result = await execute_mutation(connection, second, key, f"tasks.event:{task['id']}", payload, accept_again)
    replay = await execute_mutation(connection, second, key, f"tasks.event:{task['id']}", payload, accept_again)
    assert result == replay and result["status"] == "pending_execution"
    await set_request_context(connection, sales)
    cards = await connection.fetch(
        "SELECT payload FROM workflow.notification WHERE object_id=$1::uuid "
        "AND recipient_user_ref_id=$2::uuid AND template_code='task_accepted' ORDER BY created_at,id",
        task["id"], sales.user_id,
    )
    assert len(cards) == 2
    assert {card["payload"]["responded_by_user_ref_id"] for card in cards} == {first.user_id, second.user_id}
    assert {card["payload"]["event_version"] for card in cards} == {
        accepted["version_no"], result["version_no"],
    }
    assert await connection.fetchval(
        "SELECT count(*) FROM workflow.task_event WHERE task_id=$1::uuid AND event_type='accept'", task["id"]
    ) == 2


async def test_fde_position_reject_and_lead_cancellation_preserve_events(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    service = TaskService()
    task = await make_task(connection, sales, opportunity)
    first = await actor(connection, people["first"]["code"])
    rejected = await service.apply_event(
        connection, actor=first, task_id=task["id"], event_type="reject", note="本周时间冲突"
    )
    assert rejected["status"] == "pending_confirm"
    lead = await actor(connection, people["lead"]["code"])
    cancelled = await service.apply_event(
        connection,
        actor=lead,
        task_id=task["id"],
        event_type="cancel",
        note="本项目暂停，保留处理记录",
        expected_version=rejected["version_no"],
    )
    assert cancelled["status"] == "cancelled"
    assert len(cancelled["candidates"]) == 2 and any(e["event_type"] == "cancel" for e in cancelled["events"])


async def test_fde_http_capabilities_policy_refresh_and_analysis_revoke(connection):
    from sales_backend.auth.passwords import encode_password
    from sales_backend.repositories.company_rules import CompanyRulesRepository
    from tests.integration.test_operations_api import client_for

    _, opportunity, people, _ = await fde_fixture(connection)
    await actor(connection, "ADMIN001")
    password = "Transaction-FDE-2026"
    await connection.execute(
        "SELECT security.set_account_password($1::uuid,$2,false)", people["first"]["id"], encode_password(password)
    )
    async with await client_for(connection) as client:
        login = await client.post(
            "/api/v1/auth/password/login",
            json={"account_code": people["first"]["code"], "password": password, "role": "fde"},
        )
        assert login.status_code == 200, login.text
        first_actor = login.json()["actor"]
        assert first_actor["role"] == "fde" and first_actor["capabilities"]["visit.create"]
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        me = await client.get("/api/v1/auth/me")
        assert me.headers["cache-control"] == "no-store"
        assert (await client.get("/api/v1/console/organization")).status_code == 403
        body = {"mode": "visit_entry", "customer_id": opportunity["customer_id"]}
        assert (await client.post("/api/v1/conversations", json=body)).status_code == 403
        body["opportunity_id"] = opportunity["id"]
        assert (await client.post("/api/v1/conversations", json=body)).status_code == 201
        conversation = await client.post(
            "/api/v1/conversations", json={"mode": "customer_chatbi", "customer_id": opportunity["customer_id"]}
        )
        assert conversation.status_code == 201, conversation.text
        message = await client.post(
            "/api/v1/conversations/" + conversation.json()["id"] + "/messages", json={"text": "总结目前的技术协作进展"}
        )
        assert message.status_code == 202, message.text
        run_id = message.json()["run_id"]
        snapshot = await connection.fetchval("SELECT identity_context FROM agent.run WHERE id=$1::uuid", run_id)
        assert snapshot["permission_version"] == (await connection.fetchval("SELECT security.authorization_snapshot()"))["permission_version"]
        await actor(connection, "ADMIN001")
        rules = CompanyRulesRepository()
        current = await rules.active(connection, "fde_capabilities")
        rule_id = await rules.save(
            connection,
            "fde_capabilities",
            {
                "base_id": current["id"],
                "reason": "事务测试暂停单人录入",
                "definition": {**current["definition"], "user_overrides": {people["first"]["id"]: False}},
            },
        )
        await rules.publish(connection, rule_id, 1)
        now = await client.get("/api/v1/auth/me")
        assert not now.json()["capabilities"]["visit.create"]
        assert now.json()["permission_version"] != first_actor["permission_version"]
        assert (await client.get("/api/v1/agent/runs/" + run_id)).status_code == 404
        assert (await client.post("/api/v1/conversations", json=body)).status_code == 403
        await actor(connection, "ADMIN001")
        current = await rules.active(connection, "fde_capabilities")
        restored = await rules.save(connection, "fde_capabilities", {
            "base_id": current["id"], "reason": "恢复单人正常录入",
            "definition": {**current["definition"], "user_overrides": {}},
        })
        await rules.publish(connection, restored, 1)
        enabled = await client.post("/api/v1/conversations", json=body)
        assert enabled.status_code == 201, enabled.text
        await actor(connection, "ADMIN001")
        await connection.execute(
            "UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='测试撤权' "
            "WHERE opportunity_id=$1::uuid AND user_ref_id=$2::uuid AND valid_to='infinity'",
            opportunity["id"],
            people["first"]["id"],
        )
        blocked = await client.post(
            "/api/v1/conversations/" + enabled.json()["id"] + "/messages", json={"text": "已撤权不应继续"}
        )
        assert blocked.status_code in {403, 404}, blocked.text
        notices = await client.get("/api/v1/notifications")
        assert notices.status_code == 200, notices.text
        assert any(n["template_code"] == "fde_removed" for n in notices.json()["items"])


async def test_fde_report_and_question_facts_use_participation_instead_of_sales_ownership(connection):
    from sales_backend.repositories.fde_analysis import fde_analysis_facts

    _, opportunity, people, _ = await fde_fixture(connection)
    for label, scope in [("first", "self"), ("lead", "team")]:
        member = await actor(connection, people[label]["code"])
        facts = await fde_analysis_facts(connection, member)
        assert facts["scope"]["scope_type"] == scope
        assert [o["id"] for o in facts["opportunities"]] == [opportunity["id"]]
        assert facts["summary"]["opportunities"] == 1
        assert facts["summary"]["open_acv"] == 100000
        assert facts["summary"]["period_visits"] == 0
        assert "共享事实" in facts["scope_note"]
        assert all("open_pipeline_amount_cny" not in row for row in facts["members"])
