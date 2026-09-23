from sales_backend.services.competency_reviews import CompetencyReviewHandler


def test_competency_prompt_requires_six_dimension_sales_coaching() -> None:
    dimensions = [
        {"code": code, "name": name}
        for code, name in (
            ("need_insight", "需求洞察"),
            ("decision_chain", "决策链经营"),
            ("solution_communication", "方案沟通"),
            ("opportunity_progress", "商机推进"),
            ("customer_relationship", "客户关系"),
            ("follow_up_execution", "跟进执行"),
        )
    ]
    messages = CompetencyReviewHandler._messages(
        {"dimensions": dimensions, "scoring_rules": {}},
        {"visits": [], "visit_count": 0},
        "请保持简洁",
    )

    system = messages[0].content
    assert "资深企业级销售教练" in system
    assert "improvements必须严格输出6条" in system
    assert "方法+可验收动作" in system
    assert "请保持简洁" in system
