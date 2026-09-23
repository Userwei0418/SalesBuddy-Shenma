RISK_TYPE_CODES = frozenset(
    {
        "expectation_gap",
        "engagement_stalled",
        "decision_maker_gap",
        "budget_risk",
        "competition_risk",
        "commercial_process_risk",
        "schedule_risk",
        "technical_validation_risk",
        "relationship_risk",
    }
)

SEVERITY_LABELS = {"critical": "严重", "high": "高", "medium": "中", "low": "低"}

TASK_PRIORITY_CODES = frozenset({"normal", "medium", "high", "urgent"})
