"""New Web range reads through real HTTP, native route permissions and original RLS."""

from uuid import uuid4

import pytest

from sales_backend.db import set_request_context
from tests.integration.feishu_fixtures import seed_execute, seed_fetchval
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_customer_map_projection import formal_visit
from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login
from tests.integration.test_synthetic_trial_boundaries import customer as trial_customer

pytestmark = pytest.mark.asyncio
PATH = "/api/v1/visits/range"


async def saved_record(connection, code="XS001", day="2026-10-15", *, text="范围联调记录", score=80):
    project = await opportunity(connection, code, 100000)
    who = await actor(connection, code)
    record = await formal_visit(connection, who, project["customer_id"], day, project["id"])
    await seed_execute(
        connection,
        "UPDATE activity.visit SET follow_up_record=$2,follow_up_score=$3 WHERE id=$1::uuid",
        record["id"],
        text,
        score,
    )
    return who, project, record


async def login(client, code):
    if code == "ADMIN001":
        await sign_in(client, code)
    else:
        await business_login(client, code)


async def read(client, **params):
    response = await client.get(PATH, params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("code", ["XS001", "XS002", "ZJ001", "ZJL001", "ADMIN001"])
async def test_range_equals_original_rls_for_sales_team_and_admin(connection, code):
    first, first_project, one = await saved_record(connection, "XS001")
    second, _, two = await saved_record(connection, "XS002")
    current = await actor(connection, code)
    expected = {
        str(row["id"])
        for row in await connection.fetch(
            "SELECT id FROM activity.visit WHERE workspace_id=common.current_workspace_id() "
            "AND deleted_at IS NULL AND status IN ('confirmed','archived')"
        )
    }
    async with await client_for(connection, auth_mode="demo") as client:
        await login(client, code)
        result = await read(client)
        assert {row["id"] for row in result["items"]} == expected
        assert result["summary"]["total"] == len(expected)
        if code == "XS001":
            assert str(one["id"]) in expected and str(two["id"]) not in expected
        elif code == "XS002":
            assert str(two["id"]) in expected and str(one["id"]) not in expected
        else:
            assert {str(one["id"]), str(two["id"])} <= expected
        mine = await read(client, scope="mine")
        assert all(row["recorder_id"] == current.user_id for row in mine["items"])
        narrowed = await read(client, recorder_ids=[first.user_id], team_id=first.team_ids[0])
        assert {row["id"] for row in narrowed["items"]} <= expected
        assert all(row["recorder_id"] == first.user_id for row in narrowed["items"])
        # Flags reflect existing detail entry permissions, never a new grant.
        for row in result["items"]:
            assert row["can_read_detail"] is True
            assert (await client.get("/api/v1/visits/" + row["id"])).status_code == 200
            if row["can_open_customer"]:
                assert (await client.get("/api/v1/customers/" + row["customer_id"])).status_code == 200
            if row["can_open_opportunity"]:
                assert (
                    await client.get("/api/v1/opportunities/" + row["opportunity_id"] + "/header")
                ).status_code == 200


async def test_dates_pagination_empty_search_and_summary_truncation(connection):
    who, project, first = await saved_record(connection, day="2026-10-15", text="特定%_文字" + "长" * 800, score=None)
    await set_request_context(connection, who)
    for day in ("2026-10-15", "2026-10-16", "2026-10-14"):
        await formal_visit(connection, who, project["customer_id"], day)
    # Pending and deleted rows must not contribute to either the page or totals.
    pending = await formal_visit(connection, who, project["customer_id"], "2026-10-15")
    await seed_execute(
        connection, "UPDATE activity.visit SET status='pending_confirm' WHERE id=$1::uuid", pending["id"]
    )
    deleted = await formal_visit(connection, who, project["customer_id"], "2026-10-15")
    await seed_execute(
        connection, "UPDATE activity.visit SET deleted_at=clock_timestamp() WHERE id=$1::uuid", deleted["id"]
    )
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        params = {
            "customer_id": project["customer_id"],
            "date_from": "2026-10-15",
            "date_to": "2026-10-15",
            "page_size": 1,
        }
        one = await read(client, **params)
        two = await read(client, **params, offset=1)
        assert one["summary"] == two["summary"] == {"total": 2, "subject_count": 1, "scored_count": 1}
        assert one["has_more"] is True and one["next_offset"] == 1
        assert two["has_more"] is False and two["next_offset"] is None
        assert one["items"][0]["id"] != two["items"][0]["id"]
        assert all(row["visit_date"] == "2026-10-15" for row in one["items"] + two["items"])
        empty = await read(client, **params, offset=2)
        assert empty["items"] == [] and empty["summary"]["total"] == 2 and not empty["has_more"]
        searched = await read(client, q="%_", customer_id=project["customer_id"])
        assert [row["id"] for row in searched["items"]] == [str(first["id"])]
        assert len(searched["items"][0]["follow_up_record"]) == 600
        assert (
            not {"import_meta", "archived_fields", "attributes", "audio_asset_id", "first_visit_profile"}
            & searched["items"][0].keys()
        )
        created_day = str(await connection.fetchval("SELECT timezone('Asia/Shanghai',clock_timestamp())::date"))
        created = await read(
            client,
            customer_id=project["customer_id"],
            date_basis="created",
            date_from=created_day,
            date_to=created_day,
        )
        assert str(first["id"]) in {row["id"] for row in created["items"]}
        assert created["summary"]["total"] == 4
        none = await read(client, date_from="1990-01-01", date_to="1990-01-02")
        assert none["items"] == [] and none["summary"] == {"total": 0, "subject_count": 0, "scored_count": 0}


async def test_cross_company_and_unknown_recorder_team_filters_never_expand(connection):
    who, project, own = await saved_record(connection)
    foreign_workspace, foreign_customer, foreign_user, foreign_visit = [uuid4() for _ in range(4)]
    await seed_execute(
        connection,
        "INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1::uuid,$1::text,'范围隔离其他公司')",
        foreign_workspace,
    )
    await seed_execute(
        connection,
        "INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name) "
        "VALUES($1::uuid,$2::uuid,$1::text,'不可见用户')",
        foreign_user,
        foreign_workspace,
    )
    await seed_execute(
        connection,
        "INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id) "
        "VALUES($1,$2,'不可见客户','不可见客户',$3)",
        foreign_customer,
        foreign_workspace,
        foreign_user,
    )
    await seed_execute(
        connection,
        """INSERT INTO activity.visit(id,workspace_id,customer_id,recorder_user_ref_id,
        created_by_user_ref_id,form_version_id,status,interaction_at,follow_up_record)
        VALUES($1,$2,$3,$4,$4,(SELECT id FROM config.form_version WHERE status='active'
        ORDER BY version_no DESC LIMIT 1),
        'archived','2026-10-15 09:00+08','不可见正文')""",
        foreign_visit,
        foreign_workspace,
        foreign_customer,
        foreign_user,
    )
    async with await client_for(connection, auth_mode="demo") as client:
        for code in ("XS001", "ZJ001", "ADMIN001"):
            await login(client, code)
            result = await read(client)
            assert str(foreign_visit) not in str(result) and "不可见正文" not in str(result)
            for filters in (
                {"recorder_ids": [str(foreign_user)]},
                {"team_id": str(uuid4())},
                {"customer_id": str(foreign_customer)},
                {"opportunity_id": str(uuid4())},
            ):
                narrowed = await read(client, **filters)
                assert narrowed["items"] == [] and narrowed["summary"]["total"] == 0
        await set_request_context(connection, who.model_copy(update={"workspace_id": str(foreign_workspace)}))
        assert not await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1::uuid", own["id"])


async def test_synthetic_visibility_provenance_and_business_rows_remain_unchanged(connection):
    who = await actor(connection, "XS001")
    cid, marker = await trial_customer(connection, who)
    before = await seed_fetchval(connection, "SELECT to_jsonb(c) FROM crm.customer c WHERE id=$1", cid)
    await set_request_context(connection, who)
    visit = await formal_visit(connection, who, str(cid), "2026-10-15")
    original = await connection.fetchval("SELECT to_jsonb(v) FROM activity.visit v WHERE id=$1::uuid", visit["id"])
    assert original["import_meta"]["synthetic_trial"] == marker
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        ranged = await read(client, customer_id=str(cid))
        legacy = await client.get("/api/v1/visits", params={"customer_id": str(cid)})
        assert legacy.status_code == 200, legacy.text
        assert (
            {row["id"] for row in ranged["items"]}
            == {row["id"] for row in legacy.json()["items"]}
            == {str(visit["id"])}
        )
        assert not {"synthetic_trial", "data_kind", "import_meta"} & ranged["items"][0].keys()
        assert (
            await connection.fetchval("SELECT to_jsonb(v) FROM activity.visit v WHERE id=$1::uuid", visit["id"])
            == original
        )
        assert await seed_fetchval(connection, "SELECT to_jsonb(c) FROM crm.customer c WHERE id=$1", cid) == before


async def test_current_permission_revocation_blocks_range_without_changing_roles(connection):
    who, project, visit = await saved_record(connection)
    admin = await actor(connection, "ADMIN001")
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        assert (await read(client))["summary"]["total"] >= 1
        await grant(connection, admin, who.user_id, [{"permission": "visit.read", "effect": "deny"}])
        response = await client.get(PATH)
        assert response.status_code == 403, response.text
        await set_request_context(connection, who)
        assert not await connection.fetchval("SELECT count(*) FROM activity.visit WHERE id=$1::uuid", visit["id"])


@pytest.mark.parametrize(
    "params",
    [
        {"scope": "workspace"},
        {"date_basis": "unknown"},
        {"page_size": 0},
        {"page_size": 101},
        {"offset": -1},
        {"offset": 100001},
        {"team_id": "not-uuid"},
        {"recorder_ids": ["not-uuid"]},
        {"date_from": "2026-10-16", "date_to": "2026-10-15"},
        {"date_to": "9999-12-31"},
        {"q": "长" * 101},
    ],
)
async def test_invalid_range_parameters_are_422(connection, params):
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get(PATH, params=params)
        assert response.status_code == 422, response.text


async def test_multiple_opportunity_links_count_one_record_and_filter_both_customers(connection):
    first = await opportunity(connection, "XS001", 100000)
    who = await actor(connection, "XS001")
    visit = await formal_visit(connection, who, first["customer_id"], "2026-10-15")
    second = await opportunity(connection, "XS001", 200000)
    await seed_execute(
        connection,
        """INSERT INTO activity.visit_opportunity(workspace_id,visit_id,opportunity_id)
        SELECT workspace_id,$1::uuid,id FROM crm.opportunity WHERE id=ANY($2::uuid[])""",
        visit["id"],
        [first["id"], second["id"]],
    )
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        result = await read(client)
        assert result["summary"] == {"total": 1, "subject_count": 2, "scored_count": 1}
        row = result["items"][0]
        assert row["id"] == str(visit["id"])
        assert {project["id"] for project in row["linked_opportunities"]} == {first["id"], second["id"]}
        for project in (first, second):
            assert [row["id"] for row in (await read(client, customer_id=project["customer_id"]))["items"]] == [
                str(visit["id"])
            ]
            assert [row["id"] for row in (await read(client, opportunity_id=project["id"]))["items"]] == [
                str(visit["id"])
            ]


async def test_visible_visit_does_not_grant_customer_or_opportunity_detail(connection):
    who, project, _ = await saved_record(connection)
    await set_request_context(connection, who)
    visit = await formal_visit(connection, who, project["customer_id"], "2026-10-15")
    admin = await actor(connection, "ADMIN001")
    await grant(
        connection,
        admin,
        who.user_id,
        [
            {"permission": "customer.read", "effect": "deny"},
            {"permission": "opportunity.read", "effect": "deny"},
        ],
    )
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        result = await read(client)
        visible = next(row for row in result["items"] if row["id"] == str(visit["id"]))
        assert visible["can_open_customer"] is False and visible["can_open_opportunity"] is False
        assert visible["can_read_detail"] is True
        assert (await client.get("/api/v1/visits/" + str(visit["id"]))).status_code == 200
        assert (await client.get("/api/v1/customers/" + project["customer_id"])).status_code == 403
        assert (await client.get("/api/v1/opportunities/" + project["id"] + "/header")).status_code == 403
