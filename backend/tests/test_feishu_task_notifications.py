"""Task creation cards use recipient groups and cannot outlive the captured task facts."""
from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest

from sales_backend.domain.feishu_sync.cards import notification_card
from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS, SyncConfig
from sales_backend.domain.feishu_sync.planning import notification_plans
from sales_backend.domain.feishu_sync.projection import project
from sales_backend.integrations.feishu import FeishuError
from sales_backend.services.feishu_sync import task_notification_snapshot
from tests.test_feishu_sync_policy import D1, D2, payload
from tests.test_feishu_sync_policy import event as source_event
from tests.test_feishu_sync_service import Connection, setup


def task_config(**notification):
    data = payload()
    data['mappings'] = {'task': {'enabled': True, 'table_id': 'tblTasks', 'id_field_id': 'fldSystemId',
                                'fields': {'title': 'fldName', 'record_status': 'fldStatus'}}}
    data['notification'].update(on_create=['task'], routing_mode='department_routes', routes=[
        {'department_id': str(D1), 'chat_ids': ['oc_recipient', 'oc_shared']},
        {'department_id': str(D2), 'chat_ids': ['oc_other', 'oc_shared']},
    ])
    data['notification'].update(notification)
    return SyncConfig.model_validate(data)


def task_raw(event, **changes):
    return {'id': str(event['object_id']), 'version_no': 1, 'title': '核对方案', 'description': '与同事核对完整方案',
            'status': 'pending_confirm', 'due_at': '2026-09-29T10:00:00Z', 'source_code': 'meeting_import',
            'owner_id': str(UUID(int=80)), 'owner_name': '接收人', 'creator_user_ref_id': str(UUID(int=81)),
            'creator_team_id': str(UUID(int=99)), 'original_dispatcher_name': '下发人',
            'batch_id': str(UUID(int=82)), 'batch_name': '本周周会', 'target_position': None,
            'notification_recipient_ids': [str(UUID(int=80))], 'notification_recipient_names': ['接收人'],
            'notification_department_ids': [str(D1)], **changes}


def task_setup(**notification):
    service, event, client = setup()
    event.update(object_kind='task')
    config = task_config(**notification)
    service.client = lambda row: (config, client)
    service.repository.source.return_value = task_raw(event)
    stored = {}

    async def freeze(connection, plan, workspace, card):
        stored.setdefault(plan.dedupe_key, {'event_id': plan.event_id, 'status': 'pending',
            'chat_id': plan.chat_id, 'payload': deepcopy(card), 'dedupe_key': plan.dedupe_key})

    async def deliveries(connection, event_id):
        return [value for value in stored.values() if value['event_id'] == event_id]

    async def state(connection, key, status, **kwargs):
        stored[key].update(status=status, **kwargs)

    service.repository.freeze_delivery.side_effect = freeze
    service.repository.deliveries.side_effect = deliveries
    service.repository.delivery_state.side_effect = state
    return service, event, client, config, stored


def test_task_is_supported_only_after_explicit_selection_and_never_for_updates():
    config = SyncConfig.model_validate(payload())
    assert config.notification.on_create == {'customer', 'opportunity', 'visit'}
    task = source_event(object_kind='task', department_ids=(D1, D2, D1))
    enabled = task_config()
    assert [p.chat_id for p in notification_plans(enabled, task)] == ['oc_other', 'oc_recipient', 'oc_shared']
    assert not notification_plans(enabled, replace(task, first_formal_create=False))
    assert not notification_plans(enabled, replace(task, historical=True))
    assert not notification_plans(task_config(on_create=[]), task)
    with pytest.raises(PermissionError):
        notification_plans(enabled, replace(task, workspace_id=UUID(int=999)))


def test_task_card_shows_actual_task_people_batch_beijing_due_and_plain_text():
    service, event, _, config, _ = task_setup()
    raw = task_raw(event, description='<at id=all>不是群体提醒</at> [链接](https://example.invalid)')
    values = project('task', raw, SOURCE_FIELDS['task'] | COMMON_FIELDS)
    card = notification_card('task', values, config, 'recTask')
    assert card['header']['title']['content'] == '新增任务'
    texts = [item['text']['content'] for item in card['elements'] if item['tag'] == 'div']
    assert {'任务：核对方案', '任务内容：' + raw['description'], '负责人：接收人', '下发人：下发人',
            '截止时间：2026-09-29 18:00（北京时间）', '当前状态：待接收', '任务来源：周会导入',
            '来源批次：本周周会'} <= set(texts)
    assert all(item['text']['tag'] == 'plain_text' for item in card['elements'] if item['tag'] == 'div')
    assert card['elements'][-1]['actions'][0]['url'] == 'https://example.feishu.cn/base/testBase?table=tblTasks&record=recTask'
    assert service.repository.source.await_count == 0


@pytest.mark.asyncio
async def test_task_routed_to_recipients_groups_deduped_and_frozen_not_creators_group():
    service, event, client, _, stored = task_setup()
    service.repository.source.return_value = task_raw(event, notification_department_ids=[str(D1), str(D2), str(D1)])
    await service.handle(Connection(), event, {})
    assert {call.args[0] for call in client.send_card.await_args_list} == {'oc_recipient', 'oc_other', 'oc_shared'}
    assert client.send_card.await_count == 3 and all(row['status'] == 'sent' for row in stored.values())
    assert all(row['payload']['source']['notification_department_ids'] == [str(D1), str(D2)] for row in stored.values())
    assert all('schema_version' not in call.args[1] for call in client.send_card.await_args_list)
    await service.handle(Connection(), event, {})
    repeated = {**event, 'id': UUID(int=901)}
    await service.handle(Connection(), repeated, {})
    assert client.send_card.await_count == 3  # Same task/group identity, across retries and repeated creation events.


