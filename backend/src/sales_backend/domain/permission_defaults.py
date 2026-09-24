"""Initial templates only. Published tenant configuration replaces these defaults."""

from sales_backend.domain.permission_catalog import CATALOG

ROLE_LABELS = {
    "sales": "销售", "supervisor": "销售主管", "manager": "销售总经理",
    "fde": "FDE", "fde_lead": "FDE 主管", "operations": "运营", "administrator": "系统管理员",
}

BUSINESS_COMMON = frozenset({
    "access.mini_program", "overview.read", "customer.reference", "customer.read", "battle_map.read",
    "opportunity.read", "visit.read", "task.read", "task.create_daily", "task.create_customer",
    "task.accept", "task.decline", "task.complete", "task.cancel", "task.review", "task.assign", "task.coordinate",
    "advice.request", "advice.read", "advice.decide", "advice.opportunity", "advice.visit", "agent.chatbi", "agent.customer_chatbi",
    "agent.operating_report", "agent.history", "partner.read", "directory.read",
    "notification.read", "notification.mark_read",
})
VISIT_ENTRY = frozenset({
    "visit.create", "visit.supplement", "visit.upload", "visit.transcribe", "visit.retry_import",
    "visit.download_original", "visit.structure", "visit.quality_review",
})
SALES_COMMON = BUSINESS_COMMON | VISIT_ENTRY | frozenset({
    "visit.first_visit", "visit.attendance_manage",
    "access.business_web", "weekly_report.generate", "weekly_report.read", "weekly_report.edit", "weekly_report.cancel",
    "customer.update", "customer.claim", "customer.claim_directory", "opportunity.create", "opportunity.update", "opportunity.close",
    "opportunity.reopen", "opportunity.fde_members", "task.assign", "risk.read", "risk.resolve", "risk.auto_review", "advice.customer",
    "actual.read", "dashboard.read", "dashboard.ranking", "profile.sales_read", "profile.sales_review",
    "target.read", "target.submit", "target.history", "agent.opportunity_draft",
    "demo_scene.read", "demo_scene.create", "demo_scene.update", "demo_scene.delete",
})
CONSOLE_COMMON = frozenset({
    "access.console", "history.import", "overview.read", "visit.read", "visit.download_original", "customer.reference", "customer.read", "customer.create", "customer.update",
    "customer.claim_directory", "customer.claim_review", "customer.release", "customer.resolve_owner", "customer.export",
    "opportunity.read", "opportunity.create", "opportunity.update", "opportunity.close", "opportunity.reopen",
    "opportunity.create_for_others", "opportunity.quote_create", "opportunity.fde_members", "opportunity.export",
    "task.read", "task.create_daily", "task.create_customer", "task.assign", "task.accept", "task.decline",
    "task.complete", "task.cancel", "task.coordinate", "task.review", "actual.read", "actual.create", "actual.void",
    "target.read", "target.submit", "target.manage", "target.approve", "target.history", "target.fde_department", "partner.read", "partner.manage",
    "organization.read", "organization.manage", "account.create", "account.update", "account.reset_password",
    "account.unlock", "company.read", "rule.read", "rule.draft", "rule.preview", "ai.usage_read", "ai.usage_export",
    "ai.run_read", "ai.run_export", "ai.usage_rules_manage", "ai.execution_read", "audit.read", "audit.export", "audit.events_read", "audit.events_export",
    "audit.business_read", "audit.business_export", "directory.read", "notification.read", "notification.mark_read",
})


def default_permissions(role: str) -> list[dict]:
    if role == "administrator":
        # Administrative authority does not create a sales appointment. Mini-program
        # business use still requires a business role or an explicit additional grant.
        codes = CONSOLE_COMMON | {
            "authorization.read", "authorization.roles_manage", "authorization.accounts_manage", "authorization.audit",
            "account.password_policy", "company.update", "rule.publish", "rule.restore",
            "ai.config_read", "ai.config_test", "ai.config_publish", "ai.config_rollback",
            "feishu.read", "feishu.configure", "feishu.control", "feishu.recover",
        }
    elif role == "operations":
        codes = CONSOLE_COMMON - {"opportunity.create", "opportunity.create_for_others"}
    elif role in {"sales", "supervisor", "manager"}:
        codes = SALES_COMMON | ({"agent.today_tasks", "agent.personal_risks"} if role == "sales" else {
            "agent.management_task", "task.coordinate", "actual.create", "actual.void",
        })
        if role == "manager":
            codes = codes | {"ai.usage_read"}
    elif role in {"fde", "fde_lead"}:
        codes = BUSINESS_COMMON | VISIT_ENTRY | {"profile.fde_read", "profile.fde_review", "profile.fde_activity", "risk.read",
                                               "demo_scene.read", "demo_scene.create", "demo_scene.update", "demo_scene.delete", "actual.read", "dashboard.ranking"}
        if role == "fde_lead":
            codes = codes | {"task.coordinate", "opportunity.fde_members"}
    else:
        raise ValueError("未知内置角色")
    result = []
    for code in sorted(codes):
        definition = CATALOG[code]
        scope = "inherit"
        if definition.scopes == ("workspace",) or code in {"customer.reference", "customer.claim_directory", "dashboard.ranking"}:
            scope = "workspace"
        elif definition.scopes == ("self",) or code == "agent.history" or (code == "customer.claim" and role in {"sales", "supervisor", "manager"}) or (code == "target.submit" and role not in {"operations", "administrator"}):
            scope = "self"
        elif role in {"fde", "fde_lead"} and code in {"visit.create", "visit.supplement", "visit.structure", "visit.quality_review"}:
            scope = "assigned"
        elif role == "fde" and code in {"task.read", "risk.read", "customer.read", "opportunity.read", "battle_map.read", "demo_scene.read", "demo_scene.create", "demo_scene.update", "demo_scene.delete", "actual.read", "visit.read", "overview.read", "profile.fde_read", "profile.fde_activity", "agent.chatbi", "agent.customer_chatbi", "agent.operating_report", "advice.request", "advice.read", "advice.decide", "advice.opportunity", "advice.visit"}:
            scope = "assigned"
        result.append({"permission": code, "scope": scope, "team_ids": []})
    return result
