"""Real PostgreSQL competency persistence and self-scoped calendar boundaries.

These rollback fixtures do not invoke a model or claim platform-quality evidence.
"""

from datetime import date, timedelta
from uuid import uuid4

import pytest

from sales_backend.domain.competency_review import review_window, scored_review
from sales_backend.repositories.competency_reviews import CompetencyReviewRepository
from sales_backend.repositories.visits import VisitRepository
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_opportunity_lifecycle import setup_customer

pytestmark = pytest.mark.asyncio


async def test_competency_facts_include_exactly_thirty_beijing_days_and_only_the_recorder(connection):
    person = await actor(connection, "XS001")
    _, customer = await setup_customer(connection, person)
    day = date(2081, 1, 31)

    async def visit(recorder, offset):
        return await VisitRepository().create(connection, recorder, customer_id=customer["id"], fields={
            "interaction_at": (day + timedelta(days=offset)).isoformat(), "created_date": day.isoformat(),
            "contact_name": "契约验证联系人", "follow_up_record": "合成测试：已确认一项具体需求。",
            "next_action": "下周一明确两项试点验收标准", "_follow_up_quality_score": 85,
        })

    before = await visit(person, -30)
    first = await visit(person, -29)
    today = await visit(person, 0)
    after = await visit(person, 1)
    peer = await actor(connection, "XS002")
    other = await visit(peer, 0)
    await actor(connection, "XS001")
    start, end = review_window(day)
    rows, _ = await CompetencyReviewRepository().facts(connection, person, day, start, end)
    keys = {row["visit_id"] for row in rows}
    assert first["id"] in keys and today["id"] in keys
    assert not keys & {before["id"], after["id"], other["id"]}
    # Query boundaries are timezone-aware, unaffected by a connection's timezone.
    await connection.execute("SET LOCAL TIME ZONE 'America/Los_Angeles'")
    shifted, _ = await CompetencyReviewRepository().facts(connection, person, day, start, end)
    assert [row["visit_id"] for row in shifted] == [row["visit_id"] for row in rows]


async def test_competency_receipt_persists_actual_provider_and_prevents_terminal_overwrite(connection):
    person = await actor(connection, "XS001")
    review_id = await connection.fetchval(
        """INSERT INTO insight.sales_competency_review
           (workspace_id,subject_user_ref_id,framework_version,review_date,status)
           VALUES($1::uuid,$2::uuid,1,'2081-01-31','queued') RETURNING id::text""",
        person.workspace_id, person.user_id,
    )
    repository = CompetencyReviewRepository()
    _, framework = await repository.start(connection, person, review_id)
    result = {
        "summary": "合成契约样例：当前没有可引用的本人记录。", "strengths": [],
        "improvements": [f"{d['name']}：下次拜访前准备两项确认问题。" for d in framework["dimensions"]],
        "dimensions": [{
            "code": d["code"], "score": 0, "assessment": "证据不足", "evidence": [],
            "coaching_action": "下次拜访前准备两项确认问题并记录结果。",
        } for d in framework["dimensions"]],
    }
    values = scored_review(result, framework, {"visits": []})
    snapshot = {"review_id": review_id, "inference_route": {
        "operation_id": str(uuid4()), "provider": "agent_platform", "model_ref": "agent-platform:synthetic",
        "fallback_reason": None,
    }}
    await repository.save(connection, person, review_id, values, snapshot, "agent-platform:synthetic")
    saved = await connection.fetchrow(
        "SELECT status,overall_score,model_id,input_snapshot FROM insight.sales_competency_review WHERE id=$1::uuid",
        review_id,
    )
    assert saved["status"] == "succeeded" and saved["overall_score"] == 0
    assert saved["model_id"] == "agent-platform:synthetic" and saved["input_snapshot"] == snapshot
    with pytest.raises(LookupError):
        await repository.start(connection, person, review_id)
    with pytest.raises(LookupError):
        await repository.save(connection, person, review_id, values, {}, "incorrect-overwrite")
    assert await connection.fetchval(
        "SELECT model_id FROM insight.sales_competency_review WHERE id=$1::uuid", review_id
    ) == "agent-platform:synthetic"
