# ruff: noqa: S608 -- SQL fragments and casts are server-owned; all input values are bound.
"""Full-scope management counts and bounded tasks under the existing task RLS."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sales_backend.repositories.task_execution_projection import TASK_RESPONSIBILITY_SQL
from sales_backend.repositories.task_outcomes import NORMAL_COMPLETED_SQL, ENDED_SQL, STOPPED_SQL, UNKNOWN_OUTCOME_SQL
from sales_backend.repositories.tasks import TaskRepository

BEIJING = ZoneInfo('Asia/Shanghai')
ACTIVE = "t.status NOT IN ('completed','cancelled')"


def management_selection(*, as_of, scope='all', tab='all', status=None, overdue=False, member_id=None, team_id=None,
                         unassigned=False, date_from=None, date_to=None, batch_id=None, undated='include'):
    args = [as_of]

    def bind(value, cast):
        args.append(value)
        return f'${len(args)}::{cast}'

    predicates = ['true']
    if scope == 'mine':
        predicates.append("(" + TASK_RESPONSIBILITY_SQL + " OR " + """t.creator_user_ref_id=common.current_user_ref_id() OR
          EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id
            AND a.assignee_user_ref_id=common.current_user_ref_id()) OR
          EXISTS(SELECT 1 FROM workflow.task_candidate c WHERE c.task_id=t.id
            AND c.user_ref_id=common.current_user_ref_id()))""")
    list_predicates = ['true']
    if tab == "pending":
        list_predicates.append(ACTIVE)
    elif tab == "completed":
        list_predicates.append(NORMAL_COMPLETED_SQL)
    elif tab == "ended":
        list_predicates.append(ENDED_SQL)
    if status:
        list_predicates.append('t.status=' + bind(status, 'text'))
    if overdue:
        predicates.append(f'{ACTIVE} AND t.due_at<$1::timestamptz')
    if member_id or team_id:
        owner_filters = ["a.responsibility='owner'"]
        if member_id:
            owner_filters.append("a.assignee_user_ref_id=" + bind(member_id, 'uuid'))
        if team_id:
            owner_filters.append("a.assignee_team_id=" + bind(team_id, 'uuid'))
        predicates.append("EXISTS(SELECT 1 FROM workflow.task_assignee a WHERE a.task_id=t.id AND "
                          + " AND ".join(owner_filters) + ")")
    if unassigned:
        predicates.append('t.owner_id IS NULL')
    if batch_id:
        predicates.append('t.batch_id=' + bind(batch_id, 'uuid'))
    if undated == 'only':
        predicates.append('t.due_at IS NULL')
    elif undated == 'exclude':
        predicates.append('t.due_at IS NOT NULL')
    # A date window intersects the disclosed creation/dispatch-to-due interval,
    # never accepted_at (acceptance is not actual work commencement). Inverted
    # legacy intervals remain visible and explicitly flagged, not rewritten.
    dates = []
    if date_from:
        dates.append('GREATEST(t.schedule_start_at,t.due_at)>=' +
                     bind(datetime.combine(date_from, time(), BEIJING), 'timestamptz'))
    if date_to:
        dates.append('LEAST(t.schedule_start_at,t.due_at)<' +
                     bind(datetime.combine(date_to + timedelta(days=1), time(), BEIJING), 'timestamptz'))
    if dates and undated != 'only':
        clause = '(' + ' AND '.join(dates) + ')'
        predicates.append('(t.due_at IS NULL OR ' + clause + ')' if undated == 'include' else clause)
    # The safe summary exposes only confirmed batch headers and task IDs already
    # readable by this identity. Raw drafts/source text remain creator-only.
    batch_filter = bind(batch_id, 'uuid') if batch_id else 'NULL::uuid'
    cte = f"""WITH batches AS MATERIALIZED (
      SELECT b FROM jsonb_array_elements(security.task_import_batch_summary({batch_filter})) b
    ), batch_tasks AS (
      SELECT (b->>'id')::uuid batch_id,b->>'batch_name' batch_name,(b->>'meeting_date')::date meeting_date,
        (b->>'confirmed_at')::timestamptz dispatched_at,task_id::uuid
      FROM batches CROSS JOIN LATERAL jsonb_array_elements_text(b->'task_ids') task_id
    ), visible AS MATERIALIZED (
      SELECT t.*,a.assignee_user_ref_id owner_id,b.batch_id,b.batch_name,b.meeting_date,b.dispatched_at,
        COALESCE(b.dispatched_at,t.created_at) schedule_start_at,
        CASE WHEN b.dispatched_at IS NULL THEN 'created' ELSE 'dispatched' END start_kind
      FROM workflow.task t
      LEFT JOIN LATERAL (SELECT assignee_user_ref_id FROM workflow.task_assignee a
        WHERE a.task_id=t.id AND a.responsibility='owner' ORDER BY assignee_user_ref_id LIMIT 1) a ON true
      LEFT JOIN batch_tasks b ON b.task_id=t.id AND t.source_code='meeting_import'
      WHERE t.deleted_at IS NULL AND $1::timestamptz IS NOT NULL
    ), scoped AS MATERIALIZED (SELECT * FROM visible t WHERE {' AND '.join(predicates)}),
    filtered AS MATERIALIZED (SELECT * FROM scoped t WHERE {' AND '.join(list_predicates)})"""
    return cte, args


class TaskManagementRepository:
    async def page(self, connection, *, limit=50, offset=0, order='due_asc', **filters):
        as_of = datetime.now(UTC)
        cte, args = management_selection(as_of=as_of, **filters)
        # Same semantics as /tasks, with the page's single as_of snapshot so
        # crossing midnight cannot reorder the bounded result between reads.
        today = "date_trunc('day',timezone('Asia/Shanghai',$1::timestamptz)) AT TIME ZONE 'Asia/Shanghai'"
        sort = {
            'due_asc': 't.due_at ASC NULLS LAST',
            'due_desc': 't.due_at DESC NULLS LAST',
            'created_desc': 't.created_at DESC NULLS LAST',
            'today_first': f"(t.due_at>=({today}) AND t.due_at<({today})+interval '1 day') DESC NULLS LAST,t.due_at DESC NULLS LAST",
        }[order] + ',t.created_at DESC NULLS LAST,t.id'
        # Status tabs narrow the list only. Cards and tab counts share the
        # authorized business scope, so selecting one status cannot zero others.
        summary = await connection.fetchrow(cte + f""" SELECT count(*)::int scope_total,
          (SELECT count(*)::int FROM filtered) AS filtered_total,
          count(*) FILTER(WHERE {ACTIVE})::int pending_count,
          count(*) FILTER(WHERE t.status='pending_review' OR (t.status='needs_adjustment' AND EXISTS(SELECT 1 FROM workflow.task_execution x WHERE x.task_id=t.id AND x.workspace_id=t.workspace_id AND x.state->>'processing_reason'='awaiting_review')))::int pending_review_count,
          count(*) FILTER(WHERE {ACTIVE} AND t.due_at<$1::timestamptz)::int overdue_count,
          count(*) FILTER(WHERE {NORMAL_COMPLETED_SQL})::int completed_count,
          count(*) FILTER(WHERE {ENDED_SQL})::int ended_count,
          count(*) FILTER(WHERE {STOPPED_SQL})::int stopped_count,
          count(*) FILTER(WHERE {UNKNOWN_OUTCOME_SQL})::int unknown_outcome_count,
          count(*) FILTER(WHERE t.status='in_progress')::int AS in_progress_count,
          count(*) FILTER(WHERE t.status='deferred')::int AS deferred_count,
          count(*) FILTER(WHERE t.status='needs_adjustment')::int AS needs_adjustment_count,
          count(*) FILTER(WHERE t.status='cancelled')::int cancelled_count,
          count(*) FILTER(WHERE t.due_at IS NULL)::int undated_count,
          (SELECT count(DISTINCT a.assignee_user_ref_id)::int FROM scoped f
            JOIN workflow.task_assignee a ON a.task_id=f.id AND a.responsibility='owner') AS team_member_count
          FROM scoped t""", *args)
        summary = dict(summary)
        # Preserve the existing total contract for older management clients.
        summary['total'] = summary['filtered_total']
        team_member_count = summary.pop('team_member_count')
        args += [limit + 1, offset]
        page = cte + f""", page AS MATERIALIZED (
          SELECT t.id,t.execution_mode FROM filtered t ORDER BY {sort}
          LIMIT ${len(args)-1} OFFSET ${len(args)}), execution_page AS MATERIALIZED (
          SELECT id,CASE WHEN execution_mode IN ('execution_v1','execution_v2','execution_v3') THEN workflow.task_execution_summary(id)
            ELSE NULL END AS execution FROM page) """
        # Keep existing detail/action semantics; expensive recipients and actions
        # are evaluated only for this bounded page, not the complete result set.
        select = TaskRepository._SELECT.replace('t.description,', 'left(t.description,600) AS description,')
        # Execution and requires_action must use one authorized projection in the
        # same snapshot. The old SELECT recalculated the expensive summary twice.
        select = select.replace('workflow.task_execution_summary(t.id)', 'ep.execution')
        # normalize_execution already fences these five legacy actions for V1.
        # Do not evaluate grants only to discard them; cancel keeps its real check.
        for permission in ('accept', 'decline', 'complete', 'review', 'coordinate'):
            call = f"security.authorization_task('task.{permission}',t.id)"
            select = select.replace(call, f"CASE WHEN t.execution_mode='legacy' THEN {call} ELSE false END")
        select = select.replace('t.created_at, t.updated_at,', '''t.created_at, t.updated_at,
          fm.owner_id::text,fm.batch_id::text,fm.batch_name,fm.meeting_date,fm.dispatched_at,
          fm.schedule_start_at,fm.start_kind,COALESCE(t.due_at<fm.schedule_start_at,false) AS schedule_invalid,
          (SELECT u.status FROM platform.user_ref u WHERE u.id=fm.owner_id) AS owner_account_status,
          CASE WHEN fm.owner_id IS NULL THEN NULL ELSE EXISTS(SELECT 1 FROM platform.user_ref u
            WHERE u.id=fm.owner_id AND u.status='active' AND u.deleted_at IS NULL) END AS owner_active,
          COALESCE((SELECT jsonb_agg(jsonb_build_object('id',u.id::text,'name',u.display_name,
            'account_code',u.account_code,'active',u.status='active' AND u.deleted_at IS NULL,'account_status',u.status)
            ORDER BY u.id) FROM workflow.task_assignee oa JOIN platform.user_ref u ON u.id=oa.assignee_user_ref_id
            WHERE oa.task_id=t.id AND oa.responsibility='owner'),'[]'::jsonb) AS owners,''')
        select = select.replace('FROM workflow.task t',
            'FROM page JOIN workflow.task t ON t.id=page.id '
            'JOIN filtered fm ON fm.id=t.id JOIN execution_page ep ON ep.id=t.id')
        rows = await connection.fetch(page + select + f"""
          GROUP BY t.id,
            fm.owner_id,fm.batch_id,fm.batch_name,fm.meeting_date,fm.dispatched_at,fm.schedule_start_at,fm.start_kind,
            ep.execution,c.name,creator.display_name,creator_team.name
          ORDER BY {sort}""", *args)
        items = [TaskRepository._normalize(dict(row)) for row in rows[:limit]]
        for item in items:
            item['owner_count'] = len(item['owners'])
            # Match the primary stable-ID projection, not JSON aggregate order.
            item['owner_name'] = next((o['name'] for o in item['owners'] if o['id'] == item['owner_id']), None)
        more = len(rows) > limit
        return dict(items=items, summary=summary, team_member_count=team_member_count, has_more=more,
                    next_offset=offset + limit if more else None, as_of=as_of)

    async def owners(self, connection, *, query='', limit=50, offset=0, **filters):
        as_of = datetime.now(UTC)
        cte, args = management_selection(as_of=as_of, **filters)
        unassigned = await connection.fetchval(cte + ' SELECT count(*)::int FROM filtered WHERE owner_id IS NULL', *args)
        args += [query, limit + 1, offset]
        # IDs come from visible tasks. No active-account filter: historical owners
        # stay discoverable, and permission to create tasks is not required.
        rows = await connection.fetch(cte + f""" SELECT u.id::text,u.display_name AS name,u.account_code,
          u.status AS account_status,(u.status='active' AND u.deleted_at IS NULL) AS active,count(*)::int task_count
          FROM filtered t JOIN workflow.task_assignee oa ON oa.task_id=t.id AND oa.responsibility='owner'
          JOIN platform.user_ref u ON u.id=oa.assignee_user_ref_id
          WHERE ${len(args)-2}::text='' OR strpos(lower(u.display_name),lower(${len(args)-2}))>0
            OR strpos(lower(u.account_code),lower(${len(args)-2}))>0
          GROUP BY u.id ORDER BY u.display_name,u.id
          LIMIT ${len(args)-1} OFFSET ${len(args)}""", *args)
        more = len(rows) > limit
        return dict(items=[dict(row) for row in rows[:limit]], unassigned_count=unassigned,
                    has_more=more, next_offset=offset + limit if more else None, as_of=as_of)
