"""Native FDE history filtering and permission epochs; all fixtures roll back."""

from datetime import datetime
import re
from zoneinfo import ZoneInfo

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.fde_dashboard import fde_activity, fde_dashboard
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.agent_access import require_agent_access
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde, members, other_project, own_visit
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio
TZ = ZoneInfo("Asia/Shanghai")


async def permission(connection, person):
    await set_request_context(connection, person)
    return await connection.fetchval("SELECT security.fde_permission_version()")


async def test_history_sql_paging_period_member_and_former_member_summary(connection, monkeypatch):
    # Fixed fixtures must use a fixed reporting clock: the rolling 12-week axis
    # otherwise drops July 1 when the runner crosses into a later calendar week.
    class ReportingClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 9, 13, 12, tzinfo=TZ)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr("sales_backend.repositories.fde_dashboard.datetime", ReportingClock)
    sales, project, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    q3 = [await own_visit(connection, first, project, on=day)
          for day in ("2026-07-01", "2026-09-13", "2026-09-13")]
    await own_visit(connection, first, project, on="2026-06-30")
    await own_visit(connection, first, project, on="2025-09-13")
    await own_visit(connection, first, project, on="2026-10-01")
    second = await actor(connection, people["second"]["code"])
    await own_visit(connection, second, project)
    await set_request_context(connection, first)
    class QueryProbe:
        def __init__(self, underlying):
            self.underlying = underlying
            self.history_rows_returned = []

        async def fetch(self, query, *args):
            rows = await self.underlying.fetch(query, *args)
            if "security.fde_recorded_visit_history" in query:
                self.history_rows_returned.append(len(rows))
            return rows

        async def fetchval(self, query, *args):
            return await self.underlying.fetchval(query, *args)

    probe = QueryProbe(connection)
    first_page = await fde_activity(probe, first, year=2026, quarters=[3], limit=2)
    assert probe.history_rows_returned == [2]  # PostgreSQL returned only the requested page.
    next_page = await fde_activity(connection, first, year=2026, quarters=[3], offset=2, limit=2)
    assert first_page["total"] == next_page["total"] == 3
    assert first_page["next_offset"] == 2 and next_page["next_offset"] is None
    returned = [r["id"] for r in first_page["items"] + next_page["items"]]
    assert set(returned) == {row["id"] for row in q3} and len(returned) == len(set(returned))
    tied = sorted((row["id"] for row in q3[1:]), reverse=True)
    assert returned[:2] == tied  # SQL uses date then UUID as a stable tiebreaker.
    empty = await fde_activity(connection, first, year=2026, quarters=[3], offset=100, limit=2)
    assert empty["items"] == [] and empty["total"] == 3 and not empty["has_more"]
    lead = await actor(connection, people["lead"]["code"])
    selected = await fde_activity(connection, lead, scope="team", member_id=second.user_id,
                                  year=2026, quarters=[3], limit=1)
    assert selected["total"] == 1 and selected["items"][0]["recorder_user_id"] == second.user_id
    assert (await fde_activity(connection, lead, scope="self", year=2026, quarters=[3]))["total"] == 0
    team = await fde_dashboard(connection, lead, year=2026, quarters=[3])
    assert team["summary"]["period_visits"] == 4 and team["summary"]["active_recorders"] == 2
    assert sum(week["visits"] for week in team["rhythm_weeks"]) == 4
    assert sum(row["visits"] for row in team["ranking"]) == 4
    await members(connection, sales, project, [second.user_id])
    await set_request_context(connection, first)
    exited = await fde_activity(connection, first, year=2026, quarters=[3], limit=2)
    assert exited["total"] == 3 and all(not row["can_read_detail"] for row in exited["items"])
    assert all("follow_up_record" not in row for row in exited["items"])


async def test_parameterized_history_never_widens_authorization_and_legacy_projection_matches(connection):
    _, project, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    await own_visit(connection, first, project)
    second = await actor(connection, people["second"]["code"])
    await own_visit(connection, second, project)
    await set_request_context(connection, first)
    query = "SELECT * FROM security.fde_recorded_visit_history($1,$2,$3::uuid,$4::uuid,$5::int[])"
    assert await connection.fetch(query, None, None, second.user_id, None, None) == []
    projected = await connection.fetch(query, None, None, None, None, None)
    assert projected == await connection.fetch("SELECT * FROM security.fde_recorded_visit_history()")
    assert await connection.fetch(query, datetime(2026, 10, 1, tzinfo=TZ), None, None, None, None) == []


async def test_unknown_historical_date_remains_unknown_in_all_history(connection):
    _, project, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    known = await own_visit(connection, first, project)
    unknown = await own_visit(connection, first, project)
    # A legacy row may predate mandatory dates. Do not backfill a fabricated date.
    await connection.execute("UPDATE activity.visit SET interaction_at=NULL WHERE id=$1::uuid", unknown["id"])
    result = await fde_activity(connection, first, all_history=True)
    assert [row["id"] for row in result["items"]] == [known["id"], unknown["id"]]
    assert result["items"][-1]["interaction_at"] is None
    assert (await fde_dashboard(connection, first, year=2026, quarters=[3]))["summary"]["period_visits"] == 1


