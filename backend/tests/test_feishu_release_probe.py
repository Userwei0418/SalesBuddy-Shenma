"""Offline failure-injection tests; no Feishu or database connection is made."""
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.integrations.feishu import FeishuError
from sales_backend.maintenance.feishu_release_probe import (
    KINDS,
    REQUIRED,
    LiveConnection,
    ProbeJournal,
    ProbePlan,
    ProbeRejected,
    ReleaseProbe,
    config_fingerprint,
    normalized,
    prepare,
    raw_fixture,
)
from tests.test_feishu_sync_policy import payload


class Transport:
    def __init__(self, config, schemas):
        self.config, self.schemas = config, schemas
        self.app_id = config.app_id
        self.rows, self.messages, self.writes = {}, {}, []
        self.searches = []
        self.failure, self.changed_schema = None, False

    async def fields(self, base, table):
        return deepcopy(self.schemas[table])

    @staticmethod
    def table_path(base, table):
        return f'/bitable/v1/apps/{base}/tables/{table}'

    async def find_record(self, base, table, field, system_id):
        matches = [(rid, row) for (target, rid), row in self.rows.items()
                   if target == table and row.get(field) == system_id]
        if len(matches) > 1:
            raise FeishuError('DUPLICATE_SYSTEM_ID')
        return matches[0][0] if matches else None

    async def request(self, method, path, **kwargs):
        if method == 'POST' and path.endswith('/records/search'):
            assert kwargs['params'] == {'page_size': 2}
            body = kwargs['body']
            assert set(body) == {'field_names', 'filter'}
            assert body['filter']['conjunction'] == 'and'
            condition, = body['filter']['conditions']
            assert condition['operator'] == 'is' and len(condition['value']) == 1
            assert condition['field_name'] in body['field_names']
            self.searches.append(deepcopy(body))
            table = path.split('/')[-3]
            rows = [{'record_id': rid, 'fields': {key: deepcopy(value) for key, value in row.items()
                                                 if key in body['field_names']}}
                    for (target, rid), row in self.rows.items()
                    if target == table and row.get(condition['field_name']) == condition['value'][0]]
            definitions = {item['field_name']: item for item in self.schemas[table].values()}
            for row in rows:
                for name, value in row['fields'].items():
                    if definitions[name]['type'] in {18, 21}:
                        row['fields'][name] = {'link_record_ids': value} if value else {}
            return {'items': rows[:2], 'has_more': len(rows) > 2}
        assert method == 'GET', 'No general-purpose write bypass permitted'
        if path.startswith('/im/v1/chats/'):
            return {}
        if path.startswith('/im/v1/messages/'):
            return {'items': [deepcopy(self.messages[path.rsplit('/', 1)[-1]])]}
        # Fine-grained record search is allowed; broad single-record GET is not.
        raise FeishuError('FEISHU_99991672')

    async def create_record(self, base, table, fields, token):
        assert token.version == 4
        self.writes.append(('create', table, deepcopy(fields), str(token)))
        if self.failure == 'create_unknown_absent':
            self.failure = None
            raise FeishuError('TRANSPORT_UNKNOWN', unknown=True)
        rid = 'rec' + str(len(self.rows) + 1)
        self.rows[table, rid] = deepcopy(fields)
        if self.failure == 'create_unknown_committed':
            self.failure = None
            raise FeishuError('TRANSPORT_UNKNOWN', unknown=True)
        if self.failure == 'create_bad_readback':
            self.failure = None
            self.rows[table, rid]['系统ID'] = 'changed'
        return rid

    async def update_record(self, base, table, rid, fields):
        self.writes.append(('update', table, deepcopy(fields)))
        self.rows[table, rid].update(deepcopy(fields))
        if self.failure == 'update_unknown_committed':
            self.failure = None
            raise FeishuError('TRANSPORT_UNKNOWN', unknown=True)

    async def send_card(self, chat, card, token):
        self.writes.append(('card', chat, deepcopy(card), token))
        if self.failure == 'message_unknown':
            self.failure = None
            raise FeishuError('TRANSPORT_UNKNOWN', unknown=True)
        mid = 'om_probe' + str(len(self.messages) + 1)
        self.messages[mid] = {'message_id': mid, 'chat_id': chat, 'msg_type': 'interactive'}
        return mid


