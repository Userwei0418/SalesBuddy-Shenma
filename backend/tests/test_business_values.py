from datetime import timedelta
from decimal import Decimal

import pytest

from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.visit_normalize import (
    _amount,
    _duration_minutes,
    _interaction_datetime,
    _normalize_contact_role,
    _normalize_visit_choice,
    _review_next_action,
)


def test_interaction_datetime_accepts_mini_program_iso_value() -> None:
    value = _interaction_datetime("2026-09-02T13:00:00+08:00")
    assert value.isoformat() == "2026-09-02T13:00:00+08:00"


def test_interaction_datetime_defaults_naive_input_to_china_timezone() -> None:
    value = _interaction_datetime("2026-09-02 13:00")
    assert value.utcoffset() == timedelta(hours=8)


def test_interaction_datetime_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="日期格式"):
        _interaction_datetime("明天下午")


def test_interaction_datetime_accepts_chinese_date_and_time() -> None:
    value = _interaction_datetime("2026年9月2日下午2点30分")
    assert value.isoformat() == "2026-09-02T14:30:00+08:00"


def test_interaction_datetime_accepts_chinese_date_only() -> None:
    value = _interaction_datetime("2026年9月2日")
    assert value.isoformat() == "2026-09-02T00:00:00+08:00"


@pytest.mark.parametrize(
    ("source", "expected"),
    [("1000 万元", "10000000.00"), ("1.2亿元", "120000000.00"), ("8万元", "80000.00")],
)
def test_amount_accepts_standard_chinese_units(source: str, expected: str) -> None:
    assert str(_amount(source)) == expected


def test_duration_accepts_approximate_and_decimal_hours() -> None:
    assert _duration_minutes("约60分钟") == 60
    assert _duration_minutes("1.5小时") == 90


@pytest.mark.parametrize(
    ("key", "source", "expected"),
    [
        ("lead_source", "Inbound线索", "公司线索"),
        ("contact_category", "客户高层", "最终客户"),
        ("interaction_mode", "线下拜访", "线下会议"),
        ("expectation_met", "是", "达成100%"),
    ],
)
def test_visit_choices_are_normalized(key: str, source: str, expected: str) -> None:
    assert _normalize_visit_choice(key, source) == expected


@pytest.mark.parametrize(
    ("source", "expected"),
    [("董事长", "决策者"), ("IT总监", "影响者"), ("一线工程师", "使用者")],
)
def test_contact_roles_are_normalized(source: str, expected: str) -> None:
    assert _normalize_contact_role(source) == expected


def test_next_action_review_rechecks_current_text() -> None:
    review = _review_next_action("2026年9月4日14:00由刘志德组织产品演示，目标是获得Henry确认并形成问题清单")
    assert review == {
        "passed": True,
        "time_found": True,
        "goal_or_plan_found": True,
        "suggestions": [],
    }


@pytest.mark.parametrize(
    ("source", "time_found", "plan_found"),
    [
        ("由刘志德组织产品演示并形成问题清单", False, True),
        ("2026年9月4日14:00再处理一下", True, False),
    ],
)
def test_next_action_review_rejects_missing_hard_requirement(source: str, time_found: bool, plan_found: bool) -> None:
    review = _review_next_action(source)
    assert review["passed"] is False
    assert review["time_found"] is time_found
    assert review["goal_or_plan_found"] is plan_found


class _CustomerCreateConnection:
    async def fetch(self, *_args):
        return [{"id": "01000000-0000-0000-0000-000000000002", "name": "南区"}]

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def fetchrow(self, *_args):
        return {"id": "01000000-0000-0000-0000-000000000002", "name": "南区"}

    async def fetchval(self, sql, *args):
        if "security.authorization_snapshot" in sql:
            from tests.authorization_fixtures import permission_snapshot
            return permission_snapshot(self.actor, {"customer.create": "workspace"})
        return False

    async def execute(self, statement: str, *_args):
        self.statements.append(statement)


@pytest.mark.asyncio
async def test_new_customer_does_not_create_opportunity() -> None:
    connection = _CustomerCreateConnection()
    actor = ActorContext(
        workspace_id="00000000-0000-0000-0000-000000000001",
        user_id="02000000-0000-0000-0000-000000000004",
        role=RoleCode.OPERATIONS,
        data_scope=DataScope.WORKSPACE,
        team_ids=("01000000-0000-0000-0000-000000000002",),
    )

    connection.actor = actor
    await CustomerMutationRepository().create(
        connection,
        actor,
        data={
            "name": "商汤科技",
            "company_reference": "ISOLATED-TEST-001",
            "industry": "企业软件",
            "customer_type": "潜在客户",
            "level_code": "Tier-2",
            "source": "自主拓展",
            "target_team": "南区",
            "partner_name": "无",
            "opportunity_name": "Token Plan",
            "estimated_amount": Decimal("1000000"),
            "contact_name": "Henry",
            "contact_title": "副总裁",
            "contact_role": "决策者",
            "demand_summary": "IT需求管理",
            "next_action": "建立线下拜访",
        },
    )

    assert not any("INSERT INTO crm.opportunity" in statement for statement in connection.statements)
    assert any("relationship_role_code" in statement for statement in connection.statements)
