"""Acceptance must not hide unrelated, duplicate, external or mutated notifications."""

import copy
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.fixture
def case(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / 'scripts' / 'acceptance'))
    audit = importlib.import_module('today_tasks_notification_audit')
    now = datetime.now(UTC)
    task = dict(id='task', creator_user_ref_id='actor', source_code='today_task_agent',
                status='pending_execution', created_at=now-timedelta(minutes=1), due_at=now+timedelta(hours=2))
    prior = dict(id='prior', created_at=now-timedelta(days=1), title='existing')
    new = dict(id='new', object_id='task', workspace_id='workspace', recipient_user_ref_id='actor',
               channel_code='in_app', template_code='priority_task_due', object_type='task',
               dedupe_key='priority_monitor:task:task', status='pending', sent_at=None,
               delivered_at=None, read_at=None, failure_reason=None, created_at=now,
               payload=dict(agent_code='priority_monitor', task_id='task', due_at=task['due_at'].isoformat()))
    return audit, task, prior, new


def test_delayed_own_reminder_reconstructs_original_snapshot(case):
    audit, task, prior, new = case
    for baseline in ([prior], audit.digest([prior])):
        result = audit.audit_rows([prior, new], baseline, [task], actor='actor', workspace='workspace',
                                  since=new['created_at']-timedelta(seconds=2))
        assert len(result) == 1 and result[0]['task_id'] == 'task'


@pytest.mark.parametrize('change', ['recipient', 'external', 'duplicate', 'foreign_task', 'old_changed',
                                  'delivered', 'not_due', 'wrong_template'])
def test_unexpected_notification_changes_fail(case, change):
    audit, task, prior, new = case
    rows = copy.deepcopy([prior, new])
    if change == 'recipient':
        rows[1]['recipient_user_ref_id'] = 'other'
    elif change == 'external':
        rows[1]['channel_code'] = 'email'
    elif change == 'duplicate':
        rows.append({**new, 'id': 'duplicate'})
    elif change == 'foreign_task':
        rows[1]['object_id'] = 'other'
    elif change == 'old_changed':
        rows[0]['title'] = 'changed'
    elif change == 'delivered':
        rows[1]['delivered_at'] = new['created_at']
    elif change == 'not_due':
        task['due_at'] = new['created_at']+timedelta(days=2)
    elif change == 'wrong_template':
        rows[1]['template_code'] = 'task_assigned'
    with pytest.raises(AssertionError):
        audit.audit_rows(rows, [prior], [task], actor='actor', workspace='workspace')
