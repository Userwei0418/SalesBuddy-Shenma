"""Fixed SQL projections under caller RLS; no lifecycle or authorization changes."""

ACTION_SQL = """(SELECT x.state->'completion_result'->>'action' FROM workflow.task_execution x
  WHERE x.task_id=t.id AND x.workspace_id=t.workspace_id)"""
NORMAL_COMPLETED_SQL = """(t.status='completed' AND (t.execution_mode IS DISTINCT FROM 'execution_v3'
  OR COALESCE(""" + ACTION_SQL + """ IN ('submit_completion','review_completion'),false)))"""
STOPPED_SQL = "(t.status='completed' AND t.execution_mode='execution_v3' AND " + ACTION_SQL + "='close')"
ENDED_SQL = "t.status IN ('completed','cancelled')"
END_EVENT_SQL = """(SELECT e.event_type FROM workflow.task_event e
  WHERE e.task_id=t.id AND e.workspace_id=t.workspace_id AND e.to_status='cancelled'
    AND e.from_status IS DISTINCT FROM 'cancelled'
  ORDER BY e.occurred_at DESC,e.id DESC LIMIT 1)"""
ENDED_AT_SQL = """CASE WHEN t.status='completed' THEN t.completed_at WHEN t.status='cancelled' THEN
  (SELECT max(e.occurred_at) FROM workflow.task_event e WHERE e.task_id=t.id
    AND e.workspace_id=t.workspace_id AND e.to_status='cancelled'
    AND e.from_status IS DISTINCT FROM 'cancelled') END"""


def alias(sql, name):
    # Callers provide fixed internal SQL aliases only, never request values.
    assert name in {"t", "wt"}
    return sql.replace("t.", name + ".")

UNKNOWN_OUTCOME_SQL = "((t.status='completed' AND NOT " + NORMAL_COMPLETED_SQL + " AND NOT COALESCE(" + STOPPED_SQL + ",false)) OR (t.status='cancelled' AND COALESCE(" + END_EVENT_SQL + " NOT IN ('cancel','cancel_task','reject','decline','decline_task'),true)))"
