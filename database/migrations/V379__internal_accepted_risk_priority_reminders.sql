-- Derived from internal e40a8029d94a03fc45448e00d536e5a5b9120831, migrations/V231__accepted_risk_priority_reminders.sql.
-- Original SHA-256: e192fab73534c5a54e9577e723de5eac46297ae0215272296bd0ea348476dc65. See shenma_whole_121_manifest.json.
-- Accepted risk suggestions remain unresolved until a human resolves the risk.
-- Preserve pending reminders and create missing eligible reminders for accepted risks.
-- Retain V191 history/dedupe, task logic, owner, SECURITY DEFINER and EXECUTE grants.
-- Cancelled/read history remains occupied and is not revived.
BEGIN;

-- CREATE OR REPLACE retains its
-- historical owner, SECURITY DEFINER boundary, search_path and EXECUTE grants.

CREATE OR REPLACE FUNCTION ops.run_priority_reminder_monitor() RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'workflow', 'insight', 'crm', 'ops'
    AS $$
DECLARE
  task_count integer := 0;
  risk_count integer := 0;
BEGIN
  INSERT INTO workflow.notification (
    workspace_id, recipient_user_ref_id, channel_code, template_code,
    title, body, object_type, object_id, status, dedupe_key,
    scheduled_at, payload
  )
  SELECT t.workspace_id, ta.assignee_user_ref_id, 'in_app',
         'priority_task_due', '待办即将到期',
         t.title || '；截止时间：' || to_char(t.due_at AT TIME ZONE 'Asia/Shanghai', 'MM-DD HH24:MI'),
         'task', t.id, 'pending', 'priority_monitor:task:' || t.id::text,
         clock_timestamp(),
         jsonb_build_object(
           'agent_code', 'priority_monitor',
           'task_id', t.id::text,
           'description', t.description,
           'due_at', t.due_at,
           'hours_remaining', round(extract(epoch FROM (t.due_at - clock_timestamp())) / 3600.0, 1),
           'monitored_at', clock_timestamp()
         )
    FROM workflow.task t
    JOIN workflow.task_assignee ta
      ON ta.task_id = t.id AND ta.responsibility = 'owner'
   WHERE t.deleted_at IS NULL
     AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred')
     AND t.due_at > clock_timestamp()
     AND t.due_at <= clock_timestamp() + interval '12 hours'
     AND NOT EXISTS (
       SELECT 1 FROM workflow.notification existing
        WHERE existing.workspace_id = t.workspace_id
          AND existing.recipient_user_ref_id = ta.assignee_user_ref_id
          AND existing.dedupe_key = 'priority_monitor:task:' || t.id::text
     )
  ON CONFLICT (workspace_id, recipient_user_ref_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL
  DO NOTHING;
  GET DIAGNOSTICS task_count = ROW_COUNT;

  INSERT INTO workflow.notification (
    workspace_id, recipient_user_ref_id, channel_code, template_code,
    title, body, object_type, object_id, status, dedupe_key,
    scheduled_at, payload
  )
  SELECT r.workspace_id, r.owner_user_ref_id, 'in_app',
         'priority_risk',
         CASE r.severity_code
           WHEN 'critical' THEN '严重风险提醒'
           WHEN 'high' THEN '高风险提醒'
           ELSE '中风险提醒'
         END,
         r.title || COALESCE('；' || NULLIF(btrim(r.description), ''), ''),
         'risk', r.id, 'pending', 'priority_monitor:risk:' || r.id::text,
         clock_timestamp(),
         jsonb_build_object(
           'agent_code', 'priority_monitor',
           'risk_id', r.id::text,
           'severity', r.severity_code,
           'customer_name', c.name,
           'description', r.description,
           'due_at', r.due_at,
           'monitored_at', clock_timestamp()
         )
    FROM insight.risk r
    LEFT JOIN crm.customer c ON c.id = r.customer_id
   WHERE r.deleted_at IS NULL
     AND r.owner_user_ref_id IS NOT NULL
     AND r.status IN ('new', 'pending', 'in_progress', 'escalated', 'accepted')
     AND r.severity_code IN ('medium', 'high', 'critical')
     AND NOT EXISTS (
       SELECT 1 FROM workflow.notification existing
        WHERE existing.workspace_id = r.workspace_id
          AND existing.recipient_user_ref_id = r.owner_user_ref_id
          AND existing.dedupe_key = 'priority_monitor:risk:' || r.id::text
     )
  ON CONFLICT (workspace_id, recipient_user_ref_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL
  DO NOTHING;
  GET DIAGNOSTICS risk_count = ROW_COUNT;

  UPDATE workflow.notification n
     SET status = 'cancelled'
   WHERE n.template_code = 'priority_task_due'
     AND n.status NOT IN ('cancelled', 'read')
     AND NOT EXISTS (
       SELECT 1 FROM workflow.task t
        WHERE t.id = n.object_id
          AND t.deleted_at IS NULL
          AND t.status IN ('pending_confirm', 'pending_execution', 'in_progress', 'deferred')
          AND t.due_at > clock_timestamp()
          AND t.due_at <= clock_timestamp() + interval '12 hours'
     );

  UPDATE workflow.notification n
     SET status = 'cancelled'
   WHERE n.template_code = 'priority_risk'
     AND n.status NOT IN ('cancelled', 'read')
     AND NOT EXISTS (
       SELECT 1 FROM insight.risk r
        WHERE r.id = n.object_id
          AND r.deleted_at IS NULL
          AND r.status IN ('new', 'pending', 'in_progress', 'escalated', 'accepted')
          AND r.severity_code IN ('medium', 'high', 'critical')
     );

  RETURN task_count + risk_count;
END;
$$;

INSERT INTO ops.schema_migration (version, description)
VALUES ('V379', 'Keep priority reminders for accepted unresolved risks');

COMMIT;
