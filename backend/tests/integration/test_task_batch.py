"""Atomic multi-recipient assignment with real runtime RLS, workflow and advice decisions."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from sales_backend.domain.advice import AdviceRequest
from sales_backend.services.advice import AdviceHandler, AdviceService
from sales_backend.services.agent_platform.inference import InferenceService
from tests.integration.test_authorization_opportunities import grant
from tests.integration.test_business_advice import database
from tests.integration.test_business_rankings import opportunity
from tests.integration.test_operations_api import client_for
from tests.integration.test_operations_claims_sql import actor
from tests.integration.test_profile_scores import business_login

pytestmark = pytest.mark.asyncio


def batch(*accounts, **links):
    common = dict(description='请独立核对试点资料并提交结果', association_kind='daily',
                  due_at=(datetime.now(UTC)+timedelta(days=2)).isoformat(), **links)
    return [dict(**common, assignee_account_code=account) for account in accounts]


async def test_batch_replay_and_independent_receipt_completion(connection):
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'XS001')
        body = {'tasks': batch('XS001', 'XS002')}
        headers = {'Idempotency-Key': str(uuid4())}
        result = await client.post('/api/v1/tasks/batch', json=body, headers=headers)
        assert result.status_code == 201, result.text
        items = result.json()['items']
        assert len({item['id'] for item in items}) == 2
        assert (await client.post('/api/v1/tasks/batch', json=body, headers=headers)).json() == result.json()
        assert await connection.fetchval('SELECT count(*) FROM workflow.task WHERE description=$1', body['tasks'][0]['description']) == 2
        other_id = items[1]['id']
        # The creator can inspect both; the recipients respond to their own task only.
        await business_login(client, 'XS002')
        assert (await client.get('/api/v1/tasks/'+items[0]['id'])).status_code == 404
        accepted = await client.post(f'/api/v1/tasks/{other_id}/events', json={'event_type': 'accept'})
        assert accepted.status_code == 200, accepted.text
        done = await client.post(f'/api/v1/tasks/{other_id}/events', json={'event_type': 'complete', 'note': '已独立核对并交付'})
        assert done.status_code == 200, done.text
        await business_login(client, 'XS001')
        assert (await client.get('/api/v1/tasks/'+items[0]['id'])).json()['status'] == 'pending_confirm'
        assert (await client.get('/api/v1/tasks/'+other_id)).json()['status'] == 'pending_review'
        # Notifications are recipient-private: verify each inbox under its own identity.
        for account, item in zip(('XS001','XS002'), items):
            await actor(connection, account)
            assert await connection.fetchval("SELECT count(*) FROM workflow.notification WHERE object_id=$1::uuid AND template_code='task_assigned'", item['id']) == 1


async def test_invalid_later_recipient_rolls_back_tasks_notifications_and_retries(connection):
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'XS001')
        before = await connection.fetchval('SELECT count(*) FROM workflow.notification')
        tasks = batch('XS001', 'ACCOUNT_OUTSIDE_COMPANY')
        result = await client.post('/api/v1/tasks/batch', json={'tasks': tasks})
        assert result.status_code == 403, result.text
        assert await connection.fetchval('SELECT count(*) FROM workflow.task WHERE description=$1', tasks[0]['description']) == 0
        assert await connection.fetchval('SELECT count(*) FROM workflow.notification') == before
        tasks[1]['assignee_account_code'] = 'XS002'
        assert (await client.post('/api/v1/tasks/batch', json={'tasks': tasks})).status_code == 201


async def test_batch_requires_assign_and_the_actual_task_kind_permission(connection):
    who = await actor(connection, 'XS001')
    admin = await actor(connection, 'ADMIN001')
    await grant(connection, admin, who.user_id, [dict(permission='task.assign', effect='deny')])
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'XS001')
        response = await client.post('/api/v1/tasks/batch', json={'tasks': batch('XS001', 'XS002')})
        assert response.status_code == 403, response.text
        assert await connection.fetchval('SELECT count(*) FROM workflow.task') == 0
        await grant(connection, admin, who.user_id, [dict(permission='task.create_daily', effect='deny')], version=1)
        assert (await client.post('/api/v1/tasks/batch', json={'tasks': batch('XS001')})).status_code == 403
        await grant(connection, admin, who.user_id, [], version=2)
        assert (await client.post('/api/v1/tasks/batch', json={'tasks': batch('XS001')})).status_code == 201


@pytest.mark.parametrize('linked', [False, True])
async def test_ai_multiselect_atomic_once_statistics_and_visible_task_links(connection, monkeypatch, linked):
    from sales_backend.repositories.visits import VisitRepository
    from sales_backend.services.agent_platform.audit import InferenceAudit
    from sales_backend.services.agent_platform.inference import InferenceResult
    from tests.test_business_advice import output
    operation_id = str(uuid4())
    async def audited(self, **kwargs):
        audit = InferenceAudit(service.database, who, operation_id, mode='opportunity_advice' if linked else 'visit_advice', run_id=None, facts={}, config={})
        await audit.start()
        await audit.finish('accepted', trace={'operation_id':operation_id, 'provider':'controlled_test_output'})
        return InferenceResult(kwargs['validate'](output()), {'operation_id':operation_id, 'provider':'controlled_test_output'})
    monkeypatch.setattr(InferenceService, 'evaluate', audited)
    op = await opportunity(connection, 'XS001', 100)
    who = await actor(connection, 'XS001')
    if linked:
        req = AdviceRequest(subject_kind='opportunity', subject_id=op['id'])
    else:
        visit = await VisitRepository().create(connection, who, customer_id=op['customer_id'], fields={
            'follow_up_record': '客户需要补充试点范围，等待销售整理资料', 'next_action': '下周确认试点范围',
            '_follow_up_quality_score': 85, 'interaction_at': '2026-09-21', 'created_date': '2026-09-21', 'contact_name': '隔离联系人'})
        req = AdviceRequest(subject_kind='visit', subject_id=visit['id'], section='tasks')
    service = AdviceService(database(connection))
    first = await service.request(who, req)
    await AdviceHandler(service.database).handle(first['id'], who)
    suggestion = (await service.get(who, first['id']))['suggestions'][0]
    tasks = batch('XS001', 'XS002')
    if linked:
        tasks = [{**task, 'association_kind': 'customer', 'customer_id': op['customer_id'], 'opportunity_id': op['id']} for task in tasks]
    body = dict(decision='adopted', version_no=suggestion['version_no'], tasks=tasks)
    url = '/api/v1/advice/suggestions/'+suggestion['id']+'/decision'
    async with await client_for(connection, auth_mode='demo') as client:
        await business_login(client, 'XS001')
        invalid = {**body, 'tasks': [tasks[0], {**tasks[1], 'assignee_account_code': 'OUTSIDE_COMPANY'}]}
        assert (await client.post(url, json=invalid)).status_code == 403
        assert await connection.fetchval('SELECT count(*) FROM workflow.task WHERE source_suggestion_id=$1::uuid', suggestion['id']) == 0
        assert (await client.get('/api/v1/advice/'+first['id'])).json()['suggestions'][0]['decision'] == 'pending'
        headers = {'Idempotency-Key': str(uuid4())}
        result = await client.post(url, json=body, headers=headers)
        assert result.status_code == 200, result.text
        saved = result.json()
        assert len(saved['tasks']) == 2 and saved['task'] == saved['tasks'][0]
        assert all(t['source_suggestion_id']==suggestion['id'] for t in saved['tasks'])
        assert (await client.post(url, json=body, headers=headers)).json() == saved
        assert (await client.post(url, json=body)).status_code == 409
        assert (await client.get('/api/v1/advice/statistics')).json()['adopted'] == 1
        detail = (await client.get('/api/v1/advice/'+first['id'])).json()['suggestions'][0]
        assert {t['id'] for t in detail['tasks']} == {t['id'] for t in saved['tasks']}
        assert {t['assignee_name'] for t in detail['tasks']} == {'XS001', 'XS002'}
        assert {t['assignee_account'] for t in detail['tasks']} == {'XS001', 'XS002'}
        await actor(connection, 'ADMIN001')
        event = await connection.fetchrow("SELECT payload FROM security.business_activity_rows($1,$2) WHERE event_id=$3",
            datetime.now(UTC)-timedelta(days=1),datetime.now(UTC)+timedelta(days=1),'advice:'+suggestion['id'])
        assert event and set(event['payload']['task_ids']) == {t['id'] for t in saved['tasks']}
        audit = await connection.fetchval("SELECT record FROM security.agent_operation_rows($1,$2) WHERE operation_id=$3::uuid",
            datetime.now(UTC)-timedelta(days=1),datetime.now(UTC)+timedelta(days=1),operation_id)
        assert {t['id'] for t in audit['business_effects']['created_tasks']} == {t['id'] for t in saved['tasks']}
        assert len(audit['business_effects']['suggestions']) == 1
        await business_login(client, 'XS002')
        # Receiving a task does not grant the source advice or unrelated CRM access.
        assert (await client.get('/api/v1/advice/'+first['id'])).status_code == 404
