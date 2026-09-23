"""FDE analysis uses assigned project facts and personally archived visit records."""

from sales_backend.repositories.collaboration import scoped_opportunity_ids
from sales_backend.repositories.fde_dashboard import fde_dashboard
from sales_backend.repositories.tasks import TaskRepository


async def fde_analysis_facts(connection, actor, *, personal=False):
    scope = "self" if personal or actor.role.value == "fde" else "team"
    board = await fde_dashboard(connection, actor, scope=scope)
    _, _, _, projects = await scoped_opportunity_ids(connection, actor, scope=scope)
    ids = [p["id"] for p in projects]
    opportunities = await connection.fetch(
        "SELECT o.id::text,o.name,o.customer_id::text,c.name AS customer_name,o.status,o.probability,o.amount,"
        "o.expected_close_date,u.display_name AS sales_owner FROM crm.opportunity o "
        "JOIN crm.customer c ON c.id=o.customer_id LEFT JOIN platform.user_ref u ON u.id=o.owner_user_ref_id "
        "WHERE o.id=ANY($1::uuid[]) ORDER BY o.updated_at DESC LIMIT 100",
        ids,
    )
    risks = await connection.fetch(
        "SELECT id::text,opportunity_id::text,title,description,status,severity_code FROM insight.risk "
        "WHERE opportunity_id=ANY($1::uuid[]) AND deleted_at IS NULL "
        "AND status IN ('new','pending','in_progress','escalated') ORDER BY opened_at DESC LIMIT 100",
        ids,
    )
    tasks = await TaskRepository().list(connection, status=None, customer_id=None, limit=100, fde_view=scope)
    return {
        "scope": {
            "role": actor.role.value,
            "scope_type": scope,
            "scope_label": board["scope_label"],
            "user_id": actor.user_id,
            "team_ids": list(actor.team_ids),
        },
        "summary": board["summary"],
        "members": board["ranking"],
        "visits": board["recent_visits"],
        "opportunities": [dict(r) for r in opportunities],
        "risks": [dict(r) for r in risks],
        "tasks": tasks,
        "scope_note": "商机金额是参与项目的共享事实，不是FDE独占或分成业绩；拜访只统计本人创建并确认归档的记录，"
        "新老客户均计入。销售勾选参与不计作FDE本人填报，多人分别填报按不同记录统计，不推断物理出访次数。"
        "明细最多100条，拜访最多20条，汇总为完整授权范围。",
        "data_as_of": board["as_of"].isoformat(),
    }
