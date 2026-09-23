"""Real PostgreSQL detail reads: complete facts, bounded bodies and unchanged RLS."""

import asyncio
import json
import os
import time
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.customer_assets import CustomerAssetRepository, today
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.detail_history import DetailHistoryRepository
from sales_backend.repositories.detail_overviews import DetailOverviewRepository
from sales_backend.repositories.opportunities import OpportunityRepository
from sales_backend.services.opportunities import save_opportunity
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_fde_identity_tasks import fde_fixture
from tests.integration.test_fde_owned_recording import login_fde
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


def plan_counters(node, depth=0):
    """Keep every plan node and its selectivity/cost evidence, without SQL text.

    Expanded RLS predicates repeat large expressions (and fixture identifiers)
    across subplans. They are not performance counters and can swamp CI evidence.
    Depth retains the tree shape; rows removed retains filter selectivity.
    Zero temporary-buffer counters are omitted; any spill remains explicit.
    """
    counters = {key: node[key] for key in (
        "Node Type", "Relation Name", "Index Name", "Actual Rows", "Actual Loops",
        "Shared Hit Blocks", "Shared Read Blocks", "Temp Read Blocks", "Temp Written Blocks",
        "Plan Rows", "Rows Removed by Filter", "Rows Removed by Join Filter",
        "Sort Method", "Sort Space Used", "Sort Space Type",
    ) if key in node and (key not in {"Temp Read Blocks", "Temp Written Blocks"} or node[key] != 0)}
    return [{"depth": depth, **counters}] + [
        item for child in node.get("Plans", []) for item in plan_counters(child, depth + 1)
    ]


def emit_plan(value):
    """Optional CI evidence contains only fixture scale, sizes and plan counters."""
    line = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    output = os.environ.get("DETAIL_QUERY_EVIDENCE_PATH")
    if output:
        path = Path(output)
        current_size = path.stat().st_size if path.exists() else 0
        assert current_size + len((line + "\n").encode("utf-8")) < 65536
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    print(line)


async def test_plan_evidence_retains_nodes_and_counters_without_expanded_predicates():
    plan = {"Node Type": "Limit", "Actual Rows": 21, "Actual Loops": 1, "Plans": [{
        "Node Type": "Index Scan", "Relation Name": "visit", "Index Name": "idx_visit_team_time",
        "Actual Rows": 1000, "Actual Loops": 21, "Plan Rows": 1, "Shared Hit Blocks": 6001,
        "Rows Removed by Filter": 980, "Temp Read Blocks": 0, "Temp Written Blocks": 12,
        "Filter": "sensitive fixture predicate" * 10000,
        "Index Cond": "fixture uuid", "Plans": [{"Node Type": "Seq Scan", "Actual Loops": 0}],
    }]}
    evidence = plan_counters(plan)
    assert [node["depth"] for node in evidence] == [0, 1, 2]
    assert evidence[1] == {
        "depth": 1, "Node Type": "Index Scan", "Relation Name": "visit", "Index Name": "idx_visit_team_time",
        "Actual Rows": 1000, "Actual Loops": 21, "Plan Rows": 1, "Shared Hit Blocks": 6001,
        "Rows Removed by Filter": 980, "Temp Written Blocks": 12,
    }
    assert evidence[2]["Actual Loops"] == 0
    assert len(json.dumps(evidence)) < 1024


async def test_plan_evidence_checks_utf8_budget_before_appending(monkeypatch, tmp_path):
    output = tmp_path / "plans.jsonl"
    monkeypatch.setenv("DETAIL_QUERY_EVIDENCE_PATH", str(output))
    emit_plan({"detail_scale": 10, "history_ms": 1.5, "history_plan": []})
    previous = output.read_bytes()
    with pytest.raises(AssertionError):
        emit_plan({"diagnostic": "中" * 22000})
    assert output.read_bytes() == previous


