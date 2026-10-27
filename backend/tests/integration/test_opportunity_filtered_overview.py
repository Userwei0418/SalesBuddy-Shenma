"""Real PostgreSQL: one authorized, filtered set supplies pages and overview."""

from datetime import date
from uuid import uuid4

import asyncpg
import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.repositories.visits import VisitRepository
from tests.integration.feishu_fixtures import seed_execute, seed_fetchval
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def test_filtered_overview_is_full_set_and_keeps_all_business_filters(connection):
    project = await opportunity(connection, "XS001", 1000000, close=date(2026, 3, 31))
    own = await actor(connection, "XS001")
    prefix = "组合筛选-" + uuid4().hex
    await connection.execute(
        "UPDATE crm.opportunity SET name=$2,product_line='产品甲',created_at='2026-07-01' WHERE id=$1::uuid",
        project["id"], prefix,
    )
    await connection.execute(
        """INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
          created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code,product_line,created_at)
        SELECT workspace_id,customer_id,$2||n,owner_user_ref_id,owner_team_id,
          created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code,product_line,created_at
        FROM crm.opportunity CROSS JOIN generate_series(1,24) n WHERE id=$1::uuid""",
        project["id"], prefix,
    )
    filters = dict(
        query=prefix, owner_id=own.user_id, stages=["identified"], grade="A", product_line="产品甲",
        year=2026, quarters=[1], include_closed=True, include_overview=True, overview_year=2026,
        overview_quarters=[3],
    )
    repo = OpportunityRepository()
    own = await actor(connection, "ZJL001")
    first = await repo.page(connection, own, limit=1, **filters)
    filters["team_id"] = first["items"][0]["team_id"]
    first = await repo.page(connection, own, limit=1, **filters)
    second = await repo.page(connection, own, limit=1, offset=24, **filters)
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["summary"]["total"] == first["overview"]["metrics"]["total"] == 25
    assert second["overview"] == first["overview"]
    assert first["overview"]["metrics"]["newCount"] == 25
    assert first["overview"]["definition_version"] == "opportunity_filtered_overview_v1"
    # Creation in Q3 and planned close in Q1 are independent predicates.
    other_period = await repo.page(connection, own, limit=1, **{**filters, "overview_quarters": [1]})
    assert other_period["summary"]["total"] == 25 and other_period["overview"]["metrics"]["newCount"] == 0
    for changed in ({"quarters": [2]}, {"grade": "B"}, {"product_line": "产品乙"}, {"stages": ["won"]}):
        empty = await repo.page(connection, own, limit=1, **{**filters, **changed})
        assert empty["summary"]["total"] == empty["overview"]["metrics"]["total"] == 0
        assert empty["overview"]["metrics"]["newCount"] == 0
    # Ordinary clients keep their response shape; HTTP validates and forwards opt-in.
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        legacy = await client.get("/api/v1/opportunities", params={"q": prefix, "page_size": 1})
        assert legacy.status_code == 200 and "overview" not in legacy.json()
        result = await client.get("/api/v1/opportunities", params={
            "q": prefix, "page_size": 1, "include_overview": True, "overview_year": 2026,
            "overview_quarters": [3], "year": 2026, "quarters": [1],
        })
        assert result.status_code == 200, result.text
        assert result.json()["overview"]["metrics"]["newCount"] == 25


