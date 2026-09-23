"""Public Agent identities and policy ownership; never a source of runtime credentials or grants."""

from dataclasses import dataclass

from sales_backend.domain.company_rules import TECHNICAL_CAPABILITIES


@dataclass(frozen=True)
class AgentRegistration:
    capability: str
    name: str


# Names describe registered agents, while the company binding is always resolved at request time.
AGENTS = {
    "01a09fca-4a3b-7da4-8522-e68ca2b78074": AgentRegistration("opportunity_advice", "销售智助-商机经营建议-V2"),
    "01a09684-1968-77da-bfd0-e5e0ab59f015": AgentRegistration("opportunity_advice", "销售智助-商机经营建议-V1"),
    "01a09eff-d317-7725-8941-7b8e37f4a641": AgentRegistration("visit_quality", "销售智助-拜访记录质检"),
    "01a09a6c-97c7-7631-b580-c3b2b14739ad": AgentRegistration("competency_review", "销售智助-销售六维能力复盘-V1"),
    "01a09684-1c0b-763f-b70c-84ea7d2dac08": AgentRegistration("visit_advice", "销售智助-单次拜访建议-V1"),
    "01a09684-1692-78ec-b1e7-07838158bb36": AgentRegistration("customer_advice", "销售智助-客户经营建议-V1"),
    "01a09022-d0ec-7703-b59f-0699150d63a7": AgentRegistration("operating_report", "销售智助-销售经营即时总结-BETA"),
    "01a09022-cb7b-7f33-8b58-410850ac1d8a": AgentRegistration("today_tasks", "销售智助-今日待办规划-BETA"),
    "01a09022-c5ff-7562-bd7d-f8d6c74f5486": AgentRegistration("chatbi", "销售智助-销售经营问数-BETA"),
    "01a09022-c06d-7ac3-b863-8a1345b069ae": AgentRegistration("visit_entry", "销售智助-拜访记录结构化"),
    "01a09022-bb1a-7dab-8481-917e0472c528": AgentRegistration("personal_risks", "销售智助-个人客户风险识别-BETA"),
    "01a09022-b58f-7214-85eb-29d035ea6f2c": AgentRegistration("opportunity_draft", "销售智助-商机新建更新判断-BETA"),
    "01a09021-b848-7e7f-9567-e20859d29bf8": AgentRegistration("battle_map_review", "销售智助-客户作战地图评估-BETA"),
}

RULE_CAPABILITIES = {
    "fde_capabilities": (),
    "customer_quadrant": ("battle_map_review",),
    "visit_admission": ("visit_quality",),
    "home_display": (),
    "task_schedule": ("today_tasks",),
    "score.maturity": (),
    "score.efficiency": (),
    "score.competency": ("competency_review",),
}

RULE_RESPONSIBILITIES = {
    "fde_capabilities": "后端控制自主拜访录入资格，商业编辑与数据权限独立校验",
    "customer_quadrant": "中台依据公司规则评分；后端校验结果并计算象限",
    "visit_admission": "中台依据公司规则质检；后端判断准入，人工确认归档",
    "home_display": "后端按公司规则排列总览消息",
    "task_schedule": "中台提供时间建议；后端计算默认时间，人工确认任务",
    "score.maturity": "后端根据经营事实和公司权重计算总分",
    "score.efficiency": "后端根据经营事实和公司权重计算总分",
    "score.competency": "中台依据证据评估六维能力；后端按公司权重计算总分",
}


def management_metadata(code, bindings):
    """Only link a loaded company binding whose registered capability matches."""
    capability = code.split(".", 1)[1] if code.startswith(("agent_execution.", "agent_business.")) else None
    capabilities = (capability,) if capability else RULE_CAPABILITIES[code]
    related = []
    mismatched = False
    for item in capabilities:
        binding = bindings.get(item)
        agent_id = binding.agent_id if binding else None
        registration = AGENTS.get(agent_id)
        matches = bool(registration and registration.capability == item)
        mismatched = mismatched or bool(registration and not matches)
        related.append({
            "capability": item,
            "capability_label": TECHNICAL_CAPABILITIES[item],
            "agent_name": registration.name if registration else "尚未登记名称" if binding else "当前公司尚未绑定",
            "agent_id": agent_id,
            "expected_snapshot_id": binding.snapshot_id if binding else None,
            "configuration_url": f"https://123.207.235.181/agents/{agent_id}/configure" if matches else None,
            "bound": bool(binding),
        })
    pending = bool(related) and not all(item["bound"] for item in related)
    return {
        "scope": "当前公司",
        "executor": RULE_RESPONSIBILITIES.get(code, "中台判断与后端校验；正式业务写入仍按原审批流程"),
        "validation_status": (
            "绑定能力不匹配" if mismatched else "未绑定" if pending else "已加载绑定" if related else "程序计算"
        ),
        "validation_note": (
            "此处仅展示接口进程已加载的公司绑定配置；实际调用以运行审计为准。" + (
                "登记的智能体用途与此业务能力不一致；配置快捷入口已关闭，请核对绑定。"
                if mismatched else "当前公司尚未完整绑定相关中台能力。"
                if pending else "绑定与预期快照不代表中台实际执行版本或业务验收通过。"
            ) if related else "规则由后端直接执行，无需发布中台 Agent。"
        ),
        "related_agents": related,
    }
