import pytest

from sales_backend.domain.agent import ActorContext, DataScope, RoleCode
from sales_backend.integrations.senseaudio import SenseAudioError
from sales_backend.services.agent_run import (
    AgentPromptBuilder,
    RunInput,
    ensure_instant_summary_contract,
)


def report_run(role: RoleCode, scope: DataScope, text: str = "生成即时总结") -> RunInput:
    return RunInput(
        run_id="11111111-1111-1111-1111-111111111111",
        conversation_id="22222222-2222-2222-2222-222222222222",
        text=text,
        mode="operating_report",
        customer_id=None,
        actor=ActorContext(
            workspace_id="33333333-3333-3333-3333-333333333333",
            user_id="44444444-4444-4444-4444-444444444444",
            role=role,
            data_scope=scope,
        ),
    )


def test_contract_keeps_model_sections_including_empty_lists() -> None:
    result = ensure_instant_summary_contract(
        report_run(RoleCode.SALES, DataScope.SELF),
        {
            "title": "旧日报",
            "period": "本周",
            "summary": "今日两家客户需要跟进。",
            "safe_customers": [],
            "attention_customers": [{"title": "风险客户", "detail": "回款逾期"}],
            "action_plan": [{"title": "今天联系风险客户"}],
        },
        {"scope": {"scope_label": "个人"}},
    )

    assert result["title"] == "一线销售个人即时总结"
    assert result["period"] == "当前实时状态"
    assert result["scope"] == "个人"
    assert result["safe_customers"] == []
    assert result["attention_customers"][0]["title"] == "风险客户"


def test_contract_fails_retryable_when_model_omits_sections() -> None:
    with pytest.raises(SenseAudioError) as excinfo:
        ensure_instant_summary_contract(
            report_run(RoleCode.SALES, DataScope.SELF),
            {"summary": "模型只给了摘要"},
            {"scope": {"scope_label": "个人"}},
        )

    assert excinfo.value.retryable is True
    assert "attention_customers" in str(excinfo.value)


def test_contract_fails_retryable_when_summary_is_blank() -> None:
    with pytest.raises(SenseAudioError) as excinfo:
        ensure_instant_summary_contract(
            report_run(RoleCode.SALES, DataScope.SELF),
            {
                "summary": "   ",
                "safe_customers": [],
                "attention_customers": [],
                "action_plan": [],
            },
            {"scope": {"scope_label": "个人"}},
        )

    assert excinfo.value.retryable is True
    assert "summary" in str(excinfo.value)


def test_supervisor_and_manager_contracts_require_their_own_sections() -> None:
    supervisor_payload = {
        "summary": "团队两家客户需要跟进。",
        "personal_safe_customers": [],
        "personal_attention_customers": [],
        "team_safe_customers": [],
        "team_attention_customers": [],
        "action_plan": [],
    }
    supervisor = ensure_instant_summary_contract(
        report_run(RoleCode.SUPERVISOR, DataScope.TEAM),
        supervisor_payload,
        {"scope": {"scope_label": "直属团队"}},
    )
    assert supervisor["title"] == "销售主管个人与团队即时总结"
    assert supervisor["scope"] == "直属团队"

    with pytest.raises(SenseAudioError):
        ensure_instant_summary_contract(
            report_run(RoleCode.MANAGER, DataScope.WORKSPACE),
            supervisor_payload,
            {"scope": {"scope_label": "销售部门"}},
        )

    manager = ensure_instant_summary_contract(
        report_run(RoleCode.MANAGER, DataScope.WORKSPACE),
        {
            "summary": "部门当前无重大风险。",
            "safe_customers": [],
            "attention_customers": [],
            "team_comparison": [],
            "action_plan": [],
        },
        {"scope": {"scope_label": "销售部门"}},
    )
    assert manager["title"] == "销售总经理部门即时总结"


def test_chatbi_prompt_uses_task_glossary() -> None:
    prompts = AgentPromptBuilder()
    run = RunInput(
        run_id="11111111-1111-1111-1111-111111111111",
        conversation_id="22222222-2222-2222-2222-222222222222",
        text="我有多少未完成任务",
        mode="chatbi",
        customer_id=None,
        actor=report_run(RoleCode.SALES, DataScope.SELF).actor,
    )
    messages = prompts.build(run, {"summary": {"open_tasks": 27}}, None)
    assert "open_tasks=未完成任务数" in messages[0].content
    assert "只输出一个JSON对象" in messages[0].content

    customer_run = RunInput(
        run_id=run.run_id,
        conversation_id=run.conversation_id,
        text="这个客户风险如何",
        mode="customer_chatbi",
        customer_id="33333333-3333-3333-3333-333333333333",
        actor=run.actor,
    )
    customer_messages = prompts.build(customer_run, {"customer": {"name": "商汤科技"}}, None)
    assert "单个客户问数" in customer_messages[0].content
