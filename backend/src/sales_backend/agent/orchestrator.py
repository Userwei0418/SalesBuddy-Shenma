from __future__ import annotations

from sales_backend.domain.agent import (
    ActorContext,
    AgentMode,
    AgentPlan,
    AgentRequest,
)
from sales_backend.domain.policy import assert_mode_allowed

READ_TOOLS = ("metric_catalog.read", "chatbi.query.execute", "drilldown.read")


def build_plan(actor: ActorContext, request: AgentRequest) -> AgentPlan:
    """根据显式页面模式构建确定性计划；不让 LLM 决定是否执行写操作。"""

    assert_mode_allowed(actor.role, request.mode)

    if request.mode in {AgentMode.CHATBI, AgentMode.CUSTOMER_CHATBI}:
        tools = READ_TOOLS
        if request.mode is AgentMode.CUSTOMER_CHATBI:
            if not request.customer_id:
                raise ValueError("customer_chatbi requires customer_id")
            tools = ("customer.read",) + tools
        return AgentPlan(
            mode=request.mode,
            intent_code="bi.query",
            agent_code="chatbi_agent",
            stages=(
                "scope_resolve",
                "query_plan_generate",
                "query_plan_validate",
                "read_only_execute",
                "answer_ground",
            ),
            allowed_tools=tools,
            produces_artifact=False,
            confirmation_required=False,
        )

    if request.mode is AgentMode.TODAY_TASKS:
        return AgentPlan(
            mode=request.mode,
            intent_code="todo.plan",
            agent_code="today_task_agent",
            stages=(
                "assigned_task_read",
                "visit_follow_up_read",
                "time_extract",
                "priority_order",
                "task_materialize",
                "card_render",
            ),
            allowed_tools=(
                "task.assigned.read",
                "visit.follow_up.read",
                "task.follow_up.write",
            ),
            produces_artifact=False,
            confirmation_required=False,
        )

    if request.mode is AgentMode.PERSONAL_RISKS:
        return AgentPlan(
            mode=request.mode,
            intent_code="risk.analyze",
            agent_code="personal_risk_agent",
            stages=(
                "visit_history_read",
                "risk_evidence_analyze",
                "senior_sales_review",
                "risk_materialize",
                "card_render",
            ),
            allowed_tools=(
                "visit.history.read",
                "risk.personal.write",
                "risk.personal.read",
            ),
            produces_artifact=False,
            confirmation_required=False,
        )

    if request.mode is AgentMode.OPERATING_REPORT:
        return AgentPlan(
            mode=request.mode,
            intent_code="report.generate",
            agent_code="operating_report_agent",
            stages=(
                "scope_resolve",
                "period_facts_read",
                "good_bad_risk_analyze",
                "comments_generate",
                "improvement_generate",
                "card_render",
            ),
            allowed_tools=(
                "visit.period.read",
                "task.period.read",
                "risk.period.read",
                "opportunity.period.read",
            ),
            produces_artifact=False,
            confirmation_required=False,
        )

    if request.mode is AgentMode.VISIT_ENTRY:
        return AgentPlan(
            mode=request.mode,
            intent_code="visit.create",
            agent_code="visit_entry_agent",
            stages=(
                "customer_match",
                "opportunity_match",
                "visit_extract_16_fields",
                "first_visit_profile_extract",
                "missing_field_check",
                "artifact_persist",
                "wait_human_confirmation",
            ),
            allowed_tools=(
                "customer.candidate.search",
                "opportunity.candidate.search",
                "visit.form.read",
                "artifact.draft.write",
            ),
            produces_artifact=True,
            confirmation_required=True,
        )

    if request.mode is AgentMode.OPPORTUNITY_DRAFT:
        if not request.customer_id:
            raise ValueError("opportunity_draft requires customer_id")
        return AgentPlan(
            mode=request.mode,
            intent_code="opportunity.upsert",
            agent_code="opportunity_draft_agent",
            stages=(
                "visit_facts_read",
                "current_opportunities_read",
                "create_or_update_decide",
                "opportunity_fields_generate",
                "artifact_persist",
                "wait_human_confirmation",
            ),
            allowed_tools=(
                "customer.read",
                "opportunity.current.read",
                "artifact.draft.write",
            ),
            produces_artifact=True,
            confirmation_required=True,
        )

    if request.mode is AgentMode.CUSTOMER_CREATE:
        return AgentPlan(
            mode=request.mode,
            intent_code="customer.create",
            agent_code="customer_draft_agent",
            stages=(
                "customer_extract",
                "duplicate_candidate_search",
                "assignee_scope_check",
                "artifact_persist",
                "wait_human_confirmation",
            ),
            allowed_tools=(
                "customer.duplicate.search",
                "assignee.allowed.list",
                "dictionary.read",
                "artifact.draft.write",
            ),
            produces_artifact=True,
            confirmation_required=True,
        )

    return AgentPlan(
        mode=request.mode,
        intent_code="management.assign",
        agent_code="management_task_agent",
        stages=(
            "task_extract",
            "assignee_scope_check",
            "due_at_validate",
            "artifact_persist",
            "wait_human_confirmation",
        ),
        allowed_tools=(
            "assignee.allowed.list",
            "customer.read",
            "artifact.draft.write",
        ),
        produces_artifact=True,
        confirmation_required=True,
    )
