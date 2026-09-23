"""Real HTTP + PostgreSQL UUID validation and existing RLS/not-found semantics."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sales_backend.domain.agent import AgentMode
from sales_backend.repositories.assistant import AssistantRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.tasks import TaskService
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde, members, other_project, technical_review
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login
from tests.integration.test_workbench_risk_memberships import risk_fixture

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("path", ["/agent/runs/", "/customers/", "/tasks/", "/risks/", "/visits/"])
async def test_well_formed_unknown_uuid_still_returns_404(connection, path):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get("/api/v1" + path + str(uuid4()))
        assert response.status_code == 404, response.text
        invalid = await client.get("/api/v1" + path + "not-a-uuid")
        assert invalid.status_code == 422, invalid.text


async def test_uuid_normalization_does_not_expand_customer_task_visit_run_or_risk_access(connection):
    op = await opportunity(connection, "XS001", 100)
    owner = await actor(connection, "XS001")
    visit = await VisitRepository().create(
        connection,
        owner,
        customer_id=op["customer_id"],
        fields={
            "opportunity_id": op["id"],
            "interaction_at": "2026-09-14",
            "created_date": "2026-09-14",
            "contact_name": "测试联系人",
            "follow_up_record": "已确认客户试点的技术需求",
            "next_action": "9月20日由销售发送试点方案",
            "_follow_up_quality_score": 85,
        },
    )
    task = await TaskService().create(
        connection,
        actor=owner,
        description="核实已关联商机的试点方案",
        target_position="self",
        due_at=datetime.now(UTC) + timedelta(days=1),
        priority_code="normal",
        customer_id=op["customer_id"],
        opportunity_id=op["id"],
    )
    repo = AssistantRepository()
    conversation = await repo.create_conversation(
        connection, owner, mode=AgentMode.VISIT_ENTRY, customer_id=op["customer_id"]
    )
    run_id = await repo.enqueue_message(
        connection,
        owner,
        conversation_id=conversation["id"],
        text="已与客户确认下一步跟进计划",
        client_message_id="client:opaque-unit-1",
        input_source="text",
    )
    risk_id, _ = await risk_fixture(connection)
    paths = [
        f"/customers/{op['customer_id']}",
        f"/tasks/{task['id']}",
        f"/visits/{visit['id']}",
        f"/agent/runs/{run_id}",
        f"/risks/{risk_id}",
    ]
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        for path in paths:
            prefix, identifier = path.rsplit("/", 1)
            response = await client.get("/api/v1" + prefix + "/" + identifier.upper())
            assert response.status_code == 200, (path, response.text)
            assert response.json()["id"] == identifier
        filters = await client.get(
            "/api/v1/tasks", params={"customer_id": op["customer_id"].upper(), "opportunity_id": op["id"].upper()}
        )
        assert filters.status_code == 200, filters.text
        assert task["id"] in {row["id"] for row in filters.json()["items"]}
        await business_login(client, "XS002")
        for path in paths:
            response = await client.get("/api/v1" + path)
            assert response.status_code == 404, (path, response.text)


async def test_notification_read_keeps_idempotent_absent_id_semantics(connection):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        path = "/api/v1/notifications/" + str(uuid4()) + "/read"
        assert (await client.post(path)).status_code == 204
        assert (await client.post(path)).status_code == 204
        assert (await client.post("/api/v1/notifications/not-a-uuid/read")).status_code == 422


async def test_business_activity_ids_keep_evidence_prefixes(connection):
    op = await opportunity(connection, "XS001", 100)
    event_id = "customer:" + op["customer_id"]
    async with await client_for(connection) as client:
        await sign_in(client)
        response = await client.get("/api/v1/console/activities/" + event_id)
        assert response.status_code == 200, response.text
        assert response.json()["event_id"] == event_id
        missing = await client.get("/api/v1/console/activities/customer:" + str(uuid4()))
        assert missing.status_code == 404, missing.text


@pytest.mark.parametrize("member", ["first", "lead"], ids=["fde", "fde_lead"])
async def test_nested_visit_opportunity_uuid_preserves_fde_recording_permissions(connection, member):
    sales, project, people, _ = await fde_fixture(connection)
    if member == "lead":
        await members(connection, sales, project, [person["id"] for person in people.values()])
    unrelated = await other_project(connection, sales, project)
    recorder = await actor(connection, people[member]["code"])
    body = await technical_review(connection, recorder, project)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people[member])
        invalid = await client.post(
            "/api/v1/visits", json={**body, "fields": {**body["fields"], "opportunity_id": "not-a-uuid"}},
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["detail"][0]["loc"] == ["body", "fields"]
        for reference in [{}, {"opportunity_id": None}, {"opportunity_id": ""}]:
            fields = {key: value for key, value in body["fields"].items() if key != "opportunity_id"}
            missing = await client.post("/api/v1/visits", json={**body, "fields": {**fields, **reference}})
            assert missing.status_code == 403, missing.text
        for identifier in [str(uuid4()), unrelated["id"].upper()]:
            forbidden = await client.post(
                "/api/v1/visits", json={**body, "fields": {**body["fields"], "opportunity_id": identifier}},
            )
            assert forbidden.status_code == 403, forbidden.text
        saved = await client.post(
            "/api/v1/visits", json={**body, "fields": {**body["fields"], "opportunity_id": project["id"].upper()}},
        )
        assert saved.status_code == 201, saved.text
        assert saved.json()["opportunity_id"] == project["id"]
