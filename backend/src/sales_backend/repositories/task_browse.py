"""Task list projections: database-filtered counts and one bounded card page."""

from datetime import datetime
from zoneinfo import ZoneInfo

from sales_backend.repositories.task_alignment import TASK_ALIGNMENT_READ_SQL
from sales_backend.repositories.task_execution_projection import normalize_execution
from sales_backend.repositories.task_outcomes import NORMAL_COMPLETED_SQL, ENDED_SQL, STOPPED_SQL, UNKNOWN_OUTCOME_SQL, ENDED_AT_SQL, END_EVENT_SQL
from sales_backend.repositories.tasks import TASK_CAN_CANCEL_SQL, TASK_HANDOVER_REQUIRED_SQL, TaskRepository

PENDING = "t.status NOT IN ('completed','cancelled')"
TODAY = "date_trunc('day',timezone('Asia/Shanghai',clock_timestamp())) AT TIME ZONE 'Asia/Shanghai'"


def selection(
    *,
    status=None,
    tab="pending",
    overview=None,
    completed_year=None,
    completed_quarters=(),
    member_id=None,
    team_id=None,
    team=None,
    member=None,
    opportunity_only=False,
    **scope,
):
    predicates, args = TaskRepository._filters(**scope)

    def bind(value, cast="text"):
        args.append(value)
        return f"${len(args)}::{cast}"

    if opportunity_only:
        predicates.append("t.opportunity_id IS NOT NULL")
    if member_id or team_id:
        # Reuse the RLS-authorized member task set only within this statement,
        # avoiding repeated assignee permission checks for each candidate task.
        owner_filters = ["a.responsibility='owner'"]
        if member_id:
            owner_filters.append("a.assignee_user_ref_id=" + bind(member_id, "uuid"))
        if team_id:
            owner_filters.append("a.assignee_team_id=" + bind(team_id, "uuid"))
        predicates.append(
            "t.id IN (WITH member_tasks AS MATERIALIZED ("  # noqa: S608 -- Owner selectors are bound values.
            "SELECT a.task_id FROM workflow.task_assignee a "
            "WHERE " + " AND ".join(owner_filters)
            + ") SELECT task_id FROM member_tasks)"
        )
    for value, table, column in (
        (team, "platform.team", "assignee_team_id"),
        (member, "platform.user_ref", "assignee_user_ref_id"),
    ):
        if value and value != "all":
            field = "name" if column == "assignee_team_id" else "display_name"
            predicates.append(
                f"EXISTS(SELECT 1 FROM workflow.task_assignee a JOIN {table} p ON p.id=a.{column} "
                "WHERE a.task_id=t.id AND a.responsibility='owner' AND p." + field + "=" + bind(value) + ")"
            )
    if completed_year:
        date_column = "(" + ENDED_AT_SQL + ")" if tab == "ended" else "t.completed_at"
        ranges = []
        for quarter in sorted(set(completed_quarters or (1, 2, 3, 4))):
            month = (quarter - 1) * 3 + 1
            start = datetime(completed_year, month, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
            end = datetime(
                completed_year + (quarter == 4), 1 if quarter == 4 else month + 3, 1, tzinfo=ZoneInfo("Asia/Shanghai")
            )
            ranges.append(
                "(" + date_column + " >= "
                + bind(start, "timestamptz")
                + " AND " + date_column + " < "
                + bind(end, "timestamptz")
                + ")"
            )
        predicates.append("(" + ("NOT (" + ENDED_SQL + ")" if tab == "ended" else "t.status<>'completed'") + " OR (" + " OR ".join(ranges) + "))")
    chosen = {
        "pending": PENDING,
        "completed": NORMAL_COMPLETED_SQL,
        "ended": ENDED_SQL,
        "rejected": "t.status='cancelled'",
        "all": "true",
    }[tab]
    if overview:
        chosen = {
            "all_pending": PENDING,
            "today_pending": f"{PENDING} AND t.due_at>={TODAY} AND t.due_at<({TODAY})+interval '1 day'",
            "today_completed": f"{NORMAL_COMPLETED_SQL} AND t.completed_at>={TODAY} AND t.completed_at<({TODAY})+interval '1 day'",
        }[overview]
    # Status narrows the list, not the shared overview counts. Keep all owner,
    # permission, business and completed-period predicates in the base scope.
    if status is not None:
        chosen = "(" + chosen + ") AND t.status=" + bind(status)
    return " AND ".join(predicates), args, chosen


class TaskBrowseRepository:
    async def page(self, connection, *, limit=20, offset=0, order="today_first", **filters):
        where, args, chosen = selection(**filters)
        summary = await connection.fetchrow(
            f"""SELECT count(*)::int AS total,
          count(*) FILTER(WHERE {PENDING})::int AS pending_count,
          count(*) FILTER(WHERE {PENDING} AND t.due_at<statement_timestamp())::int AS overdue_count,
          count(*) FILTER(WHERE {PENDING} AND t.due_at>={TODAY}
            AND t.due_at<({TODAY})+interval '1 day')::int AS today_pending_count,
          count(*) FILTER(WHERE {NORMAL_COMPLETED_SQL} AND t.completed_at>={TODAY}
            AND t.completed_at<({TODAY})+interval '1 day')::int AS today_completed_count,
          count(*) FILTER(WHERE {NORMAL_COMPLETED_SQL})::int AS completed_count,
          count(*) FILTER(WHERE {ENDED_SQL})::int AS ended_count,
          count(*) FILTER(WHERE {STOPPED_SQL})::int AS stopped_count,
          count(*) FILTER(WHERE {UNKNOWN_OUTCOME_SQL})::int AS unknown_outcome_count,
          count(*) FILTER(WHERE t.status='in_progress')::int AS in_progress_count,
          count(*) FILTER(WHERE t.status='deferred')::int AS deferred_count,
          count(*) FILTER(WHERE t.status='needs_adjustment')::int AS needs_adjustment_count,
          count(*) FILTER(WHERE t.status='cancelled')::int AS rejected_count,
          count(*) FILTER(WHERE {chosen})::int AS filtered_total
          FROM workflow.task t WHERE {where}""",
            *args,
        )
        sort = {
            "due_asc": "t.due_at ASC NULLS LAST",
            "due_desc": "t.due_at DESC NULLS LAST",
            "created_desc": "t.created_at DESC NULLS LAST",
            "today_first": f"(t.due_at>={TODAY} AND t.due_at<({TODAY})+interval '1 day') DESC NULLS LAST,t.due_at DESC NULLS LAST",
        }[order]
        sort += ",t.created_at DESC NULLS LAST,t.id"
        args.extend((limit + 1, offset))
        # Reuse the task rows already authorized in this statement; a second
        # base-table join can rescan the workspace once per page item under RLS.
        rows = await connection.fetch(
            f"""WITH page AS MATERIALIZED (
          SELECT t.id,t.workspace_id,t.execution_mode,t.task_type,t.title,left(t.description,600) AS description,
            t.customer_id,t.opportunity_id,t.target_position,t.status,t.priority_code,
            t.due_at,t.completed_at,t.created_at,t.updated_at,t.version_no,t.creator_user_ref_id
          FROM workflow.task t WHERE {where} AND ({chosen}) ORDER BY {sort}
          LIMIT ${len(args) - 1} OFFSET ${len(args)})
          SELECT t.id::text,t.execution_mode,t.task_type,t.title,t.description,
            {ENDED_AT_SQL} AS ended_at, {END_EVENT_SQL} AS end_event_type,
            workflow.task_execution_summary(t.id) AS execution,
            {TASK_ALIGNMENT_READ_SQL} AS alignment,
            t.customer_id::text,COALESCE(c.name,security.customer_reference(t.customer_id)->>'name') AS customer_name,
            t.opportunity_id::text,o.name AS opportunity_name,t.target_position,t.status,t.priority_code,
            t.due_at,t.completed_at,t.created_at,t.updated_at,t.version_no,t.creator_user_ref_id::text,
            creator.display_name AS creator_name,owner.owner_name,owner.team_name,
            {TASK_CAN_CANCEL_SQL} AS can_cancel,
            {TASK_HANDOVER_REQUIRED_SQL} AS handover_required,
            (SELECT e.event_type FROM workflow.task_event e WHERE e.task_id=t.id
              ORDER BY e.occurred_at DESC,e.id DESC LIMIT 1) AS last_event_type
          FROM page t
          LEFT JOIN crm.customer c ON c.id=t.customer_id
          LEFT JOIN crm.opportunity o ON o.id=t.opportunity_id
          JOIN platform.user_ref creator ON creator.id=t.creator_user_ref_id
          LEFT JOIN LATERAL (SELECT p.display_name AS owner_name,team.name AS team_name
             FROM workflow.task_assignee a JOIN platform.user_ref p ON p.id=a.assignee_user_ref_id
             LEFT JOIN platform.team team ON team.id=a.assignee_team_id
             WHERE a.task_id=t.id AND a.responsibility='owner' ORDER BY p.id LIMIT 1) owner ON true
          ORDER BY {sort}""",
            *args,
        )
        more = len(rows) > limit
        return {
            "items": [normalize_execution(dict(row)) for row in rows[:limit]],
            "summary": dict(summary),
            "has_more": more,
            "next_offset": offset + limit if more else None,
        }