def configuration():
    data = payload()
    data['mappings'] = {}
    schemas = {}
    for kind in KINDS:
        sources = REQUIRED[kind] | {'owner_id'} if kind == 'task' else REQUIRED[kind]
        mapping = {'enabled': True, 'table_id': 'tbl' + kind,
                   'id_field_id': 'fldId',
                   'fields': {source: 'fld' + str(i) for i, source in enumerate(sorted(sources))}}
        definitions = {'fldId': {'field_name': '系统ID', 'type': 1}}
        for source, target in mapping['fields'].items():
            definition = {'field_name': source, 'type': 2 if source.endswith('_count') or source == 'amount' else 1}
            if source == 'expected_close_date':
                definition['type'] = 5
            if source in {'customer_id', 'opportunity_id', 'owner_id'}:
                parent = {'customer_id': 'customer', 'opportunity_id': 'opportunity', 'owner_id': 'member'}[source]
                definition.update(type=18, property={'table_id': 'tbl' + parent})
            definitions[target] = definition
        data['mappings'][kind], schemas[mapping['table_id']] = mapping, definitions
    data['mappings']['member'] = {'enabled': True, 'table_id': 'tblmember',
                                 'id_field_id': 'fldId', 'fields': {'record_status': 'fldStatus'}}
    return SyncConfig.model_validate(data), schemas


async def setup(tmp_path):
    directory = (tmp_path / 'private').resolve()
    directory.mkdir(mode=0o700)
    journal = ProbeJournal(directory)
    config, schemas = configuration()
    client = Transport(config, schemas)
    loader = AsyncMock(return_value=LiveConnection(config, 'credential-revision'))
    plan = await prepare(journal, loader, client, config.workspace_id, config.connection_id)
    return journal, plan, loader, client


def probe(journal, plan, loader, client):
    return ReleaseProbe(journal, plan, journal.read('STATE.json'), loader, client)


@pytest.mark.parametrize('field_type', [18, 21])
@pytest.mark.parametrize(('value', 'expected'), [
    ({}, None), ({'link_record_ids': []}, None), ([], None), (None, None),
    ({'link_record_ids': ['recB', 'recA']}, ['recA', 'recB']),
    (['recB', 'recA'], ['recA', 'recB']),
    ([{'record_id': 'recA', 'text': 'legacy display'}], ['recA']),
])
def test_link_search_and_legacy_readback_normalization(field_type, value, expected):
    assert normalized(value, field_type) == expected


@pytest.mark.parametrize('field_type', [18, 21])
@pytest.mark.parametrize('value', [
    {'link_record_ids': None}, {'link_record_ids': 'recA'},
    {'link_record_ids': [{'record_id': 'recA'}]},
    {'link_record_ids': ['recA'], 'unreviewed': True}, {'record_id': 'recA'},
    {'link_record_ids': ['wrong']}, {'link_record_ids': ['recA/path']},
    {'link_record_ids': [1]}, ['wrong'], [{}], [None], 'recA', 1,
])
def test_link_readback_rejects_unknown_shapes_and_invalid_ids(field_type, value):
    with pytest.raises(ProbeRejected, match='UNRECOGNIZED_LINK_READBACK'):
        normalized(value, field_type)


@pytest.mark.asyncio
async def test_search_link_envelope_unknown_adoption_does_not_repeat_write(tmp_path, monkeypatch):
    import sales_backend.maintenance.feishu_release_probe as module
    journal, plan, loader, client = await setup(tmp_path)
    await probe(journal, plan, loader, client).write_record('customer', 'initial')
    original_plan = (journal.directory / 'PLAN.json').read_bytes()
    fixed_normalized = module.normalized
    def old_normalized(value, field_type):
        return value if field_type in {18, 21} and isinstance(value, dict) else fixed_normalized(value, field_type)
    # Reproduce the real post-create mismatch: search represents links with
    # envelopes, but the old reader compares them to the submitted lists.
    monkeypatch.setattr(module, 'normalized', old_normalized)
    with pytest.raises(ProbeRejected, match='WRITE_READBACK_MISMATCH'):
        await probe(journal, plan, loader, client).write_record('opportunity', 'initial')
    assert journal.read('STATE.json')['steps']['opportunity:initial']['status'] == 'unknown'
    before = deepcopy(client.writes)
    monkeypatch.setattr(module, 'normalized', fixed_normalized)
    await probe(journal, plan, loader, client).write_record('opportunity', 'initial')
    state = journal.read('STATE.json')
    assert state['steps']['opportunity:initial']['status'] == 'verified'
    assert client.writes == before
    assert client.rows['tblopportunity', state['records']['opportunity']['record_id']]['customer_id'] == [
        state['records']['customer']['record_id']]
    assert (journal.directory / 'PLAN.json').read_bytes() == original_plan