async def test_filtered_overview_formal_followups_actual_wins_and_unknown_history(connection):
    projects = [await opportunity(connection, "XS001", 100, status, close=date(2026, 3, 31))
                for status in ("open", "won", "lost", "won")]
    ids = [project["id"] for project in projects]
    prefix = "日期验收-" + uuid4().hex
    await seed_execute(connection, """UPDATE crm.opportunity SET name=$2,
        created_at='2026-03-31T16:00:00Z',closed_at=CASE WHEN status='won'
        THEN '2026-06-30T16:00:00Z'::timestamptz ELSE closed_at END WHERE id=ANY($1::uuid[])""", ids, prefix)
    await seed_execute(connection, "UPDATE crm.opportunity SET closed_at=NULL WHERE id=$1::uuid", ids[3])
    own = await actor(connection, "XS001")
    for project, when in [(projects[0], "2026-07-01"), (projects[0], "2026-09-01"),
                          (projects[1], "2026-06-30"), (projects[2], "2026-07-01")]:
        await VisitRepository().create(connection, own, customer_id=project["customer_id"], fields={
            "opportunity_id": project["id"], "interaction_at": when, "created_date": "2026-09-12",
            "contact_name": "隔离联系人", "follow_up_record": "客户确认方案", "next_action": "安排复核",
            "_follow_up_quality_score": 85,
        })
    # Imported source day is read without replacing the audit creation timestamp.
    await actor(connection, "OPS001")
    await connection.execute("SELECT set_config('app.feishu_historical_import','on',true)")
    for index, source in enumerate(({"商机创建时间": "2026/07/02"}, {}, {"商机创建时间": "2026-02-30"})):
        await seed_fetchval(connection, """INSERT INTO crm.opportunity(workspace_id,customer_id,name,
          owner_user_ref_id,owner_team_id,created_by_user_ref_id,stage_code,status,amount,probability,
          expected_close_date,source_code,created_at,import_meta)
          SELECT workspace_id,customer_id,$2,owner_user_ref_id,owner_team_id,created_by_user_ref_id,
          stage_code,status,amount,probability,expected_close_date,source_code,'2029-01-01',
          $3::jsonb FROM crm.opportunity WHERE id=$1::uuid RETURNING id""", ids[0], prefix + str(index),
          {"import_type": "crm_history", "source_fields": source})
    await connection.execute("SELECT set_config('app.feishu_historical_import','off',true)")
    own = await actor(connection, "XS001")
    repo = OpportunityRepository()
    common = dict(limit=1, query=prefix, include_closed=True, include_overview=True, overview_year=2026)
    q3 = (await repo.page(connection, own, **common, overview_quarters=[3]))["overview"]
    assert q3["metrics"] == {
        "total": 7, "won": 1, "active": 1, "newCount": 1, "missingWonDates": 1,
        "missingCloseDates": 0, "missingCreatedDates": 2, "demo_scene_count": 0,
    }
    year = (await repo.page(connection, own, **common, overview_quarters=[1, 2, 3, 4]))["overview"]
    all_time = (await repo.page(connection, own, **common, overview_quarters=[]))["overview"]
    assert year["metrics"]["newCount"] == all_time["metrics"]["newCount"] == 5
    assert year["metrics"]["won"] == 1 and all_time["metrics"]["won"] == 2
    assert all_time["metrics"]["active"] == 2  # Two visits for one opportunity count once; lost is excluded.
    assert "creation_date_facts" not in str(q3)
    assert await connection.fetchval(
        "SELECT count(*) FROM crm.opportunity WHERE strpos(name,$1)=1 AND extract(year FROM created_at)=2029", prefix,
    ) == 3


async def test_filtered_overview_cannot_expand_sales_or_tenant_scope(connection):
    own_project = await opportunity(connection, "XS001", 100)
    other_project = await opportunity(connection, "XS002", 200)
    other_owner = await actor(connection, "XS002")
    repo = OpportunityRepository()
    owner = await actor(connection, "XS001")
    common = dict(limit=1, include_closed=True, include_overview=True, overview_year=2026)
    for filters in ({"owner_id": other_owner.user_id}, {"customer_id": other_project["customer_id"]}):
        hidden = await repo.page(connection, owner, **common, **filters)
        assert hidden["items"] == [] and hidden["overview"]["metrics"]["total"] == 0
    own = await repo.page(connection, owner, **common, customer_id=own_project["customer_id"])
    assert own["overview"]["metrics"]["total"] == 1
    await connection.execute("SELECT set_config('app.workspace_id',$1,true)", str(uuid4()))
    # Identity validation rejects a stale/mismatched tenant before any list or
    # overview query; the extension must retain that fail-closed behavior.
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="不属于当前公司"):
        await repo.page(connection, owner, **common)


async def test_filtered_overview_fde_participation_and_member_selection(connection):
    _, project, people, _ = await fde_fixture(connection)
    repo = OpportunityRepository()
    common = dict(limit=1, include_closed=True, include_overview=True, overview_year=2026)
    member = await actor(connection, people["first"]["code"])
    own = await repo.page(connection, member, **common)
    assert own["overview"]["metrics"]["total"] == own["summary"]["total"] == 1
    lead = await actor(connection, people["lead"]["code"])
    team = await repo.page(connection, lead, **common, scope="team",
                           member_ids=[people["first"]["id"], people["second"]["id"]])
    assert team["overview"]["metrics"]["total"] == 1  # Shared project remains one fact.
    assert team["items"][0]["id"] == project["id"]
    await set_request_context(connection, member)
    with pytest.raises(PermissionError):
        await repo.page(connection, member, **common, member_id=people["second"]["id"])
