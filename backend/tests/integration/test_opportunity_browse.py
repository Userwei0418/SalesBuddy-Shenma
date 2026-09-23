"""Real PostgreSQL pagination, full-set totals, filters and FDE scope contracts."""

from datetime import date
from decimal import Decimal

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


async def test_browse_first_twenty_complete_totals_facets_and_stable_all_pages(connection):
    op = await opportunity(connection, "XS001", 100, close=date(2026, 3, 31))
    owner = await actor(connection, "XS001")
    await connection.execute(
        """INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
          created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code,product_line)
        SELECT workspace_id,customer_id,'浏览分页-'||n,owner_user_ref_id,owner_team_id,
          created_by_user_ref_id,'solution','open',100,50,expected_close_date,source_code,
          CASE WHEN n=80 THEN '最后一页产品线' ELSE '通用产品' END
        FROM crm.opportunity CROSS JOIN generate_series(1,80) n WHERE id=$1::uuid""",
        op["id"],
    )
    repo = OpportunityRepository()
    first = await repo.page(connection, owner, customer_id=op["customer_id"], limit=20, include_closed=True)
    assert len(first["items"]) == 20 and first["has_more"]
    assert first["summary"] == {"total": 81, "open_count": 81, "open_amount": 8100, "unknown_open_amount_count": 0}
    assert "最后一页产品线" in first["facets"]["product_lines"]
    ids = [r["id"] for r in first["items"]]
    current = first
    while current["has_more"]:
        current = await repo.page(
            connection,
            owner,
            customer_id=op["customer_id"],
            limit=20,
            include_closed=True,
            offset=current["next_offset"],
        )
        assert current["summary"] == first["summary"]
        ids.extend(r["id"] for r in current["items"])
    assert len(ids) == len(set(ids)) == 81
    empty = await repo.page(connection, owner, customer_id=op["customer_id"], limit=20, offset=100)
    assert empty["summary"]["total"] == 81 and empty["items"] == [] and not empty["has_more"]
    filtered = await repo.page(
        connection,
        owner,
        customer_id=op["customer_id"],
        limit=20,
        product_line="最后一页产品线",
        stages=["solution"],
        year=2026,
        quarters=[1, 3],
    )
    assert len(filtered["items"]) == filtered["summary"]["total"] == 1
    assert filtered["summary"]["open_amount"] == 100
    literal = await repo.page(connection, owner, customer_id=op["customer_id"], limit=20, query="%")
    assert literal["summary"]["total"] == 0


@pytest.mark.parametrize(
    "amounts,expected,unknown", [([], 0, 0), ([0, 100], 100, 0), ([None, 100], None, 1), ([None, None], None, 2)]
)
async def test_browse_unknown_amount_is_not_zero(connection, amounts, expected, unknown):
    op = await opportunity(connection, "XS001", 100)
    owner = await actor(connection, "XS001")
    await connection.execute(
        "UPDATE crm.opportunity SET amount=$2::numeric WHERE id=$1::uuid", op["id"], amounts[0] if amounts else None
    )
    if len(amounts) == 2:
        await connection.execute(
            """INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
              created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code)
            SELECT workspace_id,customer_id,'金额未知项目二',owner_user_ref_id,owner_team_id,
              created_by_user_ref_id,stage_code,status,$2,probability,expected_close_date,source_code
            FROM crm.opportunity WHERE id=$1::uuid""",
            op["id"],
            amounts[1],
        )
    page = await OpportunityRepository().page(
        connection, owner, customer_id=op["customer_id"], limit=1, query="不存在的项目" if not amounts else None
    )
    assert page["summary"]["open_amount"] == expected
    assert page["summary"]["unknown_open_amount_count"] == unknown
    assert page["summary"]["total"] == len(amounts)