@pytest.mark.parametrize('links', [['recOtherCustomer'], []])
@pytest.mark.asyncio
async def test_unknown_opportunity_rejects_changed_or_removed_customer_link(tmp_path, links):
    journal, plan, loader, client = await setup(tmp_path)
    await probe(journal, plan, loader, client).write_record('customer', 'initial')
    client.failure = 'create_unknown_committed'
    with pytest.raises(FeishuError):
        await probe(journal, plan, loader, client).write_record('opportunity', 'initial')
    rid, = [rid for table, rid in client.rows if table == 'tblopportunity']
    client.rows['tblopportunity', rid]['customer_id'] = links
    before = deepcopy(client.writes)
    with pytest.raises(ProbeRejected, match='RECORD_RESULT_UNKNOWN'):
        await probe(journal, plan, loader, client).write_record('opportunity', 'initial')
    assert client.writes == before
    assert journal.read('STATE.json')['steps']['opportunity:initial']['status'] == 'unknown'


def test_prepared_config_survives_independent_hash_seed_processes(tmp_path):
    # Reproduce the pre-v3 false drift while exercising an actual persisted
    # prepare/guard boundary in separate interpreters, not reordered mock lists.
    source = '''
import asyncio, json, sys
from pathlib import Path
from unittest.mock import AsyncMock
from sales_backend.maintenance.feishu_release_probe import (
    LiveConnection, ProbeJournal, ProbePlan, config_fingerprint, fingerprint,
)
from tests.test_feishu_release_probe import configuration, probe, setup, Transport

async def check():
    if sys.argv[2] == 'prepare':
        journal, plan, loader, client = await setup(Path(sys.argv[1]))
    else:
        journal = ProbeJournal(Path(sys.argv[1]) / 'private')
        plan = ProbePlan.model_validate(journal.read('PLAN.json'))
        config, schemas = configuration()
        loader = AsyncMock(return_value=LiveConnection(config, 'credential-revision'))
        client = Transport(config, schemas)
    await probe(journal, plan, loader, client).guard('customer')
    config = loader.return_value.config
    assert not client.writes
    assert journal.read('STATE.json')['status'] == 'prepared'
    print(json.dumps({'version': plan.version, 'canonical': config_fingerprint(config),
                      'legacy': fingerprint(config.model_dump(mode='json'))}))

asyncio.run(check())
'''
    backend = Path(__file__).resolve().parents[1]
    results = []
    for seed in ('1', '2', '3'):
        result = subprocess.run(
            [sys.executable, '-c', source, str(tmp_path.resolve()), 'prepare' if seed == '1' else 'guard'],
            check=True, capture_output=True, text=True, timeout=30, cwd=backend.parent,
            env={**os.environ, 'PYTHONHASHSEED': seed,
                 'PYTHONPATH': os.pathsep.join((str(backend / 'src'), str(backend)))},
        )
        results.append(json.loads(result.stdout))
    assert {result['version'] for result in results} == {3}
    assert len({result['canonical'] for result in results}) == 1
    assert len({result['legacy'] for result in results}) > 1


def test_config_fingerprint_preserves_route_and_chat_order():
    config, _ = configuration()
    data = config.model_dump(mode='json')
    data['notification']['routes'] = [
        {'department_id': str(UUID(int=101)), 'chat_ids': ['oc_first', 'oc_second']},
        {'department_id': str(UUID(int=102)), 'chat_ids': ['oc_third']},
    ]
    original = config_fingerprint(SyncConfig.model_validate(data))
    reordered_routes = deepcopy(data)
    reordered_routes['notification']['routes'].reverse()
    reordered_chats = deepcopy(data)
    reordered_chats['notification']['routes'][0]['chat_ids'].reverse()
    assert config_fingerprint(SyncConfig.model_validate(reordered_routes)) != original
    assert config_fingerprint(SyncConfig.model_validate(reordered_chats)) != original


