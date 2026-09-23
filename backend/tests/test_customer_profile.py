import pytest

from sales_backend.domain.customer_profile import customer_profile, customer_profile_from_facts


def test_profile_keeps_missing_distinct_from_zero_and_has_no_assumed_healthy_score():
    empty = customer_profile({}, [], [], [], [])
    assert [d["value"] for d in empty["dimensions"]] == [None, None, None, 0, None, None]
    assert empty["coverage"] == 1
    measured = customer_profile(
        {"potential_score": 0, "relationship_score": 70},
        [{"probability": 30, "status": "open"}, {"probability": 100, "status": "lost"}],
        [{"status": "archived"}, {"status": "draft"}],
        [
            {"relationship_role_code": "decision_maker"},
            {"relationship_role_code": "decision_maker"},
            {"relationship_role_code": "influencer"},
        ],
        [{"severity_code": "low", "status": "new"}, {"severity_code": "high", "status": "in_progress"}],
    )
    assert [d["value"] for d in measured["dimensions"]] == [0, 70, 30, 20, 80, 40]
    assert measured["coverage"] == 6


def test_current_clear_assessment_adds_health_without_changing_public_profile_shape():
    unknown = customer_profile({}, [], [], [], [])
    clear = customer_profile({}, [], [], [], [], risk_assessment_clear=True)
    assert [d["value"] for d in clear["dimensions"]] == [None, None, None, 0, None, 100]
    assert clear["coverage"] == unknown["coverage"] + 1
    assert clear.keys() == unknown.keys()
    assert clear["dimensions"][:-1] == unknown["dimensions"][:-1]
    assert clear["dimensions"][-1] == {
        "code": "risk_health", "label": "风险健康", "value": 100,
        "basis": "已完成当前可见记录的风险评估，未发现风险",
    }
    assert clear["version"] == "customer_profile_v1"
    assert clear["total"] == 6 and clear["scope"] == "current_authorized_records"


@pytest.mark.parametrize("clear", [False, None, 0, 1, "true", "succeeded", {}])
def test_no_risk_records_require_an_explicit_verified_boolean(clear):
    profile = customer_profile({}, [], [], [], [], risk_assessment_clear=clear)
    assert profile["dimensions"][-1]["value"] is None
    assert profile["coverage"] == 1


@pytest.mark.parametrize(("risks", "expected"), [
    ([{"severity_code": "critical", "status": "new"}], 20),
    ([{"severity_code": "low", "status": "in_progress"}], 85),
    ([{"severity_code": "unknown", "status": "new"}], None),
    ([{"severity_code": None, "status": "new"}], None),
    ([{"severity_code": "low", "status": "new"}, {"severity_code": None, "status": "new"}], None),
    ([{"severity_code": "critical", "status": "resolved"}], 100),
    ([{"severity_code": None, "status": "accepted"}], 100),
])
def test_registered_risks_keep_precedence_over_clear_assessment(risks, expected):
    original = customer_profile({}, [], [], [], risks)
    clear = customer_profile({}, [], [], [], risks, risk_assessment_clear=True)
    assert clear == original
    assert clear["dimensions"][-1]["value"] == expected


@pytest.mark.parametrize("clear", [False, True])
def test_legacy_arrays_and_aggregate_facts_share_clear_assessment_scoring(clear):
    customer = {"potential_score": 65, "relationship_score": 80}
    legacy = customer_profile(customer, [{"probability": 50, "status": "open"}],
                              [{"status": "archived"}], [{"relationship_role_code": "influencer"}], [],
                              risk_assessment_clear=clear)
    aggregated = customer_profile_from_facts(customer, {
        "max_probability": 50, "confirmed_visit_count": 1, "contact_roles": ["influencer"],
        "risk_count": 0, "open_risk_severities": [], "risk_assessment_clear": clear,
    })
    assert legacy == aggregated
    assert legacy["coverage"] == (6 if clear else 5)


def test_inconsistent_open_risk_facts_cannot_be_replaced_by_clear_assessment():
    profile = customer_profile_from_facts({}, {
        "confirmed_visit_count": 0, "risk_count": 0, "open_risk_severities": [None],
        "risk_assessment_clear": True,
    })
    assert profile["dimensions"][-1]["value"] is None