async def histories(connection, person, project, count):
    """CI transaction fixtures only; no provider calls or production account writes."""
    await set_request_context(connection, person)
    values = [person.workspace_id, project["customer_id"], project["id"], person.user_id, person.team_ids[0], count]
    await connection.execute(
        """INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
          recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,created_at,
          follow_up_record,next_action,quality_review,first_visit_profile)
        SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$4::uuid,
          (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
          CASE WHEN n%2=0 THEN 'archived' ELSE 'pending_confirm' END,
          '2026-09-01 00:00+08'::timestamptz,'2026-09-01 00:00+08'::timestamptz,
          repeat('正文',1000),'明天落实需求'||n,jsonb_build_object('large',repeat('评估',1000)),
          jsonb_build_object('large',repeat('画像',1000)) FROM generate_series(1,$6::integer) n""",
        *values,
    )
    await connection.execute(
        """INSERT INTO workflow.task(workspace_id,customer_id,opportunity_id,creator_user_ref_id,
          creator_team_id,title,description,status,created_at,due_at,completed_at)
        SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,'分页任务'||n,repeat('任务说明',500),
          CASE n%3 WHEN 0 THEN 'completed' WHEN 1 THEN 'cancelled' ELSE 'pending_execution' END,
          '2026-09-01 00:00+08'::timestamptz,'2027-01-01 00:00+08'::timestamptz,
          CASE WHEN n%3=0 THEN '2026-09-03 00:00+08'::timestamptz END
        FROM generate_series(1,$6::integer) n""",
        *values,
    )
    await connection.execute(
        """INSERT INTO insight.risk(workspace_id,customer_id,opportunity_id,owner_user_ref_id,
          owner_team_id,risk_type_code,title,description,severity_code,status,opened_at,evidence)
        SELECT $1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,'delivery','分页风险'||n,repeat('风险说明',500),
          CASE WHEN n=$6 THEN 'critical' ELSE 'low' END,
          CASE WHEN n=$6 THEN 'accepted' ELSE 'resolved' END,
          '2026-09-01 00:00+08'::timestamptz,jsonb_build_array(repeat('证据',1000))
        FROM generate_series(1,$6::integer) n""",
        *values,
    )


def payload_bytes(value):
    return len(json.dumps(value, default=str, ensure_ascii=False).encode("utf-8"))


def assert_summary(old, overview):
    summary = overview["summary"]
    assert summary["visit_count"] == len(old["visits"])
    assert summary["confirmed_visit_count"] == sum(v["status"] in {"confirmed", "archived"} for v in old["visits"])
    assert summary["task_status_counts"] == dict(Counter(t["status"] for t in old["tasks"]))
    assert summary["task_count"] == len(old["tasks"])
    assert summary["risk_count"] == len(old["risks"])


@pytest.mark.parametrize("count", [10, 50, 100, 300, 1000])
async def test_customer_overview_full_scale_counts_and_bounded_history(connection, count):
    # Bound fixture creation, SQL and the complete scenario: a slow query must fail,
    # not leave CI waiting indefinitely. Every fixture rolls back in this database.
    async with asyncio.timeout(90):
        await connection.execute("SET LOCAL statement_timeout='20s'")
        await connection.execute("SET LOCAL lock_timeout='3s'")
        await check_detail_scale(connection, count)


@pytest.mark.skipif(os.environ.get("SALES_DETAIL_CAPACITY_OBSERVATION") != "1",
                    reason="10k capacity observation is opt-in; normal acceptance uses 10–1000 histories")
async def test_customer_detail_capacity_observation_10000(connection):
    # Explicit diagnostic, not a normal release gate. Keep the original budgets
    # so capacity failure remains visible rather than hidden by a larger timeout.
    async with asyncio.timeout(90):
        await connection.execute("SET LOCAL statement_timeout='20s'")
        await connection.execute("SET LOCAL lock_timeout='3s'")
        await check_detail_scale(connection, 10000)


