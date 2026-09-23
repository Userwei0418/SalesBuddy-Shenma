"""Home counts cover the authorized set while cards remain bounded to eight."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from sales_backend.db import set_request_context
from sales_backend.repositories.fde_home import fde_home
from sales_backend.repositories.fde_dashboard import fde_dashboard
from sales_backend.repositories.tasks import TaskRepository
from sales_backend.services.tasks import TaskService
from tests.integration.test_fde_identity_tasks import fde_fixture, make_task
from tests.integration.test_operations_claims_sql import actor

pytestmark = pytest.mark.asyncio


async def test_fde_home_matches_complete_task_scope_without_materializing_full_tasks(connection, monkeypatch):
    sales, opportunity, people, _ = await fde_fixture(connection)
    created = [await make_task(connection, sales, opportunity) for _ in range(10)]
    await actor(connection, 'ADMIN001')
    await connection.execute("UPDATE workflow.task SET created_at=clock_timestamp()-interval '2 days',"
        " due_at=clock_timestamp()-interval '1 day' WHERE id=$1::uuid", created[2]['id'])
    await connection.execute("UPDATE workflow.task SET due_at=clock_timestamp()+interval '6 hours' "
        "WHERE id=$1::uuid", created[3]['id'])
    first = await actor(connection, people['first']['code'])
    service = TaskService()
    await service.apply_event(connection, actor=first, task_id=created[0]['id'], event_type='accept', note=None)
    submitted = await service.complete(connection, actor=first, task_id=created[0]['id'], note='已完成技术确认')
    await set_request_context(connection, sales)
    await service.apply_event(connection, actor=sales, task_id=created[0]['id'], event_type='approve_completion',
        note=None, expected_version=submitted['version_no'])
    await set_request_context(connection, first)
    await service.apply_event(connection, actor=first, task_id=created[1]['id'], event_type='accept', note=None)

    async def check(person):
        current = await actor(connection, people[person]['code'])
        repo = TaskRepository()
        all_rows = await repo.list(connection, status=None, customer_id=None, limit=None, inbox=True, fde_view='self')
        pending = [r for r in all_rows if r['status'] in {'pending_confirm','pending_execution','in_progress','deferred'}
                   and r['requires_action']]
        completed = [r for r in all_rows if r['status']=='completed' and any(
            p['user_id']==current.user_id and p['role']==current.role.value and p['responsibility']=='owner'
            for p in r['assignees'])]
        now = datetime.now(ZoneInfo('Asia/Shanghai'))
        team = None
        if person == 'lead':
            team_rows = await repo.list(connection, status=None, customer_id=None, limit=None, fde_view='team')
            unfinished = [r for r in team_rows if r['status'] not in {'completed','cancelled'}]
            team = {'overdue':sum(r['due_at']<now for r in unfinished),
                    'claim':sum(bool(r['target_position']) and r['status']=='pending_confirm' for r in unfinished),
                    'handover':sum(bool(r['handover_required']) for r in unfinished)}
        async def forbidden(*args, **kwargs):
            raise AssertionError('Home must not load complete task details')
        with monkeypatch.context() as patch:
            patch.setattr(TaskRepository, 'list', forbidden)
            actual = await fde_home(connection, current)
        assert actual['my_tasks'] == len(pending)
        assert actual['completed_tasks'] == len(completed)
        assert [r['source_id'] for r in actual['task_items']] == [r['id'] for r in pending[:8]]
        assert [r['source_id'] for r in actual['priority_items']] == [
            r['id'] for r in pending if r['due_at'] <= now + timedelta(hours=12)][:8]
        assert actual['team_summary'] == team
        assert actual['open_opportunities'] == (0 if person=='lead' else 1)
        assert len(actual['task_items']) <= 8
        return actual

    assert (await check('first'))['my_tasks'] == 9
    assert (await check('second'))['my_tasks'] == 8
    lead_home = await check('lead')
    assert lead_home['team_summary']['claim'] == 8
    assert lead_home['team_summary']['overdue'] == 1
    lead = await actor(connection, people['lead']['code'])
    board = await fde_dashboard(connection, lead)
    # The dashboard counts owned tasks, while home also counts unclaimed candidates.
    assert board['summary']['completed_tasks'] == 1
    assert board['summary']['pending_tasks'] == 1
    first_row = next(r for r in board['ranking'] if r['user_id']==first.user_id)
    assert first_row['completed_tasks'] == 1 and first_row['pending_tasks'] == 1
    await actor(connection, 'ADMIN001')
    await connection.execute("UPDATE crm.opportunity_participant SET valid_to=clock_timestamp(),end_reason='测试移出' "
        "WHERE opportunity_id=$1::uuid AND user_ref_id=$2::uuid AND valid_to='infinity'",
        opportunity['id'], first.user_id)
    assert (await check('lead'))['team_summary']['handover'] == 0  # Project exit alone keeps task ownership.
    await set_request_context(connection, sales)
