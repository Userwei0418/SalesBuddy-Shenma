"""Terminal reasons are read projections, not rewritten lifecycle states."""
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sales_backend.contracts.work_report_base import ResolvedPeriod
from sales_backend.domain.task_outcome import task_end_reason
from sales_backend.domain.work_report_facts import FactAssembler
from sales_backend.domain.work_report_task_activity import completion_times
from sales_backend.repositories.task_execution_projection import normalize_execution
from sales_backend.services.task_execution import _normalize_execution

NOW = datetime(2026, 10, 26, 8, tzinfo=UTC)
PERIOD = ResolvedPeriod(kind='day', start=NOW-timedelta(hours=8), end_exclusive=NOW+timedelta(hours=16), cutoff=NOW, label='今日')


@pytest.mark.parametrize('execution,expected', [
    ({'completion_action': 'close'}, 'stopped'),
    ({'completion_detail': {'action': 'submit_completion'}}, 'completed'),
    (json.dumps({'completion_result': {'action': 'review_completion'}}), 'completed'),
    ('{}', 'unknown'), ('bad json', 'unknown'), (None, 'unknown'), ([], 'unknown'),
    ({'completion_action': 'future_action'}, 'unknown'),
    ({'completion_action': ['close']}, 'unknown'), ({'completion_action': {'action':'close'}}, 'unknown'),
])
def test_v3_evidence_not_status_alone_decides_success(execution, expected):
    assert task_end_reason(dict(status='completed', execution_mode='execution_v3', execution=execution)) == expected


@pytest.mark.parametrize('mode', ['legacy','execution_v1','execution_v2'])
def test_old_completed_status_stays_success(mode):
    assert task_end_reason(dict(status='completed', execution_mode=mode)) == 'completed'


@pytest.mark.parametrize('event,expected', [('cancel','cancelled'),('cancel_task','cancelled'),('reject','rejected'),('decline_task','rejected'),(None,'unknown')])
def test_cancelled_history_uses_terminal_transition_not_later_comment(event, expected):
    row = dict(status='cancelled', end_event_type=event, last_event_type='comment')
    assert task_end_reason(row) == expected
    assert normalize_execution(row)['end_reason'] == expected


def task(action, completed_at=NOW-timedelta(minutes=1)):
    return dict(id=str(uuid4()), version_no=1, status='completed', execution_mode='execution_v3',
        completed_at=completed_at, execution=json.dumps({'completion_result': {'action':action, 'note':'合成结束说明'}}), contributors=[])


def test_report_stop_retains_reason_without_count_or_fake_missing_data():
    assembler = FactAssembler(period=PERIOD, data_as_of=NOW)
    rows = [task('close'), task('submit_completion'), task('review_completion')]
    assembler.add('task', rows)
    facts = assembler.finish()
    metric = next(m for m in facts.metrics if m.metric_id == 'tasks_completed')
    assert metric.value == '2' and metric.complete
    assert len(facts.sources) == 3 and any('已停止' in s.text and '合成结束说明' in s.text for s in facts.sources)
    assert all(row['status']=='completed' for row in rows)


def test_report_unknown_action_and_missing_time_count_once_each_and_visit_missing_stays_unknown():
    assembler = FactAssembler(period=PERIOD, data_as_of=NOW)
    assembler.add('task', [task('submit_completion', None), task('future_action')])
    assembler.add('visit', [dict(id=str(uuid4()),version_no=1,interaction_at=None,contributors=[])])
    facts = assembler.finish()
    assert next(m for m in facts.metrics if m.metric_id=='tasks_completed').value is None
    assert any('2条已结束' in gap for c in facts.coverage for gap in c.missing_reasons)
    assert next(m for m in facts.metrics if m.metric_id=='visits_completed').value is None


def test_close_event_is_not_completion_and_live_command_has_reason():
    row = task('close') | dict(completion_events=[dict(to_status='completed',from_status='in_progress',event_type='close',occurred_at=NOW)])
    assert completion_times(row, NOW) == []
    result = _normalize_execution(dict(status='completed',execution_mode='execution_v3',completion_action='close',completion_detail={'occurred_at':NOW.isoformat()}))
    assert result['end_reason']=='stopped' and result['ended_at']==NOW.isoformat()


def test_cancelled_history_never_enters_report_completed_list():
    row = dict(status='cancelled',completed_at=None,completion_events=[dict(to_status='completed',from_status='in_progress',event_type='complete',occurred_at=NOW)])
    assert completion_times(row,NOW)==[]
