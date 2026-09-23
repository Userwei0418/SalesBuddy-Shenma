"""Real PG/RLS and ASGI profile contracts; no worker/model invocation in this suite."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.domain.agent import AgentMode
from sales_backend.domain.fde_profile import TZ
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.fde_profile import profile_facts
from sales_backend.services.agent_run.models import RunInput
from sales_backend.services.fde_profile import current_run_facts, get_profile, request_review
from sales_backend.services.tasks import TaskService
from tests.integration.test_fde_collaboration import record_visit
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde, members, own_visit
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_profile_counts_only_self_archives_and_lead_never_inherits_team_samples(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    await record_visit(connection, sales, opportunity, [people["first"], people["second"]])
    first = await actor(connection, people["first"]["code"])
    before = await get_profile(connection, first, 30)
    assert before["sample_count"] == 0
    assert before["latest"]["dimensions"][0]["score"] is None
    await own_visit(connection, first, opportunity, on=datetime.now(TZ).date().isoformat())
    report = await get_profile(connection, first, 30)
    assert report["sample_count"] == 1
    assert [x["score"] for x in report["latest"]["dimensions"][:4]] == [85, 100, 100, 100]
    second = await actor(connection, people["second"]["code"])
    assert (await get_profile(connection, second, 30))["sample_count"] == 0
    lead = await actor(connection, people["lead"]["code"])
    leader = await get_profile(connection, lead, 30)
    assert leader["sample_count"] == 0 and leader["review_status"] == "empty"
    await set_request_context(connection, sales)
    with pytest.raises(PermissionError):
        await get_profile(connection, sales, 30)


async def test_profile_http_reuse_permission_snapshot_and_public_conversation_cannot_set_surface(connection):
    _, opportunity, people, _ = await fde_fixture(connection)
    first_actor = await actor(connection, people["first"]["code"])
    await own_visit(connection, first_actor, opportunity, on=datetime.now(TZ).date().isoformat())
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        profile = await client.get("/api/v1/fde/profile?days=30")
        assert profile.status_code == 200, profile.text
        for days in [6, 91]:
            assert (await client.get(f"/api/v1/fde/profile?days={days}")).status_code == 422
        first = await client.post("/api/v1/fde/profile/review?days=30")
        assert first.status_code == 202, first.text
        result = first.json()
        assert result["review_status"] == "queued" and result["latest"]["overall_score"] is None
        again = await client.post("/api/v1/fde/profile/review?days=30")
        assert again.json()["review_run_id"] == result["review_run_id"]
        assert (await client.get("/api/v1/fde/profile")).json()["review_run_id"] == result["review_run_id"]
        row = await connection.fetchrow(
            "SELECT identity_context,business_context FROM agent.run WHERE id=$1::uuid", result["review_run_id"]
        )
        assert (
            row["identity_context"]["permission_version"]
            == row["business_context"]["profile_snapshot"]["permission_version"]
        )
        assert row["business_context"]["facts_fingerprint"] == result["facts_fingerprint"]
        assert row["business_context"]["profile_snapshot"]["scope"]["user_id"] == people["first"]["id"]
        assert (
            await connection.fetchval(
                "SELECT count(*) FROM ops.job WHERE aggregate_id=$1::uuid", result["review_run_id"]
            )
            == 1
        )
        response = await client.post(
            "/api/v1/conversations",
            json={
                "mode": "operating_report",
                "surface": "fde_profile",
                "profile_days": 7,
                "facts_fingerprint": "forged",
            },
        )
        assert response.status_code in {201, 422}, response.text
        if response.status_code == 201:
            context = await connection.fetchval(
                "SELECT context FROM agent.conversation WHERE id=$1::uuid", response.json()["id"]
            )
            assert "surface" not in context and "facts_fingerprint" not in context


async def test_success_reuses_until_data_changes_and_failed_run_can_retry(connection):
    _, opportunity, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    await own_visit(connection, first, opportunity, on=datetime.now(TZ).date().isoformat())
    request = await request_review(connection, first, 30)
    run_id = request["review_run_id"]
    await connection.execute(
        "UPDATE agent.run SET status='succeeded',completed_at=clock_timestamp() WHERE id=$1::uuid", run_id
    )
    await connection.execute(
        """INSERT INTO agent.message(workspace_id,conversation_id,sender_type,content_type,
        text_content,structured_content,source_run_id) SELECT workspace_id,conversation_id,'assistant','card',
        '技术测试建议',$2::jsonb,id FROM agent.run WHERE id=$1::uuid""",
        run_id,
        {
            "summary": "技术测试已完成建议，无真实模型调用",
            "action_plan": [{"title": "跟进", "detail": "请记录协助项目"}],
        },
    )
    completed = await get_profile(connection, first, 30)
    assert completed["review_status"] == "succeeded" and completed["latest"]["advice"][0]["content"] == "请记录协助项目"
    assert (await request_review(connection, first, 30))["review_run_id"] == run_id
    await own_visit(connection, first, opportunity, on=datetime.now(TZ).date().isoformat())
    changed = await get_profile(connection, first, 30)
    assert changed["review_status"] == "missing" and changed["latest"]["advice"] == []
    assert changed["facts_fingerprint"] != request["facts_fingerprint"]
    new = await request_review(connection, first, 30)
    assert new["review_run_id"] != run_id
    await connection.execute("UPDATE agent.run SET status='failed' WHERE id=$1::uuid", new["review_run_id"])
    retry = await request_review(connection, first, 30)
    assert retry["review_run_id"] not in {new["review_run_id"], run_id}


async def test_permission_loss_hides_cached_advice_and_old_facts_before_worker_invocation(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    await own_visit(connection, first, opportunity, on=datetime.now(TZ).date().isoformat())
    requested = await request_review(connection, first, 30)
    original = await profile_facts(connection, first, 30)
    run = RunInput(
        requested["review_run_id"],
        str(uuid4()),
        "本人",
        "operating_report",
        None,
        first,
        permission_version=original["permission_version"],
        surface="fde_profile",
        profile_days=30,
        facts_fingerprint=original["facts_fingerprint"],
    )
    assert (await current_run_facts(connection, run))["facts_fingerprint"] == original["facts_fingerprint"]
    await members(connection, sales, opportunity, [people["second"]["id"]])
    await set_request_context(connection, first)
    with pytest.raises(PermissionError, match="权限已变化"):
        await current_run_facts(connection, run)
    report = await get_profile(connection, first, 30)
    assert report["review_run_id"] is None and report["latest"]["advice"] == []


async def test_ordinary_operating_report_is_never_reused_as_personal_profile(connection):
    _, opportunity, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    await own_visit(connection, first, opportunity, on=datetime.now(TZ).date().isoformat())
    repo = AssistantRepository()
    conversation = await repo.create_conversation(connection, first, mode=AgentMode.OPERATING_REPORT, customer_id=None)
    ordinary = await repo.enqueue_message(
        connection,
        first,
        conversation_id=conversation["id"],
        text="团队总结",
        client_message_id=None,
        input_source="text",
    )
    assert (await get_profile(connection, first, 30))["review_run_id"] is None
    assert (await request_review(connection, first, 30))["review_run_id"] != ordinary


async def profile_task(connection, sales, opportunity, person, description):
    await set_request_context(connection, sales)
    return await TaskService().create(
        connection, actor=sales, description=description, due_at=datetime.now(UTC) + timedelta(days=3),
        priority_code="normal", assignee_account_code=person["code"],
        customer_id=opportunity["customer_id"], opportunity_id=opportunity["id"],
    )


async def test_coaching_includes_only_currently_readable_own_archive_material(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    await record_visit(connection, sales, opportunity, [people["first"]])
    second = await actor(connection, people["second"]["code"])
    await own_visit(connection, second, opportunity, on=datetime.now(TZ).date().isoformat())
    first = await actor(connection, people["first"]["code"])
    own = await own_visit(connection, first, opportunity, on=datetime.now(TZ).date().isoformat())
    loaded = await profile_facts(connection, first, 30)
    refs = [item["source_ref"] for item in loaded["coaching_inputs"]["sources"]]
    assert refs == ["visit:" + own["id"]]
    assert loaded["coaching_inputs"]["sources"][0]["communication"] == "已确认试点验收范围与数据来源"
    lead = await actor(connection, people["lead"]["code"])
    assert (await profile_facts(connection, lead, 30))["coaching_inputs"]["sources"] == []
    await members(connection, sales, opportunity, [second.user_id])
    await set_request_context(connection, first)
    lost = await profile_facts(connection, first, 30)
    assert lost["sample_count"] == 1 and lost["coaching_inputs"]["sources"] == []
    assert (await request_review(connection, first, 30))["review_status"] == "empty"


async def test_coaching_unfinished_tasks_include_future_and_old_due_dates_only_for_current_owner(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    future = await profile_task(connection, sales, opportunity, people["first"], "未来到期的验证方案")
    old = await profile_task(connection, sales, opportunity, people["first"], "早期到期仍未处理的确认任务")
    other = await profile_task(connection, sales, opportunity, people["second"], "其他同事任务")
    done = await profile_task(connection, sales, opportunity, people["first"], "技术测试已完成任务")
    await actor(connection, "ADMIN001")
    # Seed historical time/state in a rollback-only technical fixture, no provider call.
    await connection.execute(
        "UPDATE workflow.task SET due_at=$2::timestamptz,created_at=$2::timestamptz - interval '1 day',"
                             "version_no=version_no+1 WHERE id=$1::uuid",
                             old["id"], datetime.now(UTC) - timedelta(days=120))
    await connection.execute("UPDATE workflow.task SET status='completed',completed_at=clock_timestamp(),"
                             "version_no=version_no+1 WHERE id=$1::uuid", done["id"])
    first = await actor(connection, people["first"]["code"])
    loaded = await profile_facts(connection, first, 30)
    sources = loaded["coaching_inputs"]["sources"]
    assert [row["source_ref"] for row in sources] == ["task:" + old["id"], "task:" + future["id"]]
    assert all(row["can_execute"] for row in sources)
    assert not {"task:" + other["id"], "task:" + done["id"]} & {row["source_ref"] for row in sources}
    assert (await request_review(connection, first, 30))["review_status"] == "queued"
    await members(connection, sales, opportunity, [people["second"]["id"]])
    await set_request_context(connection, first)
    # Removing project participation does not cancel the task owner responsibility.
    retained = (await profile_facts(connection, first, 30))["coaching_inputs"]["sources"]
    assert [row["source_ref"] for row in retained] == ["task:" + old["id"], "task:" + future["id"]]
    assert all(row["can_execute"] for row in retained)


async def test_coaching_task_text_change_invalidates_cached_result_without_radar_change(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    task = await profile_task(connection, sales, opportunity, people["first"], "明确接口联调范围")
    first = await actor(connection, people["first"]["code"])
    requested = await request_review(connection, first, 30)
    before = await profile_facts(connection, first, 30)
    run = RunInput(requested["review_run_id"], str(uuid4()), "本人", "operating_report", None, first,
                   permission_version=before["permission_version"], surface="fde_profile", profile_days=30,
                   facts_fingerprint=before["facts_fingerprint"])
    current = await current_run_facts(connection, run)
    assert "dimensions" not in current and "evidence_coverage" not in current
    await actor(connection, "ADMIN001")
    await connection.execute("UPDATE workflow.task SET title='已明确接口范围，仅确认验收联系人',"
                             "version_no=version_no+1 WHERE id=$1::uuid", task["id"])
    await set_request_context(connection, first)
    changed = await profile_facts(connection, first, 30)
    assert changed["dimensions"] == before["dimensions"]
    assert changed["facts_fingerprint"] != before["facts_fingerprint"]
    assert (await get_profile(connection, first, 30))["review_run_id"] is None
    with pytest.raises(PermissionError, match="事实已变化"):
        await current_run_facts(connection, run)


async def test_assigned_project_without_material_does_not_create_empty_model_run(connection):
    _, _, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    report = await get_profile(connection, first, 30)
    assert report["latest"]["dimensions"][2]["denominator"] == 1
    requested = await request_review(connection, first, 30)
    assert requested["review_status"] == "empty" and requested["review_run_id"] is None