async def test_profile_and_unrelated_participation_edits_do_not_invalidate_current_fde_analysis(connection):
    sales, project, people, _ = await fde_fixture(connection)
    other_sales, other, others, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    lead = await actor(connection, people["lead"]["code"])
    before, lead_before = await permission(connection, first), await permission(connection, lead)
    await actor(connection, "ADMIN001")
    for person in (people["first"], people["second"], others["first"]):
        await connection.execute(
            "UPDATE platform.user_ref SET display_name=display_name||' edited',version_no=version_no+1 "
            "WHERE id=$1::uuid", person["id"]
        )
    await members(connection, other_sales, other, [others["second"]["id"]])
    assert await permission(connection, first) == before
    assert await permission(connection, lead) == lead_before
    await set_request_context(connection, first)
    await require_agent_access(connection, first, "visit_entry", project["customer_id"],
                               opportunity_id=project["id"], permission_version=before)
    # A peer leaving our shared project changes the lead's managed relations, but
    # does not remove this member's own authorization for the same customer.
    await members(connection, sales, project, [first.user_id])
    assert await permission(connection, first) == before
    assert await permission(connection, lead) != lead_before


async def test_related_revocation_and_customer_panorama_membership_change_invalidate_snapshots(connection):
    sales, project, people, team = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    initial = await permission(connection, first)
    sibling = await other_project(connection, sales, project)
    expanded = await permission(connection, first)
    assert expanded != initial
    assert await connection.fetchval("SELECT security.has_opportunity_read_access($1::uuid)", sibling["id"])
    await actor(connection, "ADMIN001")
    # Business roles cannot soft-delete opportunities. Simulate a maintenance
    # deletion only in the harness's disposable database, then restore runtime RLS.
    runtime = await connection.fetchval("SELECT current_user")
    if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", runtime):
        pytest.skip("Requires the disposable non-bypass database harness")
    await connection.execute("RESET ROLE")
    try:
        await connection.execute("UPDATE crm.opportunity SET deleted_at=clock_timestamp() WHERE id=$1::uuid", sibling["id"])
    finally:
        await connection.execute(f'SET LOCAL ROLE "{runtime}"')
    assert await permission(connection, first) != expanded
    await members(connection, sales, project, [people["second"]["id"]])
    assert await permission(connection, first) != initial
    with pytest.raises(PermissionError):
        await require_agent_access(connection, first, "operating_report", project["customer_id"],
                                   permission_version=initial)
    await members(connection, sales, project, [first.user_id, people["second"]["id"]])
    before_department = await permission(connection, first)
    await actor(connection, "ADMIN001")
    await connection.execute("UPDATE platform.team SET status='inactive' WHERE id=$1::uuid", team)
    assert await permission(connection, first) != before_department
    assert not await connection.fetchval("SELECT security.is_fde_actor()")


@pytest.mark.parametrize("mutation", [
    "UPDATE platform.user_ref SET status='inactive' WHERE id=$1::uuid",
    "UPDATE platform.role_binding SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid AND role_code='fde'",
    "UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid AND membership_role='fde'",
])
async def test_related_identity_revocation_invalidates_analysis(connection, mutation):
    _, project, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    before = await permission(connection, first)
    await actor(connection, "ADMIN001")
    await connection.execute(mutation, first.user_id)
    assert await permission(connection, first) != before
    assert not await connection.fetchval("SELECT security.is_fde_actor()")
    with pytest.raises(PermissionError):
        await require_agent_access(connection, first, "operating_report", project["customer_id"],
                                   permission_version=before)


async def test_removing_one_project_invalidates_snapshot_while_other_project_keeps_customer_readable(connection):
    sales, project, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    sibling = await other_project(connection, sales, project)
    await members(connection, sales, sibling, [first.user_id])
    before = await permission(connection, first)
    await members(connection, sales, project, [people["second"]["id"]])
    assert await permission(connection, first) != before
    assert await connection.fetchval("SELECT security.has_customer_access($1::uuid)", project["customer_id"])
    assert await connection.fetchval("SELECT security.has_opportunity_read_access($1::uuid)", project["id"])
    assert not await connection.fetchval("SELECT security.fde_can_record_opportunity($1::uuid)", project["id"])
    with pytest.raises(PermissionError):
        await require_agent_access(connection, first, "operating_report", project["customer_id"],
                                   permission_version=before)


@pytest.mark.parametrize("empty_date", [None, "", "   "])
async def test_supplement_http_cannot_clear_archived_visit_date(connection, empty_date):
    _, project, people, _ = await fde_fixture(connection)
    first = await actor(connection, people["first"]["code"])
    visit = await own_visit(connection, first, project)
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        original = await VisitRepository().detail(connection, visit["id"])
        response = await client.patch("/api/v1/visits/" + visit["id"], json={
            "version_no": original["version_no"], "interaction_at": empty_date,
        })
        assert response.status_code == 422 and "不能清空" in response.text
        current = await VisitRepository().detail(connection, visit["id"])
        assert current["interaction_at"] == original["interaction_at"]
        assert current["version_no"] == original["version_no"]
        assert (await fde_dashboard(connection, first, year=2026, quarters=[3]))["summary"]["period_visits"] == 1
