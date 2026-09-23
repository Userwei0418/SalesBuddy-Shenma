"""Real PostgreSQL/RLS and ASGI regression for personally recorded FDE visits.

Review artifacts are explicitly seeded technical fixtures, never claimed as
real AI inference. All accounts, business writes and queued jobs roll back in
the integration connection fixture; no worker or external provider runs here.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.auth.passwords import encode_password
from sales_backend.db import set_request_context
from sales_backend.repositories.fde_dashboard import fde_activity, fde_dashboard
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.capabilities import capability_snapshot
from sales_backend.services.collaboration import set_opportunity_members
from sales_backend.services.opportunities import save_opportunity
from sales_backend.services.visit_access import require_visit_recording_scope, require_visit_supplement_scope
from tests.integration.test_fde_collaboration import record_visit
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


def fields_for(opportunity, *, on="2026-09-13"):
    return {
        "opportunity_id": opportunity["id"],
        "interaction_at": on,
        "created_date": "2026-09-13",
        "contact_name": "测试业务联系人",
        "follow_up_record": "已确认试点验收范围与数据来源",
        "next_action": "9月15日由记录者提交验收方案",
        "_follow_up_quality_score": 85,
    }


async def other_project(connection, sales, opportunity):
    await set_request_context(connection, sales)
    return await save_opportunity(
        connection,
        sales,
        customer_id=opportunity["customer_id"],
        data={
            "name": "同客户独立项目-" + uuid4().hex[:8],
            "amount": Decimal(900),
            "probability": 10,
            "expected_close_date": date(2026, 12, 20),
        },
    )


async def members(connection, sales, opportunity, ids):
    await set_request_context(connection, sales)
    version = await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", opportunity["id"])
    return await set_opportunity_members(connection, sales, opportunity["id"], ids, version)


async def login_fde(connection, client, person):
    await actor(connection, "ADMIN001")
    password = "Isolated-FDE-Recording-2026"
    await connection.execute(
        "SELECT security.set_account_password($1::uuid,$2,false)", person["id"], encode_password(password)
    )
    response = await client.post(
        "/api/v1/auth/password/login", json={"account_code": person["code"], "password": password}
    )
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    return response.json()["actor"]


async def own_visit(connection, owner, opportunity, *, on="2026-09-13"):
    await set_request_context(connection, owner)
    # Deliberately no fde_participant_ids: authorship cannot depend on ticking oneself.
    return await VisitRepository().create(
        connection, owner, customer_id=opportunity["customer_id"], fields=fields_for(opportunity, on=on)
    )


async def technical_review(connection, owner, opportunity):
    """Use both current review stages with controlled output, never a provider call."""
    from tests.integration.test_visit_attendance import review_attendance

    await set_request_context(connection, owner)
    _, submitted = await review_attendance(connection, owner, opportunity["customer_id"], opportunity["id"], [])
    return submitted.model_dump(mode="json")


async def test_direct_assignment_is_required_even_when_customer_panorama_or_team_is_readable(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    other = await other_project(connection, sales, opportunity)
    first = await actor(connection, people["first"]["code"])
    assert (await capability_snapshot(connection, first))["capabilities"]["visit.create"]
    assert await OpportunityRepository().detail(connection, first, other["id"])
    await require_visit_recording_scope(connection, first, opportunity["customer_id"], opportunity["id"])
    for customer_id, opportunity_id in [
        (opportunity["customer_id"], None),
        (opportunity["customer_id"], other["id"]),
        (str(uuid4()), opportunity["id"]),
    ]:
        with pytest.raises(PermissionError):
            await require_visit_recording_scope(connection, first, customer_id, opportunity_id)
    lead = await actor(connection, people["lead"]["code"])
    assert await OpportunityRepository().detail(connection, lead, opportunity["id"])
    with pytest.raises(PermissionError):
        await require_visit_recording_scope(connection, lead, opportunity["customer_id"], opportunity["id"])
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        choices = await client.get(
            "/api/v1/fde/visit-opportunities", params={"customer_id": opportunity["customer_id"]}
        )
        assert choices.status_code == 200, choices.text
        assert [row["id"] for row in choices.json()["items"]] == [opportunity["id"]]
        assert (await client.get("/api/v1/fde/visit-opportunities", params={"opportunity_id": other["id"]})).json()[
            "total"
        ] == 0
        await login_fde(connection, client, people["lead"])
        choices = await client.get("/api/v1/fde/visit-opportunities")
        assert choices.status_code == 200 and choices.json()["total"] == 0


@pytest.mark.parametrize("case", ["other_project", "no_opportunity", "unassigned_lead"])
async def test_database_rejects_direct_repository_write_outside_personal_recording_scope(connection, case):
    sales, opportunity, people, _ = await fde_fixture(connection)
    other = await other_project(connection, sales, opportunity)
    owner = await actor(connection, people["lead" if case == "unassigned_lead" else "first"]["code"])
    fields = fields_for(other if case == "other_project" else opportunity)
    if case == "no_opportunity":
        fields["opportunity_id"] = None
    with pytest.raises((asyncpg.InsufficientPrivilegeError, PermissionError)):
        async with connection.transaction():
            await VisitRepository().create(connection, owner, customer_id=opportunity["customer_id"], fields=fields)


async def test_only_self_created_recorded_confirmed_visits_count_without_self_attendance_selection(connection):
    sales, opportunity, people, team_id = await fde_fixture(connection)
    sales_visit = await record_visit(connection, sales, opportunity, [people["first"], people["second"]])
    first = await actor(connection, people["first"]["code"])
    assert (await fde_dashboard(connection, first, year=2026, quarters=[3]))["summary"]["period_visits"] == 0
    visit = await own_visit(connection, first, opportunity)
    identity = await connection.fetchrow(
        "SELECT created_by_user_ref_id::text,recorder_user_ref_id::text,confirmed_by_user_ref_id::text,"
        "recording_role_code_snapshot,recording_team_id_snapshot::text FROM activity.visit WHERE id=$1::uuid",
        visit["id"],
    )
    assert [
        identity[key] for key in ("created_by_user_ref_id", "recorder_user_ref_id", "confirmed_by_user_ref_id")
    ] == [first.user_id] * 3
    assert identity["recording_role_code_snapshot"] == "fde" and identity["recording_team_id_snapshot"] == team_id
    own = await fde_dashboard(connection, first, year=2026, quarters=[3])
    assert own["summary"]["period_visits"] == 1
    assert [row["id"] for row in own["recent_visits"]] == [visit["id"]]
    assert sales_visit["id"] not in str(own["recent_visits"])
    await require_visit_supplement_scope(connection, first, visit["id"])
    second = await actor(connection, people["second"]["code"])
    assert (await fde_dashboard(connection, second, year=2026, quarters=[3]))["summary"]["period_visits"] == 0
    with pytest.raises(PermissionError):
        await require_visit_supplement_scope(connection, second, visit["id"])
    lead = await actor(connection, people["lead"]["code"])
    assert (await fde_dashboard(connection, lead, scope="team", year=2026, quarters=[3]))["summary"][
        "period_visits"
    ] == 1
    assert (await fde_dashboard(connection, lead, scope="self", year=2026, quarters=[3]))["summary"][
        "period_visits"
    ] == 0


async def test_activity_self_and_opportunity_all_history_do_not_include_peers_or_other_projects(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    other = await other_project(connection, sales, opportunity)
    await members(connection, sales, other, [people["first"]["id"]])
    first = await actor(connection, people["first"]["code"])
    old = await own_visit(connection, first, opportunity, on="2025-12-15")
    current = await own_visit(connection, first, opportunity)
    unrelated = await own_visit(connection, first, other)
    second = await actor(connection, people["second"]["code"])
    peer = await own_visit(connection, second, opportunity)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        query = {"scope": "self", "opportunity_id": opportunity["id"], "period": "all", "year": 2026}
        result = await client.get("/api/v1/fde/activity", params=query)
        assert result.status_code == 200, result.text
        ids = {row["id"] for row in result.json()["items"]}
        assert ids == {old["id"], current["id"]} and result.json()["total"] == 2
        assert unrelated["id"] not in ids and peer["id"] not in ids
        annual = await client.get("/api/v1/fde/activity", params={**query, "period": "year"})
        assert {row["id"] for row in annual.json()["items"]} == {current["id"]}
    await members(connection, sales, opportunity, [people["second"]["id"]])
    await members(connection, sales, other, [])
    await set_request_context(connection, first)
    history = await fde_activity(connection, first, scope="self", opportunity_id=opportunity["id"], all_history=True)
    assert {row["id"] for row in history["items"]} == {old["id"], current["id"]}
    assert all(row["can_read_detail"] is False and "follow_up_record" not in row for row in history["items"])


async def test_cross_opportunity_review_rejected_without_consuming_review_or_writing_visit(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    other = await other_project(connection, sales, opportunity)
    await members(connection, sales, other, [people["first"]["id"]])
    first = await actor(connection, people["first"]["code"])
    body = await technical_review(connection, first, opportunity)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        response = await client.post(
            "/api/v1/visits", json={**body, "fields": {**body["fields"], "opportunity_id": other["id"]}}
        )
        assert response.status_code == 422 and "关联商机已修改" in response.text
    await set_request_context(connection, first)
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM activity.visit WHERE recorder_user_ref_id=$1::uuid", first.user_id
        )
        == 0
    )
    assert (
        await connection.fetchval(
            "SELECT status FROM agent.artifact WHERE run_id=$1::uuid", body["fields"]["_quality_review_run_id"]
        )
        == "pending_confirm"
    )


async def test_real_archive_receipt_replay_and_supplement_are_rejected_after_project_exit(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    other = await other_project(connection, sales, opportunity)
    await members(connection, sales, other, [people["first"]["id"]])
    first = await actor(connection, people["first"]["code"])
    body = await technical_review(connection, first, opportunity)
    archive_key, supplement_key = str(uuid4()), str(uuid4())
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        response = await client.post("/api/v1/visits", json=body, headers={"Idempotency-Key": archive_key})
        assert response.status_code == 201, response.text
        visit_id = response.json()["id"]
        assert response.json()["fde_participant_ids"] == [first.user_id]
        stored = await client.get("/api/v1/visits/" + visit_id)
        assert stored.status_code == 200 and stored.json()["version_no"] == response.json()["version_no"]
        repeat = await client.post("/api/v1/visits", json=body, headers={"Idempotency-Key": archive_key})
        assert repeat.status_code == 201 and repeat.json() == response.json()
        patch = {"contact_title": "技术负责人", "version_no": response.json()["version_no"]}
        changed = await client.patch(
            "/api/v1/visits/" + visit_id, json=patch, headers={"Idempotency-Key": supplement_key}
        )
        assert changed.status_code == 200, changed.text
        await members(connection, sales, opportunity, [people["second"]["id"]])
        # Same-customer other-project membership retains panorama READ, not record WRITE.
        assert (await client.get("/api/v1/visits/" + visit_id)).status_code == 200
        repeat = await client.post("/api/v1/visits", json=body, headers={"Idempotency-Key": archive_key})
        assert repeat.status_code == 403, repeat.text
        repeat_patch = await client.patch(
            "/api/v1/visits/" + visit_id, json=patch, headers={"Idempotency-Key": supplement_key}
        )
        assert repeat_patch.status_code == 403, repeat_patch.text
    await set_request_context(connection, first)
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM activity.visit WHERE recorder_user_ref_id=$1::uuid", first.user_id
        )
        == 1
    )
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM ops.mutation_receipt WHERE actor_id=$1::uuid AND operation='visits.archive'",
            first.user_id,
        )
        == 1
    )


async def test_conversation_and_run_keep_personal_project_scope_and_reject_unassigned_context(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    other = await other_project(connection, sales, opportunity)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        body = {"mode": "visit_entry", "customer_id": opportunity["customer_id"]}
        for rejected in [body, {**body, "opportunity_id": other["id"]}]:
            response = await client.post("/api/v1/conversations", json=rejected)
            assert response.status_code == 403, response.text
        conversation = await client.post("/api/v1/conversations", json={**body, "opportunity_id": opportunity["id"]})
        assert conversation.status_code == 201, conversation.text
        sent = await client.post(
            "/api/v1/conversations/" + conversation.json()["id"] + "/messages",
            json={"text": "测试本人商机录入范围，不调用模型"},
        )
        assert sent.status_code == 202, sent.text
        context = await connection.fetchrow(
            "SELECT business_context,identity_context FROM agent.run WHERE id=$1::uuid", sent.json()["run_id"]
        )
        assert context["business_context"]["customer_id"] == opportunity["customer_id"]
        assert context["business_context"]["opportunity_id"] == opportunity["id"]
        assert context["identity_context"]["permission_version"]
        await login_fde(connection, client, people["lead"])
        denied = await client.post("/api/v1/conversations", json={**body, "opportunity_id": opportunity["id"]})
        assert denied.status_code == 403, denied.text
