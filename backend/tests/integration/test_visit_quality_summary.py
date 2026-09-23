"""Persisted quality reaches every paginated visit without broadening visibility."""

import pytest

from tests.integration.test_operations_api import client_for
from tests.integration.test_profile_scores import business_login
from tests.integration.test_visit_history_cursor import fixture

pytestmark = pytest.mark.asyncio


async def test_customer_and_opportunity_pages_preserve_quality_evidence_and_permission(connection):
    sales, project, _ = await fixture(connection, 0)
    cases = [
        (
            75,
            {
                "follow_up_score": 75,
                "grade": "合格",
                "next_action_passed": True,
                "company_policy": {"private": "must not leave the summary"},
                "large": "详细评估" * 1000,
            },
            {"follow_up_score": 75, "grade": "合格", "next_action_passed": True},
        ),
        (
            85,
            {"follow_up_score": 85, "grade": "历史保存等级", "next_action_passed": False},
            {"follow_up_score": 85, "grade": "历史保存等级", "next_action_passed": False},
        ),
        (None, None, None),
        (None, {}, None),
        (0, {"follow_up_score": 0}, {"follow_up_score": 0, "grade": None, "next_action_passed": None}),
        (
            75,
            {"follow_up_score": 70, "grade": "待复核"},
            {"follow_up_score": 70, "grade": "待复核", "next_action_passed": None},
        ),
        (
            None,
            {"follow_up_score": "未知", "grade": [], "next_action_passed": "false"},
            {"follow_up_score": None, "grade": None, "next_action_passed": None},
        ),
    ]
    expected = {}
    for n, (score, review, summary) in enumerate(cases):
        visit_id = await connection.fetchval(
            """INSERT INTO activity.visit(workspace_id,customer_id,opportunity_id,recorder_user_ref_id,
              recorder_team_id,created_by_user_ref_id,form_version_id,status,interaction_at,created_at,
              follow_up_record,next_action,follow_up_score,quality_review)
            VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,$4::uuid,
              (SELECT id FROM config.form_version WHERE status='active' ORDER BY version_no DESC LIMIT 1),
              'archived','2026-09-01T00:00Z'::timestamptz+$6::int*interval '1 day',
              '2026-09-16T00:00Z'::timestamptz+$6::int*interval '1 second',
              '历史跟进记录','后续计划',$7,$8::jsonb) RETURNING id::text""",
            sales.workspace_id,
            project["customer_id"],
            project["id"],
            sales.user_id,
            sales.team_ids[0],
            n,
            score,
            review,
        )
        expected[visit_id] = (score, summary)

    async with await client_for(connection, auth_mode="demo") as client:
        await business_login(client, "XS001")
        for opportunity in (None, project["id"]):
            for sort in (None, "created_desc"):
                params = {"customer_id": project["customer_id"], "page_size": 2}
                if opportunity:
                    params["opportunity_id"] = opportunity
                if sort:
                    params["sort"] = sort
                seen, cursor = [], None
                for _ in range(5):
                    response = await client.get(
                        "/api/v1/visits", params={**params, **({"cursor": cursor} if cursor else {})}
                    )
                    assert response.status_code == 200, response.text
                    page = response.json()
                    assert len(page["items"]) <= 2
                    for item in page["items"]:
                        assert (item["follow_up_score"], item["quality_review"]) == expected[item["id"]]
                        seen.append(item["id"])
                    if not page["has_more"]:
                        break
                    cursor = page["next_cursor"]
                assert seen == list(reversed(expected))
                offset = await client.get("/api/v1/visits", params={**params, "offset": 2})
                assert [r["id"] for r in offset.json()["items"]] == seen[2:4]
                assert all(
                    (r["follow_up_score"], r["quality_review"]) == expected[r["id"]] for r in offset.json()["items"]
                )
        await business_login(client, "XS002")
        for opportunity in (None, project["id"]):
            params = {"customer_id": project["customer_id"], "page_size": 2, "sort": "created_desc"}
            if opportunity:
                params["opportunity_id"] = opportunity
            assert (await client.get("/api/v1/visits", params=params)).status_code == 404
