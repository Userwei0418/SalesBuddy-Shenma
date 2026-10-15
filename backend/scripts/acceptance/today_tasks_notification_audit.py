"""Read-only audit of existing task reminders; never enqueue or deliver messages."""

import hashlib
import json
from datetime import datetime, timedelta


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def audit_rows(rows, baseline, tasks, *, actor, workspace, since=None):
    """Protect existing rows and admit only the existing monitor's own-task reminder.

    Historic reports contain a hash, so a timestamp bounds candidate new rows;
    removing those candidates must reconstruct that exact original hash.
    """
    tasks = {str(task['id']): task for task in tasks}
    previous_ids = {str(row['id']) for row in baseline} if isinstance(baseline, list) else None
    kept, added, seen = [], [], set()
    for row in rows:
        is_new = (str(row['id']) not in previous_ids if previous_ids is not None
                  else row['created_at'] >= since)
        if not is_new:
            kept.append(row)
            continue
        task_id = str(row['object_id'])
        task = tasks.get(task_id)
        assert task is not None, 'Unexpected notification object'
        payload = row['payload']
        assert str(row['workspace_id']) == workspace and str(row['recipient_user_ref_id']) == actor
        assert row['channel_code'] == 'in_app' and row['template_code'] == 'priority_task_due'
        assert row['object_type'] == 'task' and row['dedupe_key'] == 'priority_monitor:task:' + task_id
        assert row['status'] == 'pending' and all(row[key] is None for key in
                                                ('sent_at', 'delivered_at', 'read_at', 'failure_reason'))
        assert payload.get('agent_code') == 'priority_monitor' and payload.get('task_id') == task_id
        assert str(task['creator_user_ref_id']) == actor and task['source_code'] == 'today_task_agent'
        assert task['status'] == 'pending_execution'
        assert task['created_at'] <= row['created_at'] < task['due_at']
        assert task['due_at'] <= row['created_at'] + timedelta(hours=12)
        assert datetime.fromisoformat(payload['due_at']) == task['due_at']
        assert task_id not in seen, 'Duplicate task reminder'
        seen.add(task_id)
        added.append({'id': str(row['id']), 'task_id': task_id,
                      'channel': 'in_app', 'template': 'priority_task_due',
                      'recipient_user_id': actor, 'created_at': row['created_at'].isoformat()})
    expected = digest(baseline) if isinstance(baseline, list) else baseline
    assert digest(kept) == expected, 'Existing notification rows changed or unexpected rows appeared'
    return added


async def audit_notifications(connection, baseline, tasks, *, actor, workspace, since=None):
    rows = [dict(row) for row in await connection.fetch('SELECT * FROM workflow.notification ORDER BY id')]
    added = audit_rows(rows, baseline, tasks, actor=actor, workspace=workspace, since=since)
    assert not await connection.fetchval(
        'SELECT count(*) FROM workflow.notification_delivery WHERE notification_id=ANY($1::uuid[])',
        [row['id'] for row in added],
    ), 'Unexpected notification delivery attempt'
    return added