@pytest.mark.asyncio
async def test_position_task_shows_candidates_and_routes_all_eligible_departments():
    service, event, client, _, stored = task_setup()
    service.repository.source.return_value = task_raw(event,
        owner_id=None, owner_name=None, target_position='sales_manager',
        notification_recipient_ids=[str(UUID(int=81)), str(UUID(int=80))],
        notification_recipient_names=['候选乙', '候选甲'], notification_department_ids=[str(D1), str(D2)])
    await service.handle(Connection(), event, {})
    assert len(stored) == 3
    text = str(client.send_card.await_args.args[1])
    assert '待领取（可接收人：候选甲、候选乙）' in text


@pytest.mark.asyncio
async def test_task_with_no_department_uses_existing_default_route():
    service, event, client, _, _ = task_setup()
    service.repository.source.return_value = task_raw(event, notification_department_ids=[])
    await service.handle(Connection(), event, {})
    assert client.send_card.await_count == 1 and client.send_card.await_args.args[0] == 'oc_default'


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'status': 'cancelled'}, {'status': 'completed'}, {'status': 'pending_review'}, {'status': 'pending_handover'},
    {'deleted_at': '2026-09-28T12:00:00Z'}, {'excluded': True}, {'data_kind': 'synthetic'},
    {'notification_recipient_ids': [], 'notification_recipient_names': []},
    {'notification_recipient_ids': [str(UUID(int=90))]}, {'notification_department_ids': ['not-a-uuid']},
    {'source_code': 'automatic_unconfirmed'}, {'source_code': 'mini_program'}, {'source_code': 'business_advice'},
    {'batch_id': None}, {'batch_id': ''}, {'batch_id': 'not-a-confirmed-batch'}, {'version_no': None},
])
async def test_task_ineligible_before_processing_never_gets_a_new_notification(change):
    service, event, client, _, stored = task_setup()
    service.repository.source.return_value = task_raw(event, **change)
    await service.handle(Connection(), event, {})
    assert not stored
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    {'status': 'cancelled'}, {'status': 'completed'}, {'status': 'pending_execution'}, {'version_no': 2},
    {'description': '已经改成另一个任务'}, {'due_at': '2026-10-01T10:00:00Z'},
    {'notification_department_ids': [str(D2)]}, {'notification_recipient_names': ['改名后的接收人']},
    {'owner_id': str(UUID(int=90)), 'notification_recipient_ids': [str(UUID(int=90))]},
    {'excluded': True}, {'deleted': True},
])
async def test_quick_change_after_planning_suppresses_frozen_task_card(change):
    service, event, client, _, stored = task_setup()
    original = task_raw(event)
    service.repository.source.side_effect = [original, {**original, **change}, {**original, **change}]
    await service.handle(Connection(), event, {})
    assert len(stored) == 2
    assert all(row['status'] == 'failed' and row['error'] == 'SOURCE_CHANGED_BEFORE_NOTIFICATION'
               for row in stored.values())
    client.send_card.assert_not_awaited()


@pytest.mark.asyncio
async def test_definitive_send_failure_retries_same_frozen_card_once_and_unknown_never_resends():
    service, event, client, _, stored = task_setup(routing_mode='single_group')
    client.send_card.side_effect = [FeishuError('TEMPORARY', retryable=True), 'om_retry']
    with pytest.raises(FeishuError, match='TEMPORARY'):
        await service.handle(Connection(), event, {})
    assert next(iter(stored.values()))['status'] == 'pending'
    await service.handle(Connection(), event, {})
    first, second = client.send_card.await_args_list
    assert first.args == second.args and next(iter(stored.values()))['status'] == 'sent'
    await service.handle(Connection(), event, {})
    assert client.send_card.await_count == 2
    next(iter(stored.values()))['status'] = 'unknown'
    with pytest.raises(FeishuError, match='MESSAGE_RESULT_UNKNOWN'):
        await service.handle(Connection(), event, {})
    assert client.send_card.await_count == 2


@pytest.mark.asyncio
async def test_source_becomes_ineligible_on_retry_closes_pending_but_preserves_sent_unknown_receipts():
    service, event, client, _, stored = task_setup()
    client.send_card.side_effect = FeishuError('TEMPORARY', retryable=True)
    with pytest.raises(FeishuError):
        await service.handle(Connection(), event, {})
    first = next(iter(stored.values()))
    stored['unknown'] = {**first, 'dedupe_key': 'unknown', 'status': 'unknown'}
    stored['sent'] = {**first, 'dedupe_key': 'sent', 'status': 'sent'}
    service.repository.source.return_value = task_raw(event, status='cancelled')
    await service.handle(Connection(), event, {})
    assert first['status'] == 'failed' and first['error'] == 'SOURCE_NO_LONGER_ELIGIBLE'
    assert stored['unknown']['status'] == 'unknown' and stored['sent']['status'] == 'sent'
    assert client.send_card.await_count == 1


@pytest.mark.parametrize('change', [
    {'notification_recipient_ids': 'id'}, {'notification_recipient_names': []},
    {'notification_department_ids': 'department'}, {'notification_recipient_names': [None]},
    {'notification_recipient_ids': [str(UUID(int=80)), str(UUID(int=80))],
     'notification_recipient_names': ['甲', '甲']},
    {'id': str(UUID(int=999))}, {'version_no': True}, {'owner_id': None, 'target_position': None},
])
def test_task_snapshot_fails_closed_on_missing_or_malformed_identity(change):
    _, event, _, _, _ = task_setup()
    assert task_notification_snapshot(event, task_raw(event, **change)) is None
