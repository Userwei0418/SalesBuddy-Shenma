"""Current-authority task activity; called only in the overview read transaction."""

from sales_backend.db import json_value
from sales_backend.domain.work_report import WorkReportError
from sales_backend.domain.work_report_task_activity import assemble_task_activity, assemble_task_activity_v2

TASK_ACTIVITY_SQL = """/* work_report_task_activity_v1 */
SELECT t.id::text,t.version_no,t.title,t.status,t.created_at,t.completed_at,t.updated_at,t.execution_mode,t.completion_note,t.due_at,
 jsonb_build_object('processing_reason',x.state->'processing_reason','processing_detail',x.state->'processing_detail',
  'pause',x.state->'pause','latest_feedback',x.state->'latest_feedback','next_step',x.state->'next_step',
  'completion_result',CASE WHEN jsonb_typeof(x.state->'completion_result')='object'
    THEN x.state->'completion_result'->'note' ELSE x.state->'completion_result' END,
  'completion_action',x.state->'completion_result'->'action','completion_detail',x.state->'completion_result') AS execution,
 (SELECT jsonb_build_object('text',e.note,'actor_user_id',e.actor_user_ref_id,'actor_name',u.display_name,
     'occurred_at',e.occurred_at,'event_id',e.id)
  FROM workflow.task_event e LEFT JOIN platform.user_ref u ON u.id=e.actor_user_ref_id AND u.workspace_id=e.workspace_id
  WHERE e.task_id=t.id AND e.workspace_id=t.workspace_id AND NULLIF(btrim(e.note),'') IS NOT NULL
  ORDER BY e.occurred_at DESC,e.id DESC LIMIT 1) AS latest_feedback,
 owners.people AS owners, completion.events AS completion_events,
 CASE WHEN visible.customer THEN c.id::text END AS customer_id,
 CASE WHEN visible.customer THEN c.name END AS customer_name,
 CASE WHEN visible.opportunity THEN o.id::text END AS opportunity_id,
 CASE WHEN visible.opportunity THEN o.name END AS opportunity_name
FROM workflow.task t
LEFT JOIN workflow.task_execution x ON x.task_id=t.id AND x.workspace_id=t.workspace_id
LEFT JOIN crm.customer c ON c.workspace_id=t.workspace_id AND c.id=t.customer_id
LEFT JOIN crm.opportunity o ON o.workspace_id=t.workspace_id AND o.id=t.opportunity_id
CROSS JOIN LATERAL (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('member_id',person.id,'name',person.name)
                          ORDER BY person.id),'[]'::jsonb) AS people
 FROM (SELECT DISTINCT a.assignee_user_ref_id::text AS id,u.display_name AS name
  FROM workflow.task_assignee a LEFT JOIN platform.user_ref u
   ON u.workspace_id=a.workspace_id AND u.id=a.assignee_user_ref_id
  WHERE a.workspace_id=t.workspace_id AND a.task_id=t.id AND a.responsibility='owner'
   AND security.authorization_subject('work_report.read','person',a.assignee_user_ref_id,NULL)
   AND (($1='team' AND a.assignee_team_id=$3::uuid)
    OR ($1 IN ('self','member') AND a.assignee_user_ref_id=ANY($2::uuid[])))) person
) owners
CROSS JOIN LATERAL (
 SELECT COALESCE(jsonb_agg(jsonb_build_object('id',e.id::text,'occurred_at',e.occurred_at,
                  'from_status',e.from_status,'to_status',e.to_status,'event_type',e.event_type)
                  ORDER BY e.occurred_at,e.id),'[]'::jsonb) AS events,
        bool_or(e.occurred_at>=$4 AND e.occurred_at<$5) AS completed_in_period
 FROM workflow.task_event e WHERE e.workspace_id=t.workspace_id AND e.task_id=t.id
  AND e.to_status='completed' AND e.from_status IS DISTINCT FROM 'completed' AND e.occurred_at<=$6
) completion
CROSS JOIN LATERAL (
 SELECT (c.id IS NOT NULL AND c.deleted_at IS NULL
         AND security.authorization_customer('customer.read',c.id)
         AND security.authorization_customer('work_report.read',c.id)) AS customer,
        (o.id IS NOT NULL AND o.deleted_at IS NULL
         AND security.authorization_opportunity('opportunity.read',o.id)
         AND security.authorization_opportunity('work_report.read',o.id)) AS opportunity
) visible
WHERE t.workspace_id=common.current_workspace_id() AND t.deleted_at IS NULL AND t.created_at<=$6
 AND security.authorization_task('task.read',t.id) AND security.authorization_task('work_report.read',t.id)
 AND jsonb_array_length(owners.people)>0
 AND (t.status NOT IN ('completed','cancelled') OR (t.created_at>=$4 AND t.created_at<$5)
  OR (t.status='completed' AND t.completed_at>=$4 AND t.completed_at<$5)
  OR completion.completed_in_period
  OR (t.status='completed' AND t.completed_at IS NULL AND jsonb_array_length(completion.events)=0))
ORDER BY t.id
"""


async def read_task_activities(connection, actor, *, period, scope, data_as_of):
    check = await connection.fetchrow(
        "SELECT current_setting('transaction_isolation') AS isolation,"
        "current_setting('row_security') AS rls,common.current_workspace_id()::text AS workspace_id,"
        "common.current_user_ref_id()::text AS user_id"
    )
    if not check or dict(check) != dict(
        isolation="repeatable read", rls="on", workspace_id=actor.workspace_id, user_id=actor.user_id
    ):
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "任务概览需要当前用户的可重复读事务", status=403)
    if await connection.fetchval("SELECT current_setting('transaction_read_only')") != "on":
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "任务概览必须在只读事务中取数", status=403)
    rows = await connection.fetch(
        TASK_ACTIVITY_SQL,
        scope.kind,
        [str(i) for i in scope.member_ids],
        str(scope.team_id) if scope.team_id else None,
        period.start,
        period.cutoff,
        data_as_of,
    )
    material = [
        {**dict(row), "owners": json_value(row["owners"]), "completion_events": json_value(row["completion_events"]),
            "execution": json_value(row.get("execution")), "latest_feedback": json_value(row.get("latest_feedback"))}
        for row in rows
    ]
    args = dict(period=period, scope=scope, data_as_of=data_as_of)
    return assemble_task_activity(material, **args), assemble_task_activity_v2(material, **args)


async def read_task_activity(connection, actor, *, period, scope, data_as_of):
    legacy, _ = await read_task_activities(connection, actor, period=period, scope=scope, data_as_of=data_as_of)
    return legacy
