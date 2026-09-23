from copy import deepcopy

import pytest

from sales_backend.domain.company_rules import QuadrantPolicy, policy_snapshot, validate_policy
from sales_backend.services.battle_map_reviews import BattleMapReviewHandler
from tests.test_battle_map_platform import ANSWER, FACTS


def test_quadrant_boundaries_and_saved_scoring_receipt():
    assert QuadrantPolicy().classify(70, 70) == "customer_asset"
    assert QuadrantPolicy(inclusive=False).classify(70, 70) == "order_driven"
    assert QuadrantPolicy(potential_threshold=75).classify(74, 80) == "customer_resource"
    facts = deepcopy(FACTS)
    policy = policy_snapshot("customer_quadrant")
    policy["id"] = "synthetic-policy"
    policy["definition"]["potential_guidance"] = "企业统一测试评分标准"
    facts["company_policy"] = policy
    with pytest.raises(ValueError, match="评分政策回执"):
        BattleMapReviewHandler._normalize_result(ANSWER, facts)
    result = BattleMapReviewHandler._normalize_result({**ANSWER, "company_policy_id": "synthetic-policy"}, facts)
    assert result["potential_score"] == ANSWER["potential_score"]


@pytest.mark.parametrize("definition", [{"potential_threshold": 100}, {"sql": "select 1"}, {"inclusive": "garbage"}])
def test_rules_reject_invalid_shape_or_code(definition):
    with pytest.raises(ValueError):
        validate_policy("customer_quadrant", definition)


def test_visit_admission_bundle_and_untrusted_policy_receipt():
    from sales_backend.domain.company_rules import VisitAdmissionPolicy
    from sales_backend.domain.visit_contract import ensure_visit_result

    baseline = VisitAdmissionPolicy()
    assert not baseline.admits(60) and baseline.admits(61)
    assert VisitAdmissionPolicy(inclusive=True).admits(60)
    with pytest.raises(ValueError):
        VisitAdmissionPolicy(score_threshold=90, good_score=80)
    policy = policy_snapshot("visit_admission")
    policy["id"] = "synthetic-policy"
    policy["definition"]["scoring_guidance"] = "新的公司评分细则"
    answer = {
        "fields": {},
        "company_policy": {"definition": {"score_threshold": 0}},
        "quality_review": {"follow_up_score": 85, "next_action": {"passed": True}},
    }
    with pytest.raises(ValueError, match="政策回执"):
        ensure_visit_result(answer, policy)
    answer["company_policy_id"] = policy["id"]
    with pytest.raises(ValueError, match="缺少时间"):
        ensure_visit_result(answer, policy)
    answer["quality_review"]["next_action"].update(time_found=False, goal_or_plan_found=True)
    result = ensure_visit_result(answer, policy)
    assert result["company_policy"] == policy
    assert result["quality_review"]["next_action"]["passed"] is False


@pytest.mark.parametrize(
    "stamp", ["2026-09-11T16:10:00+00:00", "2026-09-12T01:00:00+00:00", "2026-09-12T09:01:00+00:00"]
)
def test_task_schedule_preserves_legacy_boundary_and_company_timezone(stamp):
    from datetime import UTC, datetime, timedelta

    from sales_backend.domain.company_rules import TaskSchedulePolicy

    now = datetime.fromisoformat(stamp)
    today = now.replace(hour=9, minute=0, second=0, microsecond=0)
    old = (
        today
        if today > now + timedelta(minutes=5)
        else (now + timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)
    )
    assert TaskSchedulePolicy().due(None, now) == old
    company = TaskSchedulePolicy(timezone="Asia/Shanghai", today_at="17:00", next_day_at="10:00")
    assert company.due(None, now) > now
    explicit = now + timedelta(days=3)
    assert company.due(explicit.isoformat(), now) == explicit.astimezone(UTC)
    assert company.due("invalid", now) == company.due(None, now)
    with pytest.raises(ValueError):
        TaskSchedulePolicy(today_at="24:00")


def test_execution_configuration_does_not_expand_existing_pilot_scope():
    from sales_backend.services.agent_platform.pilot import today_tasks_pilot_policy
    from sales_backend.services.runtime_config import apply_execution_policy
    from tests.test_today_tasks_platform import ACTOR, configured

    baseline = policy_snapshot("agent_execution.today_tasks")
    settings = configured(enabled=False)
    assert today_tasks_pilot_policy(apply_execution_policy(settings, baseline), ACTOR) is None
    baseline["definition"]["strategy"] = "direct_only"
    assert today_tasks_pilot_policy(apply_execution_policy(configured(enabled=True), baseline), ACTOR) is None
    with pytest.raises(ValueError):
        validate_policy("agent_execution.today_tasks", {"platform_seconds": 60, "total_seconds": 65})
