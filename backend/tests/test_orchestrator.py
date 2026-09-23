from __future__ import annotations

import unittest

from sales_backend.agent.orchestrator import build_plan
from sales_backend.domain.agent import (
    ActorContext,
    AgentMode,
    AgentRequest,
    AssigneeContext,
    DataScope,
    RoleCode,
)
from sales_backend.domain.policy import (
    AgentModeForbidden,
    assert_customer_assignee_allowed,
    assert_task_assignee_allowed,
)


def actor(role: RoleCode, scope: DataScope) -> ActorContext:
    return ActorContext(
        workspace_id="11111111-1111-1111-1111-111111111111",
        user_id="22222222-2222-2222-2222-222222222222",
        role=role,
        data_scope=scope,
    )


def request(mode: AgentMode, customer_id: str | None = None) -> AgentRequest:
    return AgentRequest(mode=mode, text="测试问题", customer_id=customer_id)


class OrchestratorTests(unittest.TestCase):
    def test_sales_bottom_input_is_read_only_chatbi(self) -> None:
        plan = build_plan(actor(RoleCode.SALES, DataScope.SELF), request(AgentMode.CHATBI))
        self.assertEqual(plan.intent_code, "bi.query")
        self.assertFalse(plan.confirmation_required)
        self.assertFalse(plan.produces_artifact)
        self.assertIn("chatbi.query.execute", plan.allowed_tools)

    def test_sales_visit_entry_requires_confirmation(self) -> None:
        plan = build_plan(actor(RoleCode.SALES, DataScope.SELF), request(AgentMode.VISIT_ENTRY))
        self.assertEqual(plan.intent_code, "visit.create")
        self.assertTrue(plan.confirmation_required)
        self.assertTrue(plan.produces_artifact)
        self.assertIn("wait_human_confirmation", plan.stages)

    def test_sales_opportunity_draft_decides_create_or_update(self) -> None:
        plan = build_plan(
            actor(RoleCode.SALES, DataScope.SELF),
            request(
                AgentMode.OPPORTUNITY_DRAFT,
                "33333333-3333-3333-3333-333333333333",
            ),
        )
        self.assertEqual(plan.intent_code, "opportunity.upsert")
        self.assertEqual(plan.agent_code, "opportunity_draft_agent")
        self.assertIn("create_or_update_decide", plan.stages)
        self.assertTrue(plan.confirmation_required)

    def test_opportunity_draft_requires_customer(self) -> None:
        with self.assertRaisesRegex(ValueError, "customer_id"):
            build_plan(
                actor(RoleCode.SALES, DataScope.SELF),
                request(AgentMode.OPPORTUNITY_DRAFT),
            )

    def test_sales_cannot_bypass_operations_customer_creation(self) -> None:
        from sales_backend.domain.policy import AgentModeForbidden

        with self.assertRaises(AgentModeForbidden):
            build_plan(actor(RoleCode.SALES, DataScope.SELF), request(AgentMode.CUSTOMER_CREATE))

    def test_sales_today_tasks_uses_dedicated_agent(self) -> None:
        plan = build_plan(actor(RoleCode.SALES, DataScope.SELF), request(AgentMode.TODAY_TASKS))
        self.assertEqual(plan.intent_code, "todo.plan")
        self.assertEqual(plan.agent_code, "today_task_agent")
        self.assertFalse(plan.confirmation_required)
        self.assertIn("time_extract", plan.stages)
        self.assertIn("task.follow_up.write", plan.allowed_tools)

    def test_sales_personal_risks_uses_dedicated_agent(self) -> None:
        plan = build_plan(
            actor(RoleCode.SALES, DataScope.SELF),
            request(AgentMode.PERSONAL_RISKS),
        )
        self.assertEqual(plan.intent_code, "risk.analyze")
        self.assertEqual(plan.agent_code, "personal_risk_agent")
        self.assertFalse(plan.confirmation_required)
        self.assertIn("senior_sales_review", plan.stages)
        self.assertIn("risk.personal.write", plan.allowed_tools)

    def test_reports_are_read_only_and_role_scoped(self) -> None:
        for role, scope in (
            (RoleCode.SALES, DataScope.SELF),
            (RoleCode.SUPERVISOR, DataScope.TEAM),
            (RoleCode.MANAGER, DataScope.WORKSPACE),
        ):
            with self.subTest(role=role):
                plan = build_plan(actor(role, scope), request(AgentMode.OPERATING_REPORT))
                self.assertEqual(plan.agent_code, "operating_report_agent")
                self.assertEqual(plan.intent_code, "report.generate")
                self.assertFalse(plan.confirmation_required)
                self.assertFalse(plan.produces_artifact)

    def test_supervisor_can_record_visits(self) -> None:
        plan = build_plan(actor(RoleCode.SUPERVISOR, DataScope.TEAM), request(AgentMode.VISIT_ENTRY))
        self.assertEqual(plan.agent_code, "visit_entry_agent")
        self.assertTrue(plan.confirmation_required)

    def test_management_roles_can_create_task_drafts(self) -> None:
        for role, scope in (
            (RoleCode.SUPERVISOR, DataScope.TEAM),
            (RoleCode.MANAGER, DataScope.WORKSPACE),
        ):
            with self.subTest(role=role):
                plan = build_plan(actor(role, scope), request(AgentMode.MANAGEMENT_TASK))
                self.assertEqual(plan.agent_code, "management_task_agent")
                self.assertTrue(plan.confirmation_required)

    def test_sales_cannot_create_management_task(self) -> None:
        with self.assertRaises(AgentModeForbidden):
            build_plan(actor(RoleCode.SALES, DataScope.SELF), request(AgentMode.MANAGEMENT_TASK))

    def test_customer_context_requires_customer_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "customer_id"):
            build_plan(
                actor(RoleCode.SUPERVISOR, DataScope.TEAM),
                request(AgentMode.CUSTOMER_CHATBI),
            )

    def test_sales_can_assign_task_to_same_team_peer(self) -> None:
        sales = actor(RoleCode.SALES, DataScope.SELF).model_copy(update={"team_ids": ("south",)})
        peer = AssigneeContext(
            workspace_id=sales.workspace_id,
            user_id="33333333-3333-3333-3333-333333333333",
            role=RoleCode.SALES,
            team_ids=("south",),
        )
        assert_task_assignee_allowed(sales, peer)

    def test_sales_cannot_assign_task_outside_team(self) -> None:
        sales = actor(RoleCode.SALES, DataScope.SELF).model_copy(update={"team_ids": ("south",)})
        other = AssigneeContext(
            workspace_id=sales.workspace_id,
            user_id="33333333-3333-3333-3333-333333333333",
            role=RoleCode.SALES,
            team_ids=("east",),
        )
        with self.assertRaises(AgentModeForbidden):
            assert_task_assignee_allowed(sales, other)
        manager = actor(RoleCode.MANAGER, DataScope.WORKSPACE)
        supervisor = AssigneeContext(
            workspace_id=manager.workspace_id,
            user_id="33333333-3333-3333-3333-333333333333",
            role=RoleCode.SUPERVISOR,
            team_ids=("north-east",),
        )
        assert_task_assignee_allowed(manager, supervisor)

    def test_supervisor_cannot_assign_task_outside_direct_team(self) -> None:
        supervisor = actor(RoleCode.SUPERVISOR, DataScope.TEAM).model_copy(update={"team_ids": ("south",)})
        sales = AssigneeContext(
            workspace_id=supervisor.workspace_id,
            user_id="33333333-3333-3333-3333-333333333333",
            role=RoleCode.SALES,
            team_ids=("north-east",),
        )
        with self.assertRaises(AgentModeForbidden):
            assert_task_assignee_allowed(supervisor, sales)

    def test_customer_cannot_be_assigned_to_supervisor(self) -> None:
        manager = actor(RoleCode.MANAGER, DataScope.WORKSPACE)
        supervisor = AssigneeContext(
            workspace_id=manager.workspace_id,
            user_id="33333333-3333-3333-3333-333333333333",
            role=RoleCode.SUPERVISOR,
        )
        with self.assertRaises(AgentModeForbidden):
            assert_customer_assignee_allowed(manager, supervisor)

    def test_sales_can_only_assign_customer_to_self(self) -> None:
        sales_actor = actor(RoleCode.SALES, DataScope.SELF)
        self_assignee = AssigneeContext(
            workspace_id=sales_actor.workspace_id,
            user_id=sales_actor.user_id,
            role=RoleCode.SALES,
            team_ids=("south",),
        )
        assert_customer_assignee_allowed(sales_actor, self_assignee)
        other_assignee = self_assignee.model_copy(update={"user_id": "33333333-3333-3333-3333-333333333333"})
        with self.assertRaises(AgentModeForbidden):
            assert_customer_assignee_allowed(sales_actor, other_assignee)