async def test_browse_filters_full_set_and_roles_do_not_leak_options(connection):
    first = await opportunity(connection, "XS001", 1000000, close=date(2026, 3, 31))
    await connection.execute("UPDATE crm.opportunity SET product_line='销售甲独占' WHERE id=$1::uuid", first["id"])
    second = await opportunity(connection, "XS002", 500000, close=date(2026, 4, 1))
    await connection.execute("UPDATE crm.opportunity SET product_line='销售乙独占' WHERE id=$1::uuid", second["id"])
    own = await actor(connection, "XS001")
    repo = OpportunityRepository()
    result = await repo.page(connection, own, limit=1, product_line="销售乙独占")
    assert result["summary"]["total"] == 0 and "销售乙独占" not in result["facets"]["product_lines"]
    manager = await actor(connection, "ZJL001")
    result = await repo.page(
        connection,
        manager,
        limit=1,
        stages=["identified"],
        grade="A",
        year=2026,
        quarters=[1],
        product_line="销售甲独占",
    )
    assert first["id"] in {r["id"] for r in result["items"]}
    assert second["id"] not in {r["id"] for r in result["items"]}
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        response = await client.get(
            "/api/v1/opportunities",
            params={
                "customer_id": first["customer_id"],
                "page_size": 1,
                "quarters": [1],
                "year": 2026,
                "grade": "A",
                "order": "quarter_stage",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["summary"]["total"] == 1
        for invalid in [
            {"quarters": [5], "year": 2026},
            {"quarters": [1]},
            {"customer_id": "bad-id"},
            {"grade": "Z"},
            {"stages": ["invalid"]},
        ]:
            assert (await client.get("/api/v1/opportunities", params=invalid)).status_code == 422


async def test_fde_browse_participation_and_customer_panorama_are_distinct(connection):
    sales, op, people, _ = await fde_fixture(connection)
    await set_request_context(connection, sales)
    other = await save_opportunity(
        connection,
        sales,
        customer_id=op["customer_id"],
        data={
            "name": "同客户未参与项目",
            "amount": Decimal(900),
            "probability": 10,
            "expected_close_date": date(2026, 12, 20),
        },
    )
    member = await actor(connection, people["first"]["code"])
    repo = OpportunityRepository()
    own = await repo.page(connection, member, limit=20, include_closed=True)
    assert {r["id"] for r in own["items"]} == {op["id"]}
    lead = await actor(connection, people["lead"]["code"])
    team = await repo.page(connection, lead, limit=20, scope="team", include_closed=True)
    assert {r["id"] for r in team["items"]} == {op["id"]}  # Two participants count once.
    await set_request_context(connection, member)
    panorama = await repo.page(connection, member, limit=20, customer_id=op["customer_id"], include_closed=True)
    assert {r["id"] for r in panorama["items"]} == {op["id"], other["id"]}
    assert panorama["summary"]["open_amount"] == 100900
    with pytest.raises(PermissionError):
        await repo.page(connection, member, limit=20, scope="team", customer_id=op["customer_id"])
    with pytest.raises(PermissionError):
        await repo.page(connection, member, limit=20, member_id=people["second"]["id"])


class SummaryCapture:
    """Delegate to real PostgreSQL while identifying the cached summary statement."""

    def __init__(self, connection):
        self.connection = connection
        self.sql = None

    def __getattr__(self, name):
        return getattr(self.connection, name)

    async def fetchrow(self, sql, *args):
        if sql.startswith("WITH authorized AS MATERIALIZED"):
            self.sql = sql
        return await self.connection.fetchrow(sql, *args)


async def test_browse_prepared_plan_reuse_keeps_customer_search_totals_and_actor_scope(connection):
    """A pooled connection must preserve complete results after its actor changes.

    Exercise actual cached custom and generic plans, including a customer-name
    filter whose matches extend past the first page. This catches scope leakage
    or lost matches when the customer reference lookup is optimized.
    """
    accounts = ("XS001", "XS002")
    projects = [await opportunity(connection, code, amount) for code, amount in zip(accounts, (100, 200), strict=True)]
    names = []
    for code, project, extra in zip(accounts, projects, (24, 3), strict=True):
        await actor(connection, code)
        names.append(
            await connection.fetchval("SELECT name FROM crm.customer WHERE id=$1::uuid", project["customer_id"])
        )
        await connection.execute(
            """UPDATE crm.opportunity SET product_line=$2 WHERE id=$1::uuid""",
            project["id"], project["id"],
        )
        await connection.execute(
            """INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,
              created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code,product_line)
            SELECT workspace_id,customer_id,'计划复用-'||id||'-'||n,owner_user_ref_id,owner_team_id,
              created_by_user_ref_id,stage_code,status,amount,probability,expected_close_date,source_code,product_line
            FROM crm.opportunity CROSS JOIN generate_series(1,$2::integer) n WHERE id=$1::uuid""",
            project["id"], extra,
        )
    repo, statement_names, results = OpportunityRepository(), set(), []
    for mode in ("force_custom_plan", "force_generic_plan"):
        # Transaction-local: exercise planner modes without changing application settings.
        await connection.execute("SET LOCAL plan_cache_mode=" + mode)
        for index, code in enumerate(accounts):
            person = await actor(connection, code)
            old = await repo.list(
                connection, person, customer_id=None, owner_name=None, probability=None,
                stage_code=None, close_from=None, close_to=None, limit=None, include_closed=True,
            )
            captured = SummaryCapture(connection)
            first = await repo.page(captured, person, limit=7, include_closed=True)
            assert first["items"] == old[:7]
            opened = [row for row in old if row["status"] == "open"]
            unknown = sum(row["amount"] is None or row["amount"] < 0 for row in opened)
            assert first["summary"] == {
                "total": len(old), "open_count": len(opened), "unknown_open_amount_count": unknown,
                "open_amount": None if unknown else sum(row["amount"] for row in opened),
            }
            statement = await connection.fetchrow(
                "SELECT name,custom_plans,generic_plans FROM pg_prepared_statements WHERE statement=$1",
                captured.sql,
            )
            assert statement is not None
            assert statement["generic_plans" if mode == "force_generic_plan" else "custom_plans"] > 0
            statement_names.add(statement["name"])
            selected = await repo.page(connection, person, limit=7, include_closed=True, query=names[index])
            expected = [row for row in old if row["customer_id"] == projects[index]["customer_id"]]
            assert selected["items"] == expected[:7]
            assert selected["summary"]["total"] == len(expected) == (25, 4)[index]
            assert selected["summary"]["open_amount"] == (2500, 800)[index]
            other = await repo.page(connection, person, limit=7, include_closed=True, query=names[1 - index])
            assert other["summary"]["total"] == 0 and other["items"] == []
            assert projects[1 - index]["id"] not in first["facets"]["product_lines"]
            results.append((first, selected, other))
    assert len(statement_names) == 1  # Same prepared SQL survives actor and planner-mode changes.
    assert results[:2] == results[2:]
