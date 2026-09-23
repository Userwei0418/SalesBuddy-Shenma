"""Published weights, historical evidence and complete scoped aggregation on real PG."""

from decimal import Decimal

import pytest

from tests.integration.test_operations_api import client_for, sign_in
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def review(connection, account, score, days=0):
    context = await actor(connection, account)
    return await connection.fetchval(
        """INSERT INTO insight.sales_competency_review(workspace_id,subject_user_ref_id,
          framework_version,review_date,status,overall_score,dimension_scores,input_snapshot,reviewed_at)
        VALUES($1::uuid,$2::uuid,1,timezone('Asia/Shanghai',clock_timestamp())::date-$3::int,
          'succeeded',88,$4::jsonb,'{"visit_count":2}',clock_timestamp()) RETURNING id::text""",
        context.workspace_id,
        context.user_id,
        days,
        {"needs_discovery": {"score": score}, "opportunity_advancement": {"score": 20}},
    )


async def business_login(client, account):
    response = await client.post(
        "/api/v1/auth/session", json={"account_code": account, "workspace": "demo-sales-workspace"}
    )
    assert response.status_code == 200, response.text
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]


async def test_published_score_weights_change_current_projection_not_archived_evidence(connection):
    review_id = await review(connection, "XS001", 100)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        before = (await client.get("/api/v1/profile/sales-growth")).json()
        assert Decimal(before["latest"]["overall_score"]) == 88
        assert before["latest"]["score_summary"]["text"] == "58"
        await sign_in(client)
        rules = (await client.get("/api/v1/console/company-rules")).json()["items"]
        original = next(r["current"] for r in rules if r["code"] == "score.competency")
        weights = {key: 0 for key in original["definition"] if key != "schema_version"}
        weights["needs_discovery"] = 100
        body = {"base_id": original["id"], "definition": weights, "reason": "隔离测试权重发布"}
        preview = await client.post("/api/v1/console/company-rules/score.competency/preview", json=body)
        assert preview.status_code == 200, preview.text
        draft = await client.post("/api/v1/console/company-rules/score.competency/drafts", json=body)
        assert draft.status_code == 200, draft.text
        await sign_in(client, "ADMIN001")
        published = await client.post(
            "/api/v1/console/company-rules/versions/" + draft.json()["id"] + "/publish", json={"revision": 1}
        )
        assert published.status_code == 200, published.text
        await business_login(client, "XS001")
        after = (await client.get("/api/v1/profile/sales-growth")).json()
        score = after["latest"]["score_summary"]
        assert score["text"] == "100" and score["rule"]["id"] == draft.json()["id"]
        assert score["provenance"]["aggregate_input_fingerprint"] != ""
        assert Decimal(after["history"][0]["overall_score"]) == 88
        assert (
            await connection.fetchval(
                "SELECT overall_score FROM insight.sales_competency_review WHERE id=$1::uuid", review_id
            )
            == 88
        )
        denied = await client.get(
            "/api/v1/profile/sales-growth/scoped", params={"scope": "person", "account_code": "XS002"}
        )
        assert denied.status_code == 403
        performance = await client.get("/api/v1/profile/performance", params={"period": "all"})
        assert performance.status_code == 200, performance.text
        assert performance.json()["scores"]["maturity"]["value"] is None
        assert performance.json()["scores"]["efficiency"]["provenance"]["followup_start"] == "1900-01-01"


async def test_scoped_growth_latest_per_member_full_coverage_and_role_boundary(connection):
    await review(connection, "XS001", 0, days=2)
    latest_id = await review(connection, "XS001", 60)
    await review(connection, "XS002", 100)
    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "ZJL001")
        response = await client.get("/api/v1/profile/sales-growth/scoped", params={"scope": "department"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["coverage"] == {"reviewed_members": 2, "eligible_members": 3}
        assert Decimal(data["latest"]["dimension_scores"]["needs_discovery"]["score"]) == 80
        assert data["latest"]["dimension_scores"]["needs_discovery"]["sample_count"] == 2
        assert data["history"] == [] and data["projection"] == "current_group"
        assert latest_id in [r["id"] for r in data["provenance"]["source_reviews"]]
        await business_login(client, "XS001")
        assert (
            await client.get("/api/v1/profile/sales-growth/scoped", params={"scope": "department"})
        ).status_code == 403
        own = await client.get("/api/v1/profile/sales-growth/scoped")
        assert own.status_code == 200, own.text
        assert own.json()["coverage"]["eligible_members"] == 1
        assert own.json()["latest"]["dimension_scores"]["needs_discovery"]["score"] == 60
