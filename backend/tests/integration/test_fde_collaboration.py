"""Real SQL checks for participation attribution, project scope and optimistic mutations."""

from datetime import date
from decimal import Decimal

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.collaboration import archive_fde_collaboration, effective_ids
from sales_backend.repositories.fde_dashboard import fde_activity, fde_dashboard
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.collaboration import set_opportunity_members
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def record_visit(connection, sales, opportunity, people):
    await set_request_context(connection, sales)
    visit = await VisitRepository().create(
        connection,
        sales,
        customer_id=opportunity["customer_id"],
        fields={
            "opportunity_id": opportunity["id"],
            "interaction_at": "2026-09-13",
            "created_date": "2026-09-13",
            "contact_name": "业务联系人",
            "follow_up_record": "两位FDE与销售一起核对试点验收结果",
            "next_action": "9月15日由销售提交试点方案",
            "_follow_up_quality_score": 85,
            "_fde_participant_ids": [p["id"] for p in people],
        },
    )
    await archive_fde_collaboration(connection, sales, visit, [p["id"] for p in people])
    return visit


async def test_member_changes_noop_version_conflict_and_commercial_fields_are_independent(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    await set_request_context(connection, sales)
    version = await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", opportunity["id"])
    ids = [people["first"]["id"], people["second"]["id"]]
    same = await set_opportunity_members(connection, sales, opportunity["id"], ids, version)
    assert not same["changed"] and same["version_no"] == version
    lead = await actor(connection, people["lead"]["code"])
    changed = await set_opportunity_members(connection, lead, opportunity["id"], ids[1:], version)
    assert changed["changed"] and changed["version_no"] == version + 1
    assert await effective_ids(connection, opportunity["id"]) == {ids[1]}
    with pytest.raises(PermissionError):
        await save_opportunity(
            connection,
            lead,
            customer_id=opportunity["customer_id"],
            data={
                "action": "update",
                "opportunity_id": opportunity["id"],
                "version_no": version + 1,
                "amount": Decimal(10),
            },
        )
    removed = await actor(connection, people["first"]["code"])
    assert await OpportunityRepository().detail(connection, removed, opportunity["id"]) is None
    assert (
        await connection.fetchval(
            "SELECT count(*) FROM workflow.notification WHERE template_code='fde_removed' "
            "AND recipient_user_ref_id=$1::uuid",
            removed.user_id,
        )
        == 1
    )


async def test_personal_archives_exclude_sales_attendance_and_exit_keeps_own_summary(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    await record_visit(connection, sales, opportunity, [people["first"], people["second"]])
    first = await actor(connection, people["first"]["code"])
    before = await fde_dashboard(connection, first, year=2026, quarters=[3])
    assert before["summary"]["period_visits"] == 0
    visit = await record_visit(connection, first, opportunity, [people["first"]])
    second = await actor(connection, people["second"]["code"])
    await record_visit(connection, second, opportunity, [people["second"]])
    await set_request_context(connection, first)
    own = await fde_dashboard(connection, first, year=2026, quarters=[3])
    assert own["summary"]["period_visits"] == 1 and own["summary"]["period_visit_people"] == 1
    assert own["summary"]["open_acv"] == Decimal(100000)
    lead = await actor(connection, people["lead"]["code"])
    team = await fde_dashboard(connection, lead, year=2026, quarters=[3])
    assert team["summary"]["period_visits"] == 2 and team["summary"]["active_recorders"] == 2
    assert team["summary"]["opportunities"] == 1 and team["summary"]["open_acv"] == Decimal(100000)
    assert sum(r["visits"] for r in team["ranking"]) == 2
    await set_request_context(connection, sales)
    version = await connection.fetchval("SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", opportunity["id"])
    await set_opportunity_members(connection, sales, opportunity["id"], [people["second"]["id"]], version)
    await set_request_context(connection, first)
    after = await fde_dashboard(connection, first, year=2026, quarters=[3])
    assert after["summary"]["opportunities"] == 0 and after["summary"]["period_visits"] == 1
    history = await fde_activity(connection, first, year=2026, quarters=[3])
    item = next(r for r in history["items"] if r["id"] == visit["id"])
    assert item["can_read_detail"] is False and "follow_up_record" not in item
    with pytest.raises(LookupError):
        await VisitRepository().detail(connection, visit["id"])


async def test_full_customer_opportunity_read_does_not_count_as_assistance(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    await set_request_context(connection, sales)
    other = await save_opportunity(
        connection,
        sales,
        customer_id=opportunity["customer_id"],
        data={
            "name": "同客户另一个部门的独立项目",
            "amount": Decimal(900),
            "probability": 10,
            "expected_close_date": date(2026, 12, 20),
        },
    )
    first = await actor(connection, people["first"]["code"])
    assert await OpportunityRepository().detail(connection, first, other["id"])
    view = await fde_dashboard(connection, first, year=2026, quarters=[3])
    assert view["summary"]["opportunities"] == 1 and view["summary"]["open_acv"] == Decimal(100000)
    with pytest.raises(PermissionError):
        await fde_dashboard(connection, first, scope="team")


async def test_attendance_only_adds_missing_members_never_removes_absent_member(connection):
    sales, opportunity, people, _ = await fde_fixture(connection)
    await record_visit(connection, sales, opportunity, [people["first"]])
    assert await effective_ids(connection, opportunity["id"]) == {people["first"]["id"], people["second"]["id"]}
    second = await actor(connection, people["second"]["code"])
    view = await fde_dashboard(connection, second, year=2026, quarters=[3])
    assert view["summary"]["period_visits"] == 0 and view["summary"]["opportunities"] == 1
