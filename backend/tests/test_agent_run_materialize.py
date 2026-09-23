from datetime import UTC, datetime, timedelta

from sales_backend.services.agent_run.materialize import (
    follow_up_task_drafts,
    future_due,
    risk_card,
    risk_drafts,
    task_card,
)

VISITS = {
    "visit-1": {
        "source_visit_id": "visit-1",
        "customer_id": "customer-1",
        "follow_up_record": "客户要求二次演示",
        "next_action": "本周内安排演示",
    }
}


def test_risk_drafts_drop_items_the_facts_cannot_back() -> None:
    drafts = risk_drafts(
        {
            "risks": [
                {"source_visit_id": "visit-1", "risk_type": "budget_risk", "description": "预算未批"},
                {"source_visit_id": "visit-404", "risk_type": "budget_risk", "description": "拜访不在事实里"},
                {"source_visit_id": "visit-1", "risk_type": "made_up_risk", "description": "类型不合法"},
                {"source_visit_id": "visit-1", "risk_type": "schedule_risk", "description": "   "},
                "not-a-dict",
            ]
        },
        VISITS,
    )

    assert [draft.key for draft in drafts] == [("visit-1", "budget_risk")]


def test_risk_drafts_normalize_severity_title_and_evidence() -> None:
    (draft,) = risk_drafts(
        {
            "risks": [
                {
                    "source_visit_id": "visit-1",
                    "risk_type": "competition_risk",
                    "title": "  竞品\n介入  ",
                    "description": "竞品已提交方案",
                    "severity": "catastrophic",
                }
            ]
        },
        VISITS,
    )

    assert draft.title == "竞品 介入"
    assert draft.severity == "medium"
    assert draft.evidence_detail == "竞品已提交方案"
    assert draft.due_at > datetime.now(UTC)


def test_risk_drafts_keep_long_titles_within_column_limit() -> None:
    (draft,) = risk_drafts(
        {
            "risks": [
                {
                    "source_visit_id": "visit-1",
                    "risk_type": "budget_risk",
                    "title": "风险" * 300,
                    "description": "预算未批",
                }
            ]
        },
        VISITS,
    )

    assert len(draft.title) == 200


def test_risk_card_puts_this_run_first_then_severity() -> None:
    now = datetime.now(UTC)
    risks = [
        {
            "risk_id": "risk-old-critical",
            "source_visit_id": "visit-9",
            "risk_type_code": "budget_risk",
            "title": "旧的严重风险",
            "description": "历史遗留",
            "severity_code": "critical",
            "due_at": now,
            "opened_at": now,
            "customer_name": "老客户",
            "attributes": {},
        },
        {
            "risk_id": "risk-new-low",
            "source_visit_id": "visit-1",
            "risk_type_code": "budget_risk",
            "title": "本次识别",
            "description": "预算未批",
            "severity_code": "low",
            "due_at": now,
            "opened_at": now,
            "customer_name": "新客户",
            "attributes": {"suggested_action": "找决策人确认预算"},
        },
    ]

    card = risk_card(
        {"title": None},
        risks,
        written_keys=[("visit-1", "budget_risk")],
        scope={"scope_label": "个人"},
    )

    assert [row["risk_id"] for row in card["rows"]] == ["risk-new-low", "risk-old-critical"]
    assert card["rows"][0]["detail"] == "新客户 · 找决策人确认预算"
    assert card["rows"][1]["detail"] == "老客户 · 历史遗留"
    assert card["title"] == "个人客户风险清单"
    assert card["metrics"] == [
        {"label": "待处理", "value": "2"},
        {"label": "高风险", "value": "1"},
        {"label": "涉及客户", "value": "2"},
    ]


def test_risk_card_without_rows_says_nothing_to_handle() -> None:
    card = risk_card({}, [], written_keys=[], scope=None)

    assert card["rows"] == []
    assert "暂未识别出" in card["summary"]


def test_follow_up_drafts_come_from_visits_not_from_the_model() -> None:
    candidates = {
        "visit-1": {"source_id": "visit-1", "next_action": "  联系客户   安排演示 ", "customer_id": "customer-1"},
        "visit-2": {"source_id": "visit-2", "next_action": ""},
    }
    drafts = follow_up_task_drafts(
        {
            "ordered_items": [
                {"source_type": "visit_follow_up", "source_id": "visit-1", "priority": "urgent", "reason": "客户催"},
                {"source_type": "visit_follow_up", "source_id": "visit-2", "priority": "urgent"},
            ]
        },
        candidates,
    )

    assert [draft.source_visit_id for draft in drafts] == ["visit-1"]
    assert drafts[0].title == "联系客户 安排演示"
    assert drafts[0].priority == "urgent"
    assert drafts[0].agent_reason == "客户催"


def test_follow_up_drafts_clamp_unknown_priority_and_truncate_title() -> None:
    (draft,) = follow_up_task_drafts(
        {"ordered_items": [{"source_type": "visit_follow_up", "source_id": "visit-1", "priority": "asap"}]},
        {"visit-1": {"source_id": "visit-1", "next_action": "跟进" * 40}},
    )

    assert draft.priority == "medium"
    assert draft.title.endswith("…")
    assert len(draft.title) == 43


def test_task_card_follows_model_order_and_labels_the_source() -> None:
    now = datetime.now(UTC)
    tasks = [
        {
            "task_id": "task-management",
            "task_type": "management_task",
            "title": "上报季度预测",
            "source_visit_id": None,
            "priority_code": "high",
            "due_at": now + timedelta(days=2),
            "customer_name": None,
            "creator_name": "王总",
        },
        {
            "task_id": "task-follow-up",
            "task_type": "visit_follow_up",
            "title": "安排演示",
            "source_visit_id": "visit-1",
            "priority_code": "urgent",
            "due_at": now + timedelta(days=1),
            "customer_name": "新客户",
            "creator_name": "测试销售",
        },
    ]

    card = task_card(
        {"ordered_items": [{"source_type": "management_task", "source_id": "task-management"}]},
        tasks,
        scope=None,
    )

    assert [row["task_id"] for row in card["rows"]] == ["task-management", "task-follow-up"]
    assert "王总下发" in card["rows"][0]["detail"]
    assert card["rows"][0]["tone"] == "高"
    assert "未关联客户" in card["rows"][0]["detail"]
    assert "跟进记录提取" in card["rows"][1]["detail"]
    assert card["metrics"] == [
        {"label": "待处理", "value": "2"},
        {"label": "管理任务", "value": "1"},
        {"label": "跟进行动", "value": "1"},
    ]


def test_task_card_ignores_malformed_model_order() -> None:
    now = datetime.now(UTC)
    tasks = [
        {
            "task_id": "task-1",
            "task_type": "management_task",
            "title": "任务",
            "priority_code": "medium",
            "due_at": now,
            "customer_name": "客户",
            "creator_name": "王总",
        }
    ]

    card = task_card({"ordered_items": "not-a-list"}, tasks, scope=None)

    assert [row["task_id"] for row in card["rows"]] == ["task-1"]


def test_future_due_pushes_past_and_unparseable_values_forward() -> None:
    now = datetime.now(UTC)

    assert future_due(None) > now
    assert future_due("not-a-timestamp") > now
    assert future_due("2020-01-01T00:00:00Z") > now

    explicit = now + timedelta(days=3)
    assert future_due(explicit.isoformat()) == explicit

    naive = (now + timedelta(days=3)).replace(tzinfo=None)
    assert future_due(naive.isoformat()).tzinfo is UTC
