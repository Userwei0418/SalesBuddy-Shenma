from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sales_backend.domain.concurrency import VersionConflict, require_version
from sales_backend.repositories.attribute_overlay import (
    overlay_customer_attributes,
    overlay_risk_attributes,
    overlay_task_attributes,
    overlay_visit_attributes,
)


def test_require_version_skips_when_client_omits_it() -> None:
    require_version(3, None)


def test_require_version_accepts_matching_int() -> None:
    require_version(3, 3)
    require_version(3, "3")


def test_require_version_rejects_stale_or_garbage() -> None:
    with pytest.raises(VersionConflict, match="刷新"):
        require_version(4, 3)
    with pytest.raises(VersionConflict, match="刷新"):
        require_version(1, "x")


def test_customer_attributes_overlay_prefers_columns() -> None:
    row = overlay_customer_attributes(
        {
            "attributes": {"next_action": "旧", "seed_key": "keep"},
            "import_meta": {"sourceWorkbook": "demo.xlsx"},
            "next_action": "新下一步",
            "operation_type": "long_term",
            "cooperation_years": Decimal("3"),
            "main_business": None,
            "customer_budget": "",
        }
    )
    assert row["attributes"]["next_action"] == "新下一步"
    assert row["attributes"]["operation_type"] == "long_term"
    assert row["attributes"]["cooperation_years"] == 3
    assert row["attributes"]["seed_key"] == "keep"
    assert row["attributes"]["sourceWorkbook"] == "demo.xlsx"
    assert "main_business" not in row["attributes"]
    assert "customer_budget" not in row["attributes"]


def test_task_attributes_overlay_exposes_rejection_comment() -> None:
    row = overlay_task_attributes(
        {
            "attributes": {"other": 1},
            "rejection_comment": "当期资源不足",
            "source_code": "mini_program",
            "agent_reason": "客户催",
            "source_follow_up_record": "已演示",
            "source_interaction_at": datetime(2026, 9, 1, tzinfo=UTC),
            "assignees": [{"responsibility": "owner", "account_code": "XS001"}],
        }
    )
    assert row["attributes"]["rejection_comment"] == "当期资源不足"
    assert row["attributes"]["other"] == 1
    assert row["attributes"]["source"] == "mini_program"
    assert row["attributes"]["agent_reason"] == "客户催"
    assert row["attributes"]["follow_up_record"] == "已演示"
    assert row["attributes"]["assignee_account_code"] == "XS001"
    assert "2026-09-01" in row["attributes"]["interaction_at"]


def test_risk_and_visit_overlay_expose_promoted_columns() -> None:
    risk = overlay_risk_attributes(
        {
            "attributes": {},
            "suggested_action": "找决策人确认预算",
            "source_code": "personal_risk_agent",
        }
    )
    visit = overlay_visit_attributes(
        {
            "attributes": {},
            "quality_review": {"follow_up_score": 88},
            "is_first_visit": True,
            "import_meta": {"sourceRow": 12},
        }
    )
    assert risk["attributes"]["suggested_action"] == "找决策人确认预算"
    assert risk["attributes"]["source"] == "personal_risk_agent"
    assert visit["attributes"]["quality_review"]["follow_up_score"] == 88
    assert visit["attributes"]["is_first_visit"] is True
    assert visit["attributes"]["sourceRow"] == 12


@pytest.mark.parametrize("overlay,column,key,empty", [
    (overlay_customer_attributes, "next_action", "next_action", ""),
    (overlay_customer_attributes, "cooperation_years", "cooperation_years", None),
    (overlay_task_attributes, "rejection_comment", "rejection_comment", None),
    (overlay_risk_attributes, "suggested_action", "suggested_action", ""),
    (overlay_visit_attributes, "quality_review", "quality_review", {}),
])
def test_explicitly_empty_column_does_not_resurrect_legacy_attribute(overlay, column, key, empty):
    original = {key: "历史值", "sourceRow": 12}
    result = overlay({"attributes": {key: "更旧的值"}, "import_meta": original, column: empty})
    assert key not in result["attributes"]
    assert result["attributes"]["sourceRow"] == 12
    assert original == {key: "历史值", "sourceRow": 12}
    # A partial projection which did not select the formal column keeps compatibility.
    assert overlay({"attributes": {}, "import_meta": original})["attributes"][key] == "历史值"


@pytest.mark.parametrize("value", [0, False])
def test_zero_and_false_are_present_values_in_attribute_projection(value):
    result = overlay_visit_attributes({"attributes": {"is_first_visit": True}, "is_first_visit": value})
    assert result["attributes"]["is_first_visit"] == value


@pytest.mark.parametrize("assignees,expected", [
    ([{"responsibility": "owner", "account_code": "CURRENT"}], "CURRENT"),
    ([{"responsibility": "owner", "account_code": None}], None),
    ([{"responsibility": "collaborator", "account_code": "OTHER"}], None),
    ([], None),
])
def test_task_compatibility_account_tracks_current_owner(assignees, expected):
    original = {"assignee_account_code": "ORIGINAL"}
    row = overlay_task_attributes({"attributes": {}, "import_meta": original, "assignees": assignees})
    assert row["attributes"].get("assignee_account_code") == expected
    assert original == {"assignee_account_code": "ORIGINAL"}
    assert overlay_task_attributes({"import_meta": original})["attributes"]["assignee_account_code"] == "ORIGINAL"
