"""Bounded FDE home projections; dashboard and full task rows are separate reads."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sales_backend.repositories.tasks import TASK_HANDOVER_REQUIRED_SQL, TaskRepository


async def fde_home(connection, actor):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_visits = await connection.fetchval(
        "SELECT count(*) FROM security.fde_recorded_visit_history($1,$2,$3::uuid,NULL,NULL)",
        today, today + timedelta(days=1), actor.user_id,
    )
    projects = await connection.fetchrow(
        """WITH personal_projects AS MATERIALIZED (
          SELECT o.id,o.status FROM crm.opportunity o
          WHERE o.deleted_at IS NULL AND EXISTS (
            SELECT 1 FROM crm.opportunity_participant p WHERE p.opportunity_id=o.id
              AND p.user_ref_id=$1::uuid AND p.participant_role='fde'
              AND statement_timestamp()>=p.valid_from AND statement_timestamp()<p.valid_to
              AND security.fde_user_is_active(p.user_ref_id)))
        SELECT (SELECT count(*) FROM personal_projects WHERE status='open') AS open_opportunities,
          (SELECT count(*) FROM insight.risk r WHERE r.deleted_at IS NULL
            AND r.status IN ('new','pending','in_progress','escalated')
            AND EXISTS(SELECT 1 FROM personal_projects p WHERE p.id=r.opportunity_id)) AS open_risks""",
        actor.user_id,
    )
    predicates, args = TaskRepository._filters(inbox=True, fde_view="self")
    assert not args
    personal = " AND ".join(predicates)
    action = """CASE WHEN t.status='pending_review' THEN t.creator_user_ref_id=common.current_user_ref_id() ELSE EXISTS(SELECT 1 FROM workflow.v_task_action_recipient ar
        WHERE ar.task_id=t.id AND ar.assignee_user_ref_id=common.current_user_ref_id()
          AND ar.assignee_role=common.current_role_code()) END"""
    active = "t.status IN ('pending_confirm','pending_execution','in_progress','deferred','pending_review')"
    counts = await connection.fetchrow(
        "SELECT count(*) FILTER(WHERE " + active + " AND " + action + ") AS my_tasks,"
        " count(*) FILTER(WHERE t.status='completed' AND EXISTS("
        "SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND a.responsibility='owner'"
        " AND a.assignee_user_ref_id=common.current_user_ref_id()"
        " AND a.assignee_role=common.current_role_code())) AS completed_tasks"
        " FROM workflow.task t WHERE " + personal,
    )
    # Page first, without collecting candidates, events, attachments or dashboard
    # rows. Priority cards retain the previous inclusion of overdue tasks.
    cards_sql = (
        "SELECT t.id::text,t.title,t.description,t.due_at,t.status,t.target_position"
        " FROM workflow.task t WHERE " + personal + " AND " + active + " AND " + action
    )
    ordering = " ORDER BY t.due_at,t.created_at DESC,t.id LIMIT 8"
    tasks = await connection.fetch(cards_sql + ordering)
    urgent = await connection.fetch(cards_sql + " AND t.due_at<=$1" + ordering, now + timedelta(hours=12))

    def card(task):
        return {
            "source_type": "task", "source_id": task["id"], "title": task["title"],
            "detail": task["description"] or "进入任务详情查看执行要求。",
            "meta": "截止 " + task["due_at"].astimezone(now.tzinfo).strftime("%m-%d %H:%M"),
            "tag": "待领取" if task["status"] == "pending_confirm" and task["target_position"]
                else "待接受" if task["status"] == "pending_confirm"
                else "待发起人确认" if task["status"] == "pending_review"
                else "已逾期" if task["due_at"] < now else "待完成",
            "tone": "warning" if task["due_at"] < now else "normal", "scope_group": "personal",
        }

    team_summary = None
    if actor.role.value == "fde_lead":
        team_predicates, team_args = TaskRepository._filters(fde_view="team")
        assert not team_args
        row = await connection.fetchrow(
            "SELECT count(*) FILTER(WHERE t.due_at<$1) AS overdue,"
            " count(*) FILTER(WHERE t.target_position IS NOT NULL AND t.status='pending_confirm') AS claim,"
            " count(*) FILTER(WHERE " + TASK_HANDOVER_REQUIRED_SQL + ") AS handover"
            " FROM workflow.task t WHERE " + " AND ".join(team_predicates)
            + " AND t.status NOT IN ('completed','cancelled')", now,
        )
        team_summary = dict(row)
    return {"today_visits": today_visits, **dict(projects), **dict(counts),
            "task_items": [card(t) for t in tasks], "priority_items": [card(t) for t in urgent],
            "team_summary": team_summary}
