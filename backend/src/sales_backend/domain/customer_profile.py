"""Six-axis presentation indicators from complete authorized database facts, not model facts.

Keep the delivered UI scales, but represent missing evidence as null. These indicators
are not revenue, risk certainty, or the separately configured salesperson total score.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ROLE_POINTS = {"decision_maker": 50, "influencer": 30, "user": 20}
RISK_PENALTIES = {"critical": 80, "high": 60, "medium": 35, "low": 15}


def score(value):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            return None
        return int(max(Decimal(0), min(Decimal(100), number)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def customer_profile(customer, opportunities, visits, contacts, risks, *, risk_assessment_clear=False):
    probabilities = [score(o.get("probability")) for o in opportunities if o.get("status") not in {"lost", "cancelled"}]
    probabilities = [p for p in probabilities if p is not None]
    valid_visits = [v for v in visits if v.get("status") in {"confirmed", "archived"}]
    roles = {c.get("relationship_role_code") for c in contacts} & ROLE_POINTS.keys()
    open_risks = [r for r in risks if r.get("status") not in {"resolved", "accepted"}]
    return customer_profile_from_facts(customer, {
        "max_probability": max(probabilities, default=None), "confirmed_visit_count": len(valid_visits),
        "contact_roles": list(roles), "risk_count": len(risks),
        "open_risk_severities": list({r.get("severity_code") for r in open_risks}),
        "risk_assessment_clear": risk_assessment_clear,
    })


def customer_profile_from_facts(customer, facts):
    """One scoring contract for both legacy arrays and bounded SQL aggregate facts."""
    roles = set(facts.get("contact_roles") or []) & ROLE_POINTS.keys()
    penalties = [RISK_PENALTIES.get(value) for value in facts.get("open_risk_severities") or []]
    risk_health = None
    risk_basis = "按已登记风险最高等级扣分；没有风险记录不推定健康"
    if facts["risk_count"] and all(p is not None for p in penalties):
        risk_health = 100 - max(penalties, default=0)
    elif facts["risk_count"] == 0 and not penalties and facts.get("risk_assessment_clear") is True:
        risk_health = 100
        risk_basis = "已完成当前可见记录的风险评估，未发现风险"
    values = [
        score(customer.get("potential_score")),
        score(customer.get("relationship_score")),
        score(facts.get("max_probability")),
        min(100, facts["confirmed_visit_count"] * 20),
        sum(ROLE_POINTS[r] for r in roles) if roles else None,
        risk_health,
    ]
    dimensions = []
    for code, label, value, basis in zip(
        ["potential", "relationship", "opportunity", "activity", "decision_chain", "risk_health"],
        ["客户潜力", "关系深度", "商机成熟", "拜访活跃", "决策链", "风险健康"],
        values,
        [
            "当前可见潜力评估",
            "当前可见关系评估",
            "可见非丢单商机的最高已登记概率",
            "已确认或归档拜访每条20分，上限100",
            "已登记决策者50、影响者30、使用者20，按角色去重",
            risk_basis,
        ],
        strict=True,
    ):
        dimensions.append({"code": code, "label": label, "value": value, "basis": basis})
    return {
        "version": "customer_profile_v1",
        "dimensions": dimensions,
        "coverage": sum(value is not None for value in values),
        "total": 6,
        "scope": "current_authorized_records",
    }
