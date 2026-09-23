import pytest

from sales_backend.domain.agent_management import management_metadata
from sales_backend.services.agent_platform.inference import AgentBinding


def test_management_uses_company_binding_without_implying_execution_acceptance():
    agent = "01a09021-b848-7e7f-9567-e20859d29bf8"
    metadata = management_metadata("customer_quadrant", {
        "battle_map_review": AgentBinding(agent_id=agent, snapshot_id="expected-only"),
    })
    assert metadata["validation_status"] == "已加载绑定"
    related = metadata["related_agents"][0]
    assert related["configuration_url"] == f"https://123.207.235.181/agents/{agent}/configure"
    assert related["expected_snapshot_id"] == "expected-only"
    assert "不代表" in metadata["validation_note"]
    assert "此处仅展示接口进程已加载的公司绑定配置；实际调用以运行审计为准" in metadata["validation_note"]


def test_unbound_company_never_inherits_demo_agent_link():
    metadata = management_metadata("agent_execution.battle_map_review", {})
    assert metadata["validation_status"] == "未绑定"
    assert metadata["related_agents"][0]["configuration_url"] is None
    assert metadata["related_agents"][0]["agent_id"] is None


def test_unregistered_binding_has_no_trusted_configuration_link():
    metadata = management_metadata("agent_business.battle_map_review", {
        "battle_map_review": AgentBinding(agent_id="unregistered", snapshot_id="expected-only"),
    })
    assert metadata["related_agents"][0]["bound"] is True
    assert metadata["validation_status"] == "已加载绑定"
    assert metadata["related_agents"][0]["configuration_url"] is None


def test_deterministic_rule_is_not_misrepresented_as_agent():
    metadata = management_metadata("score.efficiency", {})
    assert metadata["related_agents"] == []
    assert metadata["validation_status"] == "程序计算"


def test_registered_agent_for_another_capability_keeps_identity_but_cannot_link():
    opportunity_agent = "01a09022-b58f-7214-85eb-29d035ea6f2c"
    metadata = management_metadata("agent_execution.battle_map_review", {
        "battle_map_review": AgentBinding(agent_id=opportunity_agent, snapshot_id="expected-only"),
    })

    assert metadata["validation_status"] == "绑定能力不匹配"
    related = metadata["related_agents"][0]
    assert related["bound"] is True
    assert related["agent_id"] == opportunity_agent
    assert related["agent_name"] == "销售智助-商机新建更新判断-BETA"
    assert related["expected_snapshot_id"] == "expected-only"
    assert related["configuration_url"] is None
    assert "配置快捷入口已关闭" in metadata["validation_note"]


@pytest.mark.parametrize("agent_id", [
    "01a09fca-4a3b-7da4-8522-e68ca2b78074",
    "01a09684-1968-77da-bfd0-e5e0ab59f015",
])
def test_both_registered_opportunity_advice_versions_keep_the_advice_responsibility(agent_id):
    metadata = management_metadata("agent_execution.opportunity_advice", {
        "opportunity_advice": AgentBinding(agent_id=agent_id, snapshot_id="expected-only"),
    })

    assert metadata["validation_status"] == "已加载绑定"
    assert metadata["related_agents"][0]["configuration_url"] == f"https://123.207.235.181/agents/{agent_id}/configure"