@pytest.mark.asyncio
async def test_complete_probe_keeps_amount_and_task_measures_zero_with_two_cards(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    assert not client.writes
    with journal.lock():
        await probe(journal, plan, loader, client).run()
    state = journal.read('STATE.json')
    assert state['status'] == 'passed_and_retired'
    assert len([w for w in client.writes if w[0] == 'create']) == 3
    cards = [w for w in client.writes if w[0] == 'card']
    assert len(cards) == 2 and {w[1] for w in cards} == {plan.chat_id}
    assert len({w[-1] for w in cards}) == 2
    assert all(w[2]['header']['title']['content'].startswith('【上线验收】') for w in cards)
    task_writes = [w[2] for w in client.writes if w[0] in {'create', 'update'} and w[1] == 'tbltask']
    assert len(task_writes) == 2
    assert task_writes[0]['status'] == 'cancelled'
    assert task_writes[0]['verification_status'] == 'not_required'
    for measure in ('active_count', 'completed_count', 'pending_review_count', 'ontime_completed_count'):
        assert [r[measure] for r in task_writes] == [0, 0]
    opportunity_writes = [w[2] for w in client.writes
                          if w[0] in {'create', 'update'} and w[1] == 'tblopportunity']
    assert [r['amount'] for r in opportunity_writes if 'amount' in r] == [0, 0]
    # The mapped date genuinely changes, while retired rows retain a zero amount.
    assert opportunity_writes[1]['expected_close_date'] - opportunity_writes[0]['expected_close_date'] == (
        7 * 24 * 60 * 60 * 1000)
    assert client.rows['tblopportunity', state['records']['opportunity']['record_id']]['amount'] == 0
    update_text = '\n'.join(element.get('text', {}).get('content', '') for element in cards[1][2]['elements'])
    before = raw_fixture(plan, 'opportunity', 'initial')['expected_close_date']
    after = raw_fixture(plan, 'opportunity', 'updated')['expected_close_date']
    assert f'预计关单日期\n修改前：{before}\n修改后：{after}' in update_text
    assert '金额' not in update_text
    assert all(row['record_status'] == '已归档' for row in client.rows.values())
    task = client.rows['tbltask', state['records']['task']['record_id']]
    assert task['owner_id'] == []
    assert task['customer_id'] == [state['records']['customer']['record_id']]
    assert task['opportunity_id'] == [state['records']['opportunity']['record_id']]
    # Repeat run does not recreate or resend anything.
    before = deepcopy(client.writes)
    with journal.lock():
        await probe(journal, plan, loader, client).run()
    assert client.writes == before
    for name in ('PLAN.json', 'STATE.json', 'probe.lock'):
        assert (journal.directory / name).stat().st_mode & 0o077 == 0


@pytest.mark.asyncio
async def test_committed_unknown_create_is_recovered_by_id_without_second_create(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    client.failure = 'create_unknown_committed'
    with pytest.raises(FeishuError):
        await probe(journal, plan, loader, client).run()
    assert journal.read('STATE.json')['steps']['customer:initial']['status'] == 'unknown'
    await probe(journal, plan, loader, client).run()
    assert len([w for w in client.writes if w[:2] == ('create', 'tblcustomer')]) == 1
    assert journal.read('STATE.json')['status'] == 'passed_and_retired'


@pytest.mark.asyncio
async def test_missing_single_record_scope_recovers_existing_v3_unknown_without_write(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    original_plan = (journal.directory / 'PLAN.json').read_bytes()
    p = probe(journal, plan, loader, client)

    async def legacy_record(config, kind, schema):
        mapping = config.mappings[kind]
        rid = await client.find_record(config.base_token, mapping.table_id,
            schema[mapping.id_field_id]['field_name'], str(plan.objects[kind]))
        if not rid:
            return None, None
        return await client.request('GET', client.table_path(config.base_token, mapping.table_id)
                                    + '/records/' + rid)

    # Reproduce the old sequence: create commits, then unauthorized GET leaves
    # the original v3 checkpoint unknown despite a valid remote row.
    p.record = legacy_record
    with pytest.raises(FeishuError, match='FEISHU_99991672'):
        await p.write_record('customer', 'initial')
    before_state = journal.read('STATE.json')
    assert before_state['steps']['customer:initial']['status'] == 'unknown'
    assert before_state['records'] == {}
    before_writes = deepcopy(client.writes)
    assert len(before_writes) == 1 and before_writes[0][0] == 'create'

    await probe(journal, plan, loader, client).write_record('customer', 'initial')
    after_state = journal.read('STATE.json')
    assert client.writes == before_writes  # No create, update, card or new UUID.
    assert (journal.directory / 'PLAN.json').read_bytes() == original_plan
    assert after_state['plan_hash'] == before_state['plan_hash'] == plan.digest
    assert after_state['steps']['customer:initial'] == {
        **before_state['steps']['customer:initial'], 'status': 'verified'}
    assert after_state['records']['customer']['record_id'] == 'rec1'
    assert set(client.searches[-1]['field_names']) == {'系统ID', *REQUIRED['customer']}
    assert client.searches[-1]['filter']['conditions'][0]['value'] == [str(plan.objects['customer'])]


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['duplicate', 'has_more', 'wrong_id', 'missing_id',
                                    'invalid_record_id', 'replaced_record'])
async def test_search_readback_retains_identity_and_duplicate_guards(tmp_path, invalid):
    journal, plan, loader, client = await setup(tmp_path)
    p = probe(journal, plan, loader, client)
    config, schema = await p.guard('customer')
    row = {'record_id': 'recCandidate', 'fields': {'系统ID': str(plan.objects['customer'])}}
    response = {'items': [row]}
    expected = 'REMOTE_IDENTITY_MISMATCH'
    if invalid == 'duplicate':
        response['items'].append(deepcopy(row))
        expected = 'DUPLICATE_SYSTEM_ID'
    elif invalid == 'has_more':
        response['has_more'] = True
        expected = 'DUPLICATE_SYSTEM_ID'
    elif invalid == 'wrong_id':
        row['fields']['系统ID'] = str(UUID(int=99))
    elif invalid == 'missing_id':
        row['fields'] = {}
    elif invalid == 'invalid_record_id':
        row['record_id'] = '../foreign'
        expected = 'INVALID_RECORD_RECEIPT'
    else:
        p.state['records']['customer'] = {'record_id': 'recOriginal', 'fields': {}}
        expected = 'REMOTE_RECORD_REPLACED'
    client.request = AsyncMock(return_value=response)
    with pytest.raises((FeishuError, ProbeRejected), match=expected):
        await p.record(config, 'customer', schema)
    assert not client.writes


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['edit', 'missing_field'])
async def test_unknown_search_readback_requires_every_frozen_field_before_recovery(tmp_path, change):
    journal, plan, loader, client = await setup(tmp_path)
    client.failure = 'create_unknown_committed'
    with pytest.raises(FeishuError):
        await probe(journal, plan, loader, client).write_record('customer', 'initial')
    before_state = (journal.directory / 'STATE.json').read_bytes()
    before_writes = deepcopy(client.writes)
    if change == 'edit':
        client.rows['tblcustomer', 'rec1']['name'] = 'Human edited this'
    else:
        del client.rows['tblcustomer', 'rec1']['name']
    with pytest.raises(ProbeRejected, match='RECORD_RESULT_UNKNOWN'):
        await probe(journal, plan, loader, client).write_record('customer', 'initial')
    assert client.writes == before_writes
    assert (journal.directory / 'STATE.json').read_bytes() == before_state


@pytest.mark.asyncio
async def test_absent_unknown_create_never_replayed_or_claimed_clean(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    client.failure = 'create_unknown_absent'
    with pytest.raises(FeishuError):
        await probe(journal, plan, loader, client).run()
    with pytest.raises(ProbeRejected, match='RECORD_RESULT_UNKNOWN'):
        await probe(journal, plan, loader, client).run()
    with pytest.raises(ProbeRejected, match='RETIREMENT_INCOMPLETE'):
        await probe(journal, plan, loader, client).retire()
    assert len(client.writes) == 1
    assert journal.read('STATE.json')['status'] == 'retirement_incomplete'


@pytest.mark.asyncio
async def test_unknown_message_never_resent_but_cleanup_remains_available(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    client.failure = 'message_unknown'
    with pytest.raises(FeishuError):
        await probe(journal, plan, loader, client).run()
    with pytest.raises(ProbeRejected, match='MESSAGE_RESULT_UNKNOWN'):
        await probe(journal, plan, loader, client).run()
    await probe(journal, plan, loader, client).retire()
    assert len([w for w in client.writes if w[0] == 'card']) == 1
    assert journal.read('STATE.json')['steps']['card:create']['status'] == 'unknown'
    assert journal.read('STATE.json')['status'] == 'retired'
    assert all(row['record_status'] == '已归档' for row in client.rows.values())
    with pytest.raises(ProbeRejected, match='RETIRED_RUN_CANNOT_RESTART'):
        await probe(journal, plan, loader, client).run()


@pytest.mark.asyncio
async def test_unknown_update_can_be_retired_after_matching_readback(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    p = probe(journal, plan, loader, client)
    for kind in KINDS:
        await p.write_record(kind, 'initial')
    client.failure = 'update_unknown_committed'
    with pytest.raises(FeishuError):
        await p.write_record('opportunity', 'updated')
    await probe(journal, plan, loader, client).retire()
    assert journal.read('STATE.json')['steps']['opportunity:updated']['status'] == 'verified'
    assert journal.read('STATE.json')['status'] == 'retired'
    assert len([w for w in client.writes if w[0] == 'card']) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [
    'revision', 'credential', 'credential_ref', 'disabled', 'workspace', 'schema', 'app_identity',
    'mapping', 'notification',
])
async def test_frozen_configuration_changes_stop_before_writes(tmp_path, change):
    journal, plan, loader, client = await setup(tmp_path)
    config = loader.return_value.config
    if change == 'credential':
        loader.return_value = LiveConnection(config, 'new-key-revision')
    elif change == 'app_identity':
        client.app_id = 'cli_wrongapp'
    elif change == 'schema':
        client.schemas['tblcustomer']['fldId']['field_name'] = 'renamed'
    elif change in {'mapping', 'notification', 'credential_ref'}:
        data = config.model_dump(mode='json')
        if change == 'mapping':
            data['mappings']['customer']['fields']['name'] = 'fldChanged'
        elif change == 'notification':
            data['notification']['on_create'] = ['opportunity']
        else:
            data['credential_ref'] = str(UUID(int=99))
        loader.return_value = LiveConnection(SyncConfig.model_validate(data), 'credential-revision')
    else:
        updates = {'revision': config.revision + 1} if change == 'revision' else (
            {'enabled': False} if change == 'disabled' else {'workspace_id': UUID(int=88)})
        loader.return_value = LiveConnection(config.model_copy(update=updates), 'credential-revision')
    with pytest.raises(ProbeRejected):
        await probe(journal, plan, loader, client).run()
    assert not client.writes


@pytest.mark.asyncio
async def test_missing_measure_mapping_fails_preflight_without_creating_plan(tmp_path):
    directory = (tmp_path / 'private').resolve()
    directory.mkdir(mode=0o700)
    journal = ProbeJournal(directory)
    config, schemas = configuration()
    data = config.model_dump(mode='json')
    del data['mappings']['task']['fields']['active_count']
    config = SyncConfig.model_validate(data)
    client = Transport(config, schemas)
    with pytest.raises(ProbeRejected, match='REQUIRED_MAPPING_MISSING'):
        await prepare(journal, AsyncMock(return_value=LiveConnection(config, 'rev')), client,
                      config.workspace_id, config.connection_id)
    assert not client.writes and not (directory / 'PLAN.json').exists()


@pytest.mark.asyncio
async def test_missing_close_date_mapping_fails_before_any_external_write(tmp_path):
    directory = (tmp_path / 'private').resolve()
    directory.mkdir(mode=0o700)
    journal = ProbeJournal(directory)
    config, schemas = configuration()
    data = config.model_dump(mode='json')
    del data['mappings']['opportunity']['fields']['expected_close_date']
    config = SyncConfig.model_validate(data)
    client = Transport(config, schemas)
    with pytest.raises(ProbeRejected, match='REQUIRED_MAPPING_MISSING'):
        await prepare(journal, AsyncMock(return_value=LiveConnection(config, 'rev')), client,
                      config.workspace_id, config.connection_id)
    assert not client.writes and not (directory / 'PLAN.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('old_version', [1, 2])
async def test_old_probe_plan_cannot_be_reinterpreted_under_new_contract(tmp_path, monkeypatch, old_version):
    journal, plan, loader, client = await setup(tmp_path)
    assert plan.version == 3
    old_plan = journal.read('PLAN.json')
    old_plan['version'] = old_version
    with pytest.raises(ValidationError):
        ProbePlan.model_validate(old_plan)
    with pytest.raises(ProbeRejected, match='WRITE_OUTSIDE_PLAN'):
        await probe(journal, plan, loader, client).write_record('task', 'completed')
    assert not client.writes
    journal.write('PLAN.json', old_plan)
    path = Path(__file__).parents[1] / 'scripts/acceptance/feishu_release_probe.py'
    spec = spec_from_file_location('feishu_release_probe_old_plan_cli', path)
    cli = module_from_spec(spec)
    spec.loader.exec_module(cli)
    connect = AsyncMock()
    monkeypatch.setattr(cli.asyncpg, 'connect', connect)
    for action in ('run', 'retire'):
        args = SimpleNamespace(action=action, directory=str(journal.directory), plan_sha256=plan.digest)
        with pytest.raises(ValidationError):
            await cli.execute(args)
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_remote_identity_collision_or_edit_is_not_overwritten(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    p = probe(journal, plan, loader, client)
    client.rows['tblcustomer', 'recCollision'] = {'系统ID': str(plan.objects['customer']), 'name': 'real customer'}
    with pytest.raises(ProbeRejected, match='UNOWNED_REMOTE_RECORD'):
        await p.run()
    assert not client.writes
    client.rows.clear()
    await p.write_record('customer', 'initial')
    known = p.state['records']['customer']['record_id']
    client.rows['tblcustomer', known]['name'] = 'Human edited this'
    with pytest.raises(ProbeRejected, match='RETIREMENT_INCOMPLETE'):
        await p.retire()
    assert len(client.writes) == 1


@pytest.mark.asyncio
async def test_acknowledged_write_needs_exact_readback(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    client.failure = 'create_bad_readback'
    with pytest.raises(ProbeRejected, match='WRITE_READBACK_MISMATCH'):
        await probe(journal, plan, loader, client).run()
    assert journal.read('STATE.json')['steps']['customer:initial']['status'] == 'unknown'


@pytest.mark.asyncio
async def test_known_message_receipt_resumes_readback_without_send(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    real_request = client.request
    failed = False
    async def request(method, path, **kwargs):
        nonlocal failed
        if path.startswith('/im/v1/messages/') and not failed:
            failed = True
            raise FeishuError('TRANSPORT_UNAVAILABLE', retryable=True)
        return await real_request(method, path, **kwargs)
    client.request = request
    with pytest.raises(FeishuError):
        await probe(journal, plan, loader, client).run()
    assert journal.read('STATE.json')['steps']['card:create']['message_id'] == 'om_probe1'
    await probe(journal, plan, loader, client).run()
    assert len([w for w in client.writes if w[0] == 'card']) == 2


def test_journal_rejects_symlinks_public_permissions_and_parallel_writers(tmp_path):
    private = (tmp_path / 'private').resolve()
    private.mkdir(mode=0o700)
    journal = ProbeJournal(private)
    with journal.lock():
        with pytest.raises(ProbeRejected, match='PROBE_ALREADY_RUNNING'), journal.lock():
            pass
    target = tmp_path / 'target'
    target.write_text('{}')
    (private / 'STATE.json').symlink_to(target)
    with pytest.raises(ProbeRejected, match='JOURNAL_SYMLINK_REFUSED'):
        journal.write('STATE.json', {})
    private.chmod(0o755)
    with pytest.raises(ProbeRejected, match='PRIVATE_DIRECTORY_REQUIRED'):
        ProbeJournal(private)


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['plan', 'step', 'hash', 'record', 'status'])
async def test_corrupt_journal_is_rejected_before_remote_io(tmp_path, corruption):
    journal, plan, loader, client = await setup(tmp_path)
    state = journal.read('STATE.json')
    if corruption == 'plan':
        state['plan_hash'] = '0' * 64
    elif corruption == 'step':
        state['steps']['customer:overwrite'] = {'status': 'verified'}
    elif corruption == 'hash':
        state['steps']['customer:initial'] = {'status': 'unknown', 'fields_hash': 'invalid'}
    elif corruption == 'record':
        state['records']['customer'] = {'record_id': '../outside', 'fields': {}}
    else:
        state['status'] = 'unrecognized'
    journal.write('STATE.json', state)
    loader.reset_mock()
    with pytest.raises(ProbeRejected):
        probe(journal, plan, loader, client)
    loader.assert_not_called()
    assert not client.writes


@pytest.mark.asyncio
async def test_configuration_is_checked_again_after_remote_read_before_mutation(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    original = client.request
    async def change_after_lookup(method, path, **kwargs):
        result = await original(method, path, **kwargs)
        if path.endswith('/records/search'):
            loader.return_value = LiveConnection(loader.return_value.config, 'rotated')
        return result
    client.request = change_after_lookup
    with pytest.raises(ProbeRejected, match='LIVE_CONFIGURATION_CHANGED'):
        await probe(journal, plan, loader, client).run()
    assert not client.writes


@pytest.mark.asyncio
async def test_missing_message_receipt_never_retries_send_and_still_allows_retirement(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    original = client.send_card
    async def invalid_receipt(*args):
        await original(*args)
        return 'unexpected-receipt'
    client.send_card = invalid_receipt
    with pytest.raises(ProbeRejected, match='INVALID_MESSAGE_RECEIPT'):
        await probe(journal, plan, loader, client).run()
    with pytest.raises(ProbeRejected, match='INVALID_MESSAGE_RECEIPT'):
        await probe(journal, plan, loader, client).run()
    await probe(journal, plan, loader, client).retire()
    assert len([w for w in client.writes if w[0] == 'card']) == 1
    assert journal.read('STATE.json')['status'] == 'retired'


@pytest.mark.asyncio
async def test_arbitrary_record_phase_and_filename_are_refused(tmp_path):
    journal, plan, loader, client = await setup(tmp_path)
    with pytest.raises(ProbeRejected, match='WRITE_OUTSIDE_PLAN'):
        await probe(journal, plan, loader, client).write_record('task', 'updated')
    with pytest.raises(ProbeRejected, match='INVALID_JOURNAL_FILE'):
        journal.write('../outside.json', {})
    with pytest.raises(ProbeRejected, match='INVALID_JOURNAL_FILE'):
        journal.read('../outside.json')
    assert not client.writes


@pytest.mark.asyncio
@pytest.mark.parametrize('rotate_during_preflight', [False, True])
async def test_cli_preflight_connects_read_only_and_binds_client_credentials(
        tmp_path, monkeypatch, rotate_during_preflight):
    path = Path(__file__).parents[1] / 'scripts/acceptance/feishu_release_probe.py'
    spec = spec_from_file_location('feishu_release_probe_cli', path)
    cli = module_from_spec(spec)
    spec.loader.exec_module(cli)
    directory = (tmp_path / 'private').resolve()
    directory.mkdir(mode=0o700)
    config, schemas = configuration()
    client = Transport(config, schemas)
    client.close = AsyncMock()
    conn = SimpleNamespace(close=AsyncMock())
    connect = AsyncMock(return_value=conn)
    verify = AsyncMock()
    value = {'workspace_id': config.workspace_id, 'settings': config.model_dump(mode='json'),
             'enabled': True, 'revision': config.revision, 'encryption_key_id': 'test-revision-only',
             'credential_updated_at': datetime.now(UTC)}
    repository = SimpleNamespace(worker_config=AsyncMock(return_value=value))
    if rotate_during_preflight:
        repository.worker_config.side_effect = [value, {**value, 'encryption_key_id': 'rotated'}]
    monkeypatch.setenv('FEISHU_SYNC_DATABASE_URL', 'postgresql://synthetic-test-only/none')
    monkeypatch.setattr(cli.asyncpg, 'connect', connect)
    monkeypatch.setattr(cli, 'verify_worker_role', verify)
    monkeypatch.setattr(cli, 'FeishuRepository', lambda: repository)
    monkeypatch.setattr(cli, 'get_settings', lambda: None)
    monkeypatch.setattr(cli, 'FeishuSyncService', lambda settings: SimpleNamespace(client=lambda row: (config, client)))
    args = SimpleNamespace(action='prepare', directory=str(directory), workspace=config.workspace_id,
                           connection=config.connection_id, plan_sha256=None)
    if rotate_during_preflight:
        with pytest.raises(ProbeRejected, match='LIVE_CREDENTIAL_CHANGED'):
            await cli.execute(args)
        assert not (directory / 'PLAN.json').exists()
    else:
        result = await cli.execute(args)
        assert result['status'] == 'prepared_no_external_writes'
    assert connect.call_args.kwargs['server_settings'] == {'default_transaction_read_only': 'on'}
    verify.assert_awaited_once_with(conn)
    assert all(call.args == (conn, config.connection_id) for call in repository.worker_config.await_args_list)
    assert not client.writes
    conn.close.assert_awaited_once()
    client.close.assert_awaited_once()
    if rotate_during_preflight:
        return
    # Status requires neither a database DSN nor a live provider.
    monkeypatch.delenv('FEISHU_SYNC_DATABASE_URL')
    connect.reset_mock()
    args.action = 'status'
    assert (await cli.execute(args))['status'] == 'prepared'
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_cli_wrong_digest_rejected_without_database_connection(tmp_path, monkeypatch):
    journal, plan, loader, client = await setup(tmp_path)
    path = Path(__file__).parents[1] / 'scripts/acceptance/feishu_release_probe.py'
    spec = spec_from_file_location('feishu_release_probe_wrong_digest_cli', path)
    cli = module_from_spec(spec)
    spec.loader.exec_module(cli)
    connect = AsyncMock()
    monkeypatch.setattr(cli.asyncpg, 'connect', connect)
    args = SimpleNamespace(action='run', directory=str(journal.directory), plan_sha256='0' * 64)
    with pytest.raises(ProbeRejected, match='EXACT_PLAN_HASH_REQUIRED'):
        await cli.execute(args)
    connect.assert_not_called()
