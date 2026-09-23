from inspect import getsource

from sales_backend.api import assistant
from sales_backend.domain.agent import AgentMode, RoleCode
from sales_backend.domain.policy import AgentModeForbidden, assert_mode_allowed


def test_sales_can_open_chatbi_and_customer_chatbi() -> None:
    assert_mode_allowed(RoleCode.SALES, AgentMode.CHATBI)
    assert_mode_allowed(RoleCode.SALES, AgentMode.CUSTOMER_CHATBI)


def test_sales_cannot_open_management_task_agent() -> None:
    try:
        assert_mode_allowed(RoleCode.SALES, AgentMode.MANAGEMENT_TASK)
    except AgentModeForbidden:
        return
    raise AssertionError("sales must not use management_task agent mode")


def test_conversation_api_uses_role_policy_not_hardcoded_sales_chatbi_ban() -> None:
    source = getsource(assistant.create_conversation)
    assert "assert_mode_allowed" in source
    assert "AgentMode.CHATBI" not in source


def test_home_quick_actions_keep_role_playbook() -> None:
    from sales_backend.api.assistant import home_quick_actions
    from sales_backend.domain.capabilities import role_capabilities

    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        assert [item["code"] for item in home_quick_actions(role)] == [
            "customer_claim", "visit_entry", "management_task",
        ]
    for role in (RoleCode.FDE, RoleCode.FDE_LEAD):
        enabled = role_capabilities(role.value, visit_entry_enabled=True)
        assert [item["code"] for item in home_quick_actions(role, enabled)] == ["visit_entry", "management_task"]
        disabled = role_capabilities(role.value, visit_entry_enabled=False)
        assert [item["code"] for item in home_quick_actions(role, disabled)] == ["management_task"]


def test_all_sales_roles_can_structure_and_review_their_visit() -> None:
    for role in (RoleCode.SALES, RoleCode.SUPERVISOR, RoleCode.MANAGER):
        assert_mode_allowed(role, AgentMode.VISIT_ENTRY)
