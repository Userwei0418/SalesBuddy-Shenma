from __future__ import annotations

from sales_backend.domain.agent import ActorContext, AgentMode, AssigneeContext, RoleCode


class AgentModeForbidden(PermissionError):
    """当前角色不能进入所请求的 Agent 写作模式。"""


ROLE_MODES: dict[RoleCode, frozenset[AgentMode]] = {
    RoleCode.FDE: frozenset({AgentMode.CHATBI, AgentMode.CUSTOMER_CHATBI, AgentMode.OPERATING_REPORT, AgentMode.VISIT_ENTRY}),
    RoleCode.FDE_LEAD: frozenset({AgentMode.CHATBI, AgentMode.CUSTOMER_CHATBI, AgentMode.OPERATING_REPORT, AgentMode.VISIT_ENTRY}),
    RoleCode.SALES: frozenset(
        {
            AgentMode.CHATBI,
            AgentMode.CUSTOMER_CHATBI,
            AgentMode.TODAY_TASKS,
            AgentMode.PERSONAL_RISKS,
            AgentMode.OPERATING_REPORT,
            AgentMode.VISIT_ENTRY,
            AgentMode.OPPORTUNITY_DRAFT,
        }
    ),
    RoleCode.SUPERVISOR: frozenset(
        {
            AgentMode.CHATBI,
            AgentMode.CUSTOMER_CHATBI,
            AgentMode.MANAGEMENT_TASK,
            AgentMode.OPERATING_REPORT,
            AgentMode.VISIT_ENTRY,
        }
    ),
    RoleCode.MANAGER: frozenset(
        {
            AgentMode.CHATBI,
            AgentMode.CUSTOMER_CHATBI,
            AgentMode.MANAGEMENT_TASK,
            AgentMode.OPERATING_REPORT,
            AgentMode.VISIT_ENTRY,
        }
    ),
}


def assert_mode_allowed(role: RoleCode, mode: AgentMode) -> None:
    if mode not in ROLE_MODES.get(role, frozenset()):
        raise AgentModeForbidden(f"role={role.value} cannot use mode={mode.value}")


def assert_customer_reassign_allowed(actor: ActorContext) -> None:
    if actor.role is RoleCode.SALES:
        raise AgentModeForbidden("sales cannot reassign customers")


def assert_opportunity_create_allowed(actor: ActorContext) -> None:
    if actor.role not in {RoleCode.SALES, RoleCode.OPERATIONS, RoleCode.ADMINISTRATOR}:
        raise AgentModeForbidden("current role cannot create opportunities")


def _assert_same_workspace(actor: ActorContext, assignee: AssigneeContext) -> None:
    if actor.workspace_id != assignee.workspace_id:
        raise AgentModeForbidden("assignee is outside current workspace")


def assert_task_assignee_allowed(actor: ActorContext, assignee: AssigneeContext) -> None:
    """REST 建任务与 Agent 下发共用范围：总经理→总监/销售；总监→本团队销售；一线→本团队销售（含自己）。"""

    _assert_same_workspace(actor, assignee)
    if actor.role is RoleCode.MANAGER and assignee.role in {
        RoleCode.SUPERVISOR,
        RoleCode.SALES,
    }:
        return
    if (
        actor.role is RoleCode.SUPERVISOR
        and assignee.role is RoleCode.SALES
        and set(actor.team_ids).intersection(assignee.team_ids)
    ):
        return
    if (
        actor.role is RoleCode.SALES
        and assignee.role is RoleCode.SALES
        and set(actor.team_ids).intersection(assignee.team_ids)
    ):
        return
    raise AgentModeForbidden("assignee is outside task assignment scope")


def assert_customer_assignee_allowed(actor: ActorContext, assignee: AssigneeContext) -> None:
    """新客户只能下发给一线销售；主管只能选择自己的直属团队。"""

    _assert_same_workspace(actor, assignee)
    if assignee.role is not RoleCode.SALES:
        raise AgentModeForbidden("customer can only be assigned to sales")
    if actor.role is RoleCode.MANAGER:
        return
    if actor.role is RoleCode.SALES and actor.user_id == assignee.user_id:
        return
    if actor.role is RoleCode.SUPERVISOR and set(actor.team_ids).intersection(assignee.team_ids):
        return
    raise AgentModeForbidden("assignee is outside customer assignment scope")