async def check_detail_scale(connection, count):
    people = None
    if count >= 300:
        _, project, people, _ = await fde_fixture(connection)
    else:
        project = await opportunity(connection, "XS001", 100)
    person = await actor(connection, "XS001")
    await histories(connection, person, project, count)
    repo = DetailOverviewRepository()
    started = time.perf_counter()
    overview = await repo.customer(connection, project["customer_id"])
    overview_ms = (time.perf_counter() - started) * 1000
    assert not {"visits", "tasks", "risks", "contacts", "opportunities"} & overview.keys()
    summary = overview["summary"]
    assert summary["visit_count"] == summary["task_count"] == summary["risk_count"] == count
    assert summary["confirmed_visit_count"] == count // 2
    assert summary["open_amount"] == project["amount"] and summary["opportunity_count"] == 1
    assert overview["primary_opportunity"]["id"] == project["id"]
    assert summary["status_risk"]["status"] == "accepted" and summary["open_risk"] is None
    assert overview["profile"]["dimensions"][-1]["value"] == 100
    assert overview["profile"]["dimensions"][3]["value"] == min(100, count // 2 * 20)
    assert payload_bytes(overview) < 15000
    if count <= 1000:
        old = await CustomerRepository().detail(connection, customer_id=project["customer_id"])
        assert_summary(old, overview)
        assert old["profile"] == overview["profile"]
    history = DetailHistoryRepository()
    class CaptureFetch:
        async def fetch(self, sql, *args):
            self.query, self.args = sql, args
            return await connection.fetch(sql, *args)

    measured = CaptureFetch()
    first = await history.visits(measured, project["customer_id"], opportunity_id=project["id"], limit=20)
    plan = await connection.fetchval("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + measured.query, *measured.args)
    assert plan[0]["Plan"]["Actual Rows"] <= 21
    emit_plan({"detail_scale": count, "overview_ms": round(overview_ms, 3),
                      "overview_bytes": payload_bytes(overview),
                      "history_ms": plan[0]["Execution Time"], "history_plan": plan_counters(plan[0]["Plan"])})
    assert len(first["items"]) == min(20, count)
    assert payload_bytes(first) < 100000
    assert all(len(row["follow_up_record"]) <= 600 and "large" not in (row["quality_review"] or {})
               for row in first["items"])
    # Equal timestamps exercise the UUID tie-breaker; no duplicate/missing tail.
    second = await history.visits(connection, project["customer_id"], limit=20, offset=20)
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    tail = await history.visits(connection, project["customer_id"], limit=20, offset=count)
    assert tail["items"] == [] and not tail["has_more"]
    timeline = await history.timeline(connection, project["id"], limit=20)
    assert len(timeline["items"]) == 20 and timeline["has_more"]
    assert payload_bytes(timeline) < 50000
    if people:
        await actor(connection, people["lead"]["code"])
        started = time.perf_counter()
        fde_overview = await repo.customer(connection, project["customer_id"])
        fde_ms = (time.perf_counter() - started) * 1000
        assert fde_overview["summary"]["visit_count"] == count
        assert fde_overview["summary"]["task_count"] == count
        assert fde_overview["profile"] == overview["profile"]
        assert payload_bytes(fde_overview) < 15000
        fde_page = await history.visits(measured, project["customer_id"], opportunity_id=project["id"], limit=20)
        fde_plan = await connection.fetchval("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + measured.query, *measured.args)
        assert len(fde_page["items"]) == 20 and fde_plan[0]["Plan"]["Actual Rows"] == 21
        emit_plan({"detail_scale": count, "role": "fde_lead", "overview_ms": round(fde_ms, 3),
                          "overview_bytes": payload_bytes(fde_overview),
                          "history_ms": fde_plan[0]["Execution Time"],
                          "history_plan": plan_counters(fde_plan[0]["Plan"])})
        # A small target among the large customer history exposes useful filter
        # selectivity; a 10k-row single-subject scan alone cannot justify an index.
        sales = await actor(connection, "XS001")
        small = await save_opportunity(connection, sales, customer_id=project["customer_id"], data={
            "name": "少量历史目标", "amount": Decimal(20), "probability": 10,
            "expected_close_date": date(2026, 12, 20),
        })
        await histories(connection, sales, small, 20)
        await actor(connection, people["lead"]["code"])
        started = time.perf_counter()
        small_overview = await repo.opportunity(connection, None, small["id"], customer_id=project["customer_id"])
        small_ms = (time.perf_counter() - started) * 1000
        assert small_overview["summary"]["visit_count"] == 20
        selected = await history.visits(measured, project["customer_id"], opportunity_id=small["id"], limit=20)
        assert len(selected["items"]) == 20 and not selected["has_more"]
        selected_plan = await connection.fetchval("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + measured.query,
                                                   *measured.args)
        emit_plan({"detail_scale": count, "role": "fde_lead", "target_rows": 20,
                          "target_overview_ms": round(small_ms, 3), "history_ms": selected_plan[0]["Execution Time"],
                          "history_plan": plan_counters(selected_plan[0]["Plan"])})


async def test_overview_unknown_amount_primary_sort_and_risk_profile(connection):
    project = await opportunity(connection, "XS001", 100, close=date(2026, 12, 10))
    person = await actor(connection, "XS001")
    other = await save_opportunity(connection, person, customer_id=project["customer_id"], data={
        "name": "更早的商机", "amount": Decimal(200), "probability": 70,
        "expected_close_date": date(2026, 10, 1),
        "quarterly_forecasts": [{"year": 2026, "quarter": 4, "recognized_amount": 0, "collection_amount": 0}],
    })
    await connection.execute("UPDATE crm.opportunity SET amount=NULL WHERE id=$1::uuid", project["id"])
    old = await CustomerRepository().detail(connection, customer_id=project["customer_id"])
    overview = await DetailOverviewRepository().customer(connection, project["customer_id"])
    assert overview["summary"]["open_amount"] is None
    assert overview["summary"]["unknown_open_amount_count"] == 1
    assert overview["primary_opportunity"]["id"] == other["id"]
    assert overview["profile"] == old["profile"]
    individual = await DetailOverviewRepository().opportunity(connection, person, project["id"])
    assert individual["summary"]["open_amount"] is None
    assert not {"tasks", "visits", "risks"} & individual.keys()


@pytest.mark.parametrize("clear", [False, True])
async def test_customer_profile_read_paths_share_verified_clear_evidence_without_exposing_it(
    connection, monkeypatch, clear,
):
    """Exercise SQL profile facts; receipt verification itself has separate repository tests."""
    project = await opportunity(connection, "XS001", 100)
    await actor(connection, "XS001")
    calls = []

    async def verified_clear(reader, customer_id):
        calls.append((reader, customer_id))
        return clear

    monkeypatch.setattr("sales_backend.repositories.customers.current_clear_assessment", verified_clear)
    monkeypatch.setattr("sales_backend.repositories.detail_overviews.current_clear_assessment", verified_clear)
    old = await CustomerRepository().detail(connection, customer_id=project["customer_id"])
    overview = await DetailOverviewRepository().customer(connection, project["customer_id"])
    assert calls == [(connection, project["customer_id"])] * 2
    assert old["profile"] == overview["profile"]
    assert overview["profile"]["dimensions"][-1]["value"] == (100 if clear else None)
    assert overview["summary"]["risk_count"] == 0
    assert_summary(old, overview)
    for payload in (old, overview):
        assert "risk_assessment_clear" not in json.dumps(payload, default=str)
        assert set(payload["profile"]) == {"version", "dimensions", "coverage", "total", "scope"}
        assert all(set(axis) == {"code", "label", "value", "basis"} for axis in payload["profile"]["dimensions"])

    calls.clear()
    await actor(connection, "XS002")
    assert await CustomerRepository().detail(connection, customer_id=project["customer_id"]) is None
    assert await DetailOverviewRepository().customer(connection, project["customer_id"]) is None
    assert calls == []


async def test_fde_overview_keeps_customer_panorama_but_own_projects_remain_scoped(connection):
    sales, project, people, _ = await fde_fixture(connection)
    await set_request_context(connection, sales)
    other = await save_opportunity(connection, sales, customer_id=project["customer_id"], data={
        "name": "客户全景其他商机", "amount": Decimal(900), "probability": 10,
        "expected_close_date": date(2026, 12, 20),
    })
    fde = await actor(connection, people["first"]["code"])
    repo = DetailOverviewRepository()
    overview = await repo.customer(connection, project["customer_id"])
    old = await CustomerRepository().detail(connection, customer_id=project["customer_id"])
    assert_summary(old, overview)
    assert overview["profile"] == old["profile"]
    assert overview["summary"]["opportunity_count"] == 2
    page = await OpportunityRepository().page(connection, None, customer_id=project["customer_id"], limit=1,
                                             include_closed=True)
    assert page["summary"]["total"] == 2
    assert (await repo.opportunity(connection, fde, other["id"])) is not None
    target = await repo.opportunity(connection, None, other["id"], customer_id=project["customer_id"])
    assert target["opportunities"][0]["id"] == other["id"]
    assert await repo.opportunity(connection, None, other["id"], customer_id=str(uuid4())) is None
    own = await OpportunityRepository().page(connection, fde, limit=20, include_closed=True)
    assert {p["id"] for p in own["items"]} == {project["id"]}
    async with await client_for(connection) as client:
        await login_fde(connection, client, people["first"])
        prefix = f"/api/v1/opportunities/{other['id']}/timeline"
        response = await client.get(prefix, params={"customer_id": project["customer_id"]})
        assert response.status_code == 200 and response.json()["items"]
        assert (await client.get(prefix, params={"customer_id": str(uuid4())})).status_code == 404
        target = await client.get(f"/api/v1/customers/{project['customer_id']}/opportunities/{other['id']}/overview")
        assert target.status_code == 200
    await actor(connection, "XS002")
    assert await repo.customer(connection, project["customer_id"]) is None


async def test_detail_read_endpoints_validate_uuid_subject_and_pagination(connection):
    project = await opportunity(connection, "XS001", 100)
    customer = project["customer_id"]
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        paths = [f"/customers/{customer}/overview", f"/opportunities/{project['id']}/overview",
                 f"/customers/{customer}/contacts", f"/customers/{customer}/opportunities",
                 f"/customers/{customer}/opportunities/{project['id']}/overview",
                 f"/visits?customer_id={customer}", f"/opportunities/{project['id']}/timeline",
                 f"/customer-assets/quarters?customer_id={customer}&opportunity_id={project['id']}"]
        for path in paths:
            response = await client.get("/api/v1" + path)
            assert response.status_code == 200, response.text
        assert (await client.get("/api/v1/customers/bad-id/overview")).status_code == 422
        assert (await client.get(f"/api/v1/visits?customer_id={customer}&page_size=0")).status_code == 422
        await business_login(client, "XS002")
        for path in paths:
            assert (await client.get("/api/v1" + path)).status_code == 404


async def test_quarter_actuals_group_complete_ledger_without_forecast_or_voided_rows(connection):
    project = await opportunity(connection, "XS001", 999999)
    manager = await actor(connection, "ZJL001")
    repo = CustomerAssetRepository()
    as_of = today()
    recent = date(as_of.year, 1, 1)
    past = date(as_of.year - 1, 12, 31)
    ids = []
    for day, kind, amount in [(past, "recognized", 90), (recent, "recognized", 100),
                              (recent, "collection", 0), (recent, "recognized", 999)]:
        ids.append(await repo.create(connection, manager, {
            "customer_id": UUID(project["customer_id"]), "opportunity_id": UUID(project["id"]),
            "occurred_on": day, "kind": kind, "amount": Decimal(amount),
            "source_ref": uuid4().hex, "note": "聚合口径", "request_id": uuid4(), "confirmed": True,
        }))
    await repo.void(connection, manager, UUID(ids[-1]["id"]), "排除已作废实绩")
    result = await DetailHistoryRepository().actual_quarters(
        connection, project["customer_id"], project["id"], as_of=as_of,
    )
    assert result["years"] == [as_of.year, as_of.year - 1]
    assert result["items"] == [
        {"year": as_of.year - 1, "quarter": 4, "recognized_amount": 90, "collection_amount": None,
         "recognized_count": 1, "collection_count": 0, "entry_count": 1},
        {"year": as_of.year, "quarter": 1, "recognized_amount": 100, "collection_amount": 0,
         "recognized_count": 1, "collection_count": 1, "entry_count": 2},
    ]
    previous = await DetailHistoryRepository().actual_quarters(
        connection, project["customer_id"], project["id"], as_of=past,
    )
    assert len(previous["items"]) == 1 and previous["items"][0]["recognized_amount"] == 90
    filtered = await DetailHistoryRepository().actual_quarters(
        connection, project["customer_id"], project["id"], as_of=as_of, owner_id=uuid4(),
    )
    assert filtered["items"] == []


async def test_operations_overview_and_history_do_not_load_business_history(connection):
    from sales_backend.repositories.operations_customers import OperationsCustomerRepository

    project = await opportunity(connection, "XS001", 100)
    await actor(connection, "OPS001")
    repo = OperationsCustomerRepository()
    for kind in ("claims", "ownership"):
        first = await repo.customer_history(connection, project["customer_id"], kind=kind, limit=1)
        assert len(first["items"]) <= 1
        empty = await repo.customer_history(connection, project["customer_id"], kind=kind, limit=1, offset=9999)
        assert empty["total"] == first["total"] and empty["items"] == [] and not empty["has_more"]


async def test_customer_target_pair_must_match_and_sales_cannot_gain_other_owner_records(connection):
    first = await opportunity(connection, "XS001", 100)
    second_person = await actor(connection, "XS002")
    other = await save_opportunity(connection, second_person, customer_id=first["customer_id"], data={
        "name": "同客户其他销售商机", "amount": Decimal(200), "probability": 30,
        "expected_close_date": date(2026, 12, 1),
        "quarterly_forecasts": [{"year": 2026, "quarter": 4, "recognized_amount": 0, "collection_amount": 0}],
    })
    separate = await opportunity(connection, "XS001", 300)
    first_person = await actor(connection, "XS001")
    # Customer RLS never implied visibility of another sales owner's opportunity.
    old = await CustomerRepository().detail(connection, customer_id=first["customer_id"])
    assert other["id"] not in {row["id"] for row in old["opportunities"]}
    repo = DetailOverviewRepository()
    assert await repo.opportunity(connection, None, other["id"], customer_id=first["customer_id"]) is None
    assert await repo.opportunity(connection, first_person, other["id"]) is None
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        for target in (other, separate):
            result = await client.get(f"/api/v1/customers/{first['customer_id']}/opportunities/{target['id']}/overview")
            assert result.status_code == 404
        # Cross-customer mismatch fails even for the company-wide manager.
        await business_login(client, "ZJL001")
        result = await client.get(f"/api/v1/customers/{first['customer_id']}/opportunities/{separate['id']}/overview")
        assert result.status_code == 404
        result = await client.get(f"/api/v1/opportunities/{separate['id']}/timeline",
                                  params={"customer_id": first["customer_id"]})
        assert result.status_code == 404
