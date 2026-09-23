from __future__ import annotations

from typing import Any

from sales_contracts.domain.agent import RoleCode

from sales_contracts.integrations.senseaudio import SenseAudioError

from sales_contracts.services.agent_run.models import RunInput

INSTANT_SUMMARY_CONTRACT: dict[RoleCode, tuple[str, tuple[str, ...]]] = {
    RoleCode.FDE: ("FDE个人协作即时总结", ("safe_customers", "attention_customers", "action_plan")),
    RoleCode.FDE_LEAD: (
        "FDE部门协作即时总结", ("safe_customers", "attention_customers", "team_comparison", "action_plan")
    ),
    RoleCode.SALES: (
        "一线销售个人即时总结",
        ("safe_customers", "attention_customers", "action_plan"),
    ),
    RoleCode.SUPERVISOR: (
        "销售总监个人与团队即时总结",
        (
            "personal_safe_customers",
            "personal_attention_customers",
            "team_safe_customers",
            "team_attention_customers",
            "action_plan",
        ),
    ),
    RoleCode.MANAGER: (
        "销售总经理部门即时总结",
        ("safe_customers", "attention_customers", "team_comparison", "action_plan"),
    ),
}

def ensure_instant_summary_contract(
    run: RunInput,
    result: dict[str, Any],
    facts: dict[str, Any],
) -> dict[str, Any]:
    """校验模型输出是否满足页面契约。缺段落按可重试失败处理，不用规则补写业务内容。"""
    if run.surface == "fde_profile":
        from sales_contracts.domain.fde_coaching import CoachingContractError, validate_coaching

        try:
            return validate_coaching(result, facts)
        except CoachingContractError as exc:
            raise SenseAudioError("协作建议输出未满足来源契约", retryable=True) from exc
    title, section_keys = INSTANT_SUMMARY_CONTRACT[run.actor.role]
    if not isinstance(result, dict):
        raise SenseAudioError("即时总结须为JSON对象", retryable=True)
    normalized = {key: result[key] for key in section_keys if key in result}
    summary = result.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""
    missing = [] if summary else ["summary"]
    missing.extend(key for key in section_keys if not isinstance(normalized.get(key), list))
    if missing:
        raise SenseAudioError(
            "即时总结输出缺少必填段落：" + "、".join(missing),
            retryable=True,
        )
    for key in section_keys:
        items = []
        for item in normalized[key]:
            if not isinstance(item, dict) or any(
                item.get(field) is not None and not isinstance(item[field], str) for field in ('title', 'detail')
            ) or not any(isinstance(item.get(field), str) and item[field].strip() for field in ('title', 'detail')):
                raise SenseAudioError("即时总结段落条目格式不合法：" + key, retryable=True)
            # Display suggestions only. No model-supplied operations, recipient,
            # status or another role's sections enter the saved public card.
            items.append({field: item[field] for field in ('title', 'detail')
                          if isinstance(item.get(field), str)})
        normalized[key] = items
    normalized["title"] = title
    normalized["period"] = "当前实时状态"
    normalized["scope"] = str(facts.get("scope", {}).get("scope_label") or "当前权限范围")
    normalized["summary"] = summary
    return normalized
