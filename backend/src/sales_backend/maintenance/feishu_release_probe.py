"""Bounded external acceptance; never writes CRM, queues, mappings or deliveries.

The caller supplies a read-only, live connection loader and the production
Feishu transport. Only three locally generated synthetic objects and two cards
in the configured default group are permitted. This is transport/projection
acceptance, not evidence that real business events traversed the worker.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sales_backend.domain.feishu_sync.cards import notification_card, opportunity_update_card
from sales_backend.domain.feishu_sync.config import COMMON_FIELDS, SOURCE_FIELDS, SyncConfig
from sales_backend.domain.feishu_sync.planning import SourceEvent, notification_plans
from sales_backend.domain.feishu_sync.projection import RELATIONS, project
from sales_backend.integrations.feishu import FeishuError
from sales_backend.services.feishu_sync import TASK_MEASURES, FeishuSyncService

KINDS = ('customer', 'opportunity', 'task')
REQUIRED = {
    'customer': {'name', 'record_status'},
    'opportunity': {'name', 'record_status', 'customer_id', 'amount', 'currency', 'expected_close_date'},
    'task': {'title', 'record_status', 'status', 'customer_id', 'opportunity_id',
             'batch_id', 'batch_name', 'owner_name', 'verification_status', *TASK_MEASURES},
}
RECORD_ID = r'^rec[A-Za-z0-9]+$'
PHASES = {
    'customer': ('initial', 'archived'),
    'opportunity': ('initial', 'updated', 'archived'),
    'task': ('initial', 'archived'),
}


class ProbeRejected(ValueError):
    """Safe error codes only: never append provider bodies, credentials or rows."""


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def config_fingerprint(config: SyncConfig):
    """Canonicalize only the config's unordered sets, preserving every value.

    Pydantic serializes frozensets as JSON arrays in hash-seed-dependent order.
    Route/chat tuples and all other arrays retain their original ordering.
    """
    value = config.model_dump(mode='json')
    for field in ('on_create', 'on_update'):
        value['notification'][field] = sorted(value['notification'][field])
    return fingerprint(value)


@dataclass(frozen=True)
class LiveConnection:
    config: SyncConfig
    credential_revision: str


class ProbePlan(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    # v1 had different writes; v2 used a process-dependent config digest.
    # Neither can be reinterpreted or resumed under this fingerprint contract.
    version: int = Field(default=3, ge=3, le=3)
    run_id: UUID
    workspace_id: UUID
    connection_id: UUID
    created_at: datetime
    config_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    credential_revision: str
    schemas: dict[str, str]
    objects: dict[str, UUID]
    app_id: str
    base_token: str
    tables: dict[str, str]
    chat_id: str

    @model_validator(mode='after')
    def bounded(self):
        if (self.created_at.tzinfo is None or set(self.schemas) != set(KINDS)
                or set(self.objects) != set(KINDS) or set(self.tables) != set(KINDS)
                or len(set(self.objects.values())) != 3 or len(set(self.tables.values())) != 3):
            raise ValueError('Exactly three unique typed objects and targets are required')
        return self

    @property
    def prefix(self):
        return '【上线验收】' + self.run_id.hex[:12]

    @property
    def digest(self):
        return fingerprint(self.model_dump(mode='json'))


class ProbeJournal:
    """One private directory, atomic/fsynced checkpoints, nonblocking flock."""

    def __init__(self, directory):
        self.directory = Path(directory)
        if (not self.directory.is_absolute() or '..' in self.directory.parts
                or self.directory.resolve() != self.directory):
            raise ProbeRejected('PRIVATE_LITERAL_DIRECTORY_REQUIRED')
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ProbeRejected('PRIVATE_DIRECTORY_REQUIRED')

    @contextmanager
    def lock(self):
        fd = os.open(self.directory / 'probe.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ProbeRejected('PRIVATE_LOCK_REQUIRED')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ProbeRejected('PROBE_ALREADY_RUNNING') from None
            yield
        finally:
            os.close(fd)

    def read(self, name):
        if name not in {'PLAN.json', 'STATE.json'}:
            raise ProbeRejected('INVALID_JOURNAL_FILE')
        fd = os.open(self.directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'r') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 2_000_000):
                raise ProbeRejected('PRIVATE_JOURNAL_REQUIRED')
            return json.load(stream)

    def write(self, name, value, *, create=False):
        if name not in {'PLAN.json', 'STATE.json'}:
            raise ProbeRejected('INVALID_JOURNAL_FILE')
        target = self.directory / name
        if target.is_symlink():
            raise ProbeRejected('JOURNAL_SYMLINK_REFUSED')
        fd, temporary = tempfile.mkstemp(prefix='.probe-', dir=self.directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            if create:
                os.link(temporary, target)
            else:
                os.replace(temporary, target)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)


def schema_identity(mapping, fields):
    used = {mapping.id_field_id, *mapping.fields.values()}
    if not used <= fields.keys():
        raise ProbeRejected('MAPPED_FIELD_MISSING')
    return fingerprint({key: fields[key] for key in sorted(used)})


def validate_config(config, workspace_id, connection_id):
    if config.workspace_id != workspace_id or config.connection_id != connection_id:
        raise ProbeRejected('CONNECTION_SCOPE_CHANGED')
    if not config.enabled or config.direction != 'system_to_base' or not config.notification.enabled:
        raise ProbeRejected('CONNECTION_NOT_READY')
    for kind in KINDS:
        mapping = config.mappings.get(kind)
        if not mapping or not mapping.enabled or not REQUIRED[kind] <= mapping.fields.keys():
            raise ProbeRejected('REQUIRED_MAPPING_MISSING')
    if ('opportunity' not in config.notification.on_create
            or 'opportunity' not in config.notification.on_update
            or not config.notification.default_chat_id):
        raise ProbeRejected('OPPORTUNITY_NOTIFICATIONS_REQUIRED')


async def inspect_schema(client, config, kind):
    mapping = config.mappings[kind]
    fields = await client.fields(config.base_token, mapping.table_id)
    schema_identity(mapping, fields)
    if fields[mapping.id_field_id]['type'] != 1:
        raise ProbeRejected('SYSTEM_ID_MUST_BE_TEXT')
    for source, target in mapping.fields.items():
        definition = fields[target]
        if source in TASK_MEASURES and definition['type'] != 2:
            raise ProbeRejected('TASK_MEASURE_MUST_BE_NUMBER')
        if definition['type'] not in {1, 2, 3, 4, 5, 7, 13, 18, 21}:
            raise ProbeRejected('UNSUPPORTED_FIELD_TYPE')
        if definition['type'] in {18, 21}:
            parent = config.mappings.get(RELATIONS.get(source))
            if not parent or not parent.enabled or definition.get('property', {}).get('table_id') != parent.table_id:
                raise ProbeRejected('LINK_TARGET_CHANGED')
    return fields


async def prepare(journal, loader, client, workspace_id, connection_id):
    """Read-only remote preflight; creates only the local plan and journal."""
    live = await loader()
    config = live.config
    validate_config(config, workspace_id, connection_id)
    if client.app_id != config.app_id:
        raise ProbeRejected('CLIENT_APP_ID_MISMATCH')
    schemas = {}
    for kind in KINDS:
        schemas[kind] = schema_identity(config.mappings[kind], await inspect_schema(client, config, kind))
    await client.request('GET', f'/im/v1/chats/{config.notification.default_chat_id}')
    plan = ProbePlan(run_id=uuid4(), workspace_id=workspace_id, connection_id=connection_id,
        created_at=datetime.now(UTC), config_hash=config_fingerprint(config),
        credential_revision=live.credential_revision, schemas=schemas,
        objects={kind: uuid4() for kind in KINDS}, app_id=config.app_id, base_token=config.base_token,
        tables={kind: config.mappings[kind].table_id for kind in KINDS},
        chat_id=config.notification.default_chat_id)
    # Reject UUID collisions before any external mutation, regardless of labels.
    for kind in KINDS:
        mapping = config.mappings[kind]
        fields = await inspect_schema(client, config, kind)
        if await client.find_record(config.base_token, mapping.table_id,
                                    fields[mapping.id_field_id]['field_name'], str(plan.objects[kind])):
            raise ProbeRejected('SYSTEM_ID_ALREADY_EXISTS')
    with journal.lock():
        journal.write('PLAN.json', plan.model_dump(mode='json'), create=True)
        journal.write('STATE.json', {'plan_hash': plan.digest, 'steps': {}, 'records': {},
                                    'status': 'prepared'}, create=True)
    return plan


def raw_fixture(plan, kind, phase):
    base = {'id': str(plan.objects[kind]), 'name': plan.prefix + kind,
        'title': plan.prefix + kind, 'description': '合成验收样本，不是正式业务任务',
        'company_name': '上线验收合成投影', 'created_at': plan.created_at.isoformat(),
        'updated_at': plan.created_at.isoformat(), 'version_no': 1,
        'owner_name': plan.prefix + '合成负责人', 'source_code': 'release_probe',
        'batch_id': str(plan.run_id), 'batch_name': plan.prefix + '合成批次',
        'customer_name': plan.prefix + 'customer', 'amount': 0, 'currency': 'CNY',
        'stage_code': 'identified', 'probability': 10,
        'expected_close_date': (plan.created_at + timedelta(days=14)).date().isoformat(),
        'customer_id': str(plan.objects['customer']), 'opportunity_id': str(plan.objects['opportunity']),
        'due_at': (plan.created_at + timedelta(days=1)).isoformat(),
        'status': 'cancelled' if kind == 'task' else 'open', 'verification_status': 'not_required'}
    if phase == 'updated':
        base.update(version_no=2,
                    expected_close_date=(plan.created_at + timedelta(days=21)).date().isoformat())
    elif phase == 'archived':
        base = {'id': str(plan.objects[kind]), 'deleted': True}
    return base


def normalized(value, field_type):
    # Bitable may return text as spans and links as linked-record objects.
    if value is None or value == []:
        return None
    if field_type in {1, 13} and isinstance(value, list):
        if not all(isinstance(item, dict) and isinstance(item.get('text'), str) for item in value):
            raise ProbeRejected('UNRECOGNIZED_TEXT_READBACK')
        return ''.join(item['text'] for item in value)
    if field_type in {18, 21}:
        # Search returns link envelopes (including {} for no links), while
        # older record reads and writes use lists. Accept only known shapes.
        if isinstance(value, dict):
            if not value:
                return None
            if (set(value) != {'link_record_ids'} or not isinstance(value['link_record_ids'], list)
                    or not all(isinstance(item, str) for item in value['link_record_ids'])):
                raise ProbeRejected('UNRECOGNIZED_LINK_READBACK')
            value = value['link_record_ids']
        if not isinstance(value, list):
            raise ProbeRejected('UNRECOGNIZED_LINK_READBACK')
        ids = [item if isinstance(item, str) else item.get('record_id') if isinstance(item, dict) else None
               for item in value]
        if not all(isinstance(item, str) and re.fullmatch(RECORD_ID, item) for item in ids):
            raise ProbeRejected('UNRECOGNIZED_LINK_READBACK')
        return sorted(ids) or None
    return value


def matches(fields, expected, schema):
    by_name = {definition['field_name']: definition for definition in schema.values()}
    return all(normalized(fields.get(key), by_name[key]['type']) == normalized(value, by_name[key]['type'])
               for key, value in expected.items())


def validate_state(plan, state):
    """Reject damaged or hand-edited checkpoint shapes before any remote I/O."""
    if not isinstance(state, dict) or state.get('plan_hash') != plan.digest:
        raise ProbeRejected('JOURNAL_PLAN_MISMATCH')
    if (set(state) != {'plan_hash', 'status', 'steps', 'records'}
            or state['status'] not in {'prepared', 'retired', 'retirement_incomplete', 'passed_and_retired'}
            or not isinstance(state['steps'], dict) or not isinstance(state['records'], dict)
            or not set(state['records']) <= set(KINDS)):
        raise ProbeRejected('INVALID_JOURNAL_STATE')
    record_steps = {kind + ':' + phase for kind, phases in PHASES.items() for phase in phases}
    for key, step in state['steps'].items():
        if (key not in record_steps | {'card:create', 'card:update'} or not isinstance(step, dict)
                or step.get('status') not in {'unknown', 'rejected', 'verified'}):
            raise ProbeRejected('INVALID_JOURNAL_STEP')
        if key in record_steps:
            absent = step.get('absent') is True and key.endswith(':archived') and step['status'] == 'verified'
            if not absent and not re.fullmatch(r'[a-f0-9]{64}', str(step.get('fields_hash', ''))):
                raise ProbeRejected('INVALID_JOURNAL_FIELDS_HASH')
        elif step['status'] == 'verified' and not re.fullmatch(
                r'om_[A-Za-z0-9]+', str(step.get('message_id', ''))):
            raise ProbeRejected('INVALID_MESSAGE_RECEIPT')
    for record in state['records'].values():
        if (not isinstance(record, dict) or set(record) != {'record_id', 'fields'}
                or not re.fullmatch(RECORD_ID, str(record.get('record_id', '')))
                or not isinstance(record.get('fields'), dict)):
            raise ProbeRejected('INVALID_JOURNAL_RECORD')


class ReleaseProbe:
    def __init__(self, journal, plan, state, loader, client):
        validate_state(plan, state)
        self.journal, self.plan, self.state = journal, plan, state
        self.loader, self.client = loader, client
        self.service = FeishuSyncService(None)

    def save(self):
        self.journal.write('STATE.json', self.state)

    async def guard(self, kind):
        live = await self.loader()
        config = live.config
        validate_config(config, self.plan.workspace_id, self.plan.connection_id)
        if self.client.app_id != config.app_id:
            raise ProbeRejected('CLIENT_APP_ID_MISMATCH')
        if (config_fingerprint(config) != self.plan.config_hash
                or live.credential_revision != self.plan.credential_revision):
            raise ProbeRejected('LIVE_CONFIGURATION_CHANGED')
        schema = await inspect_schema(self.client, config, kind)
        if schema_identity(config.mappings[kind], schema) != self.plan.schemas[kind]:
            raise ProbeRejected('REMOTE_SCHEMA_CHANGED')
        return config, schema

    async def record(self, config, kind, schema):
        mapping = config.mappings[kind]
        id_field = schema[mapping.id_field_id]['field_name']
        # Search supports the existing fine-grained record-read scope. A second
        # single-record GET would additionally require a broad Base scope.
        data = await self.client.request('POST', self.client.table_path(config.base_token, mapping.table_id)
            + '/records/search', params={'page_size': 2}, body={
                'field_names': sorted({schema[target]['field_name']
                                       for target in {mapping.id_field_id, *mapping.fields.values()}}),
                'filter': {'conjunction': 'and', 'conditions': [
                    {'field_name': id_field, 'operator': 'is', 'value': [str(self.plan.objects[kind])]}]}})
        rows = data.get('items', [])
        if not isinstance(rows, list):
            raise ProbeRejected('INVALID_RECORD_RECEIPT')
        if len(rows) > 1 or data.get('has_more'):
            raise FeishuError('DUPLICATE_SYSTEM_ID')
        if not rows:
            return None, None
        row = rows[0]
        if not isinstance(row, dict):
            raise ProbeRejected('INVALID_RECORD_RECEIPT')
        rid = row.get('record_id')
        if not isinstance(rid, str) or not re.fullmatch(RECORD_ID, rid):
            raise ProbeRejected('INVALID_RECORD_RECEIPT')
        fields = row.get('fields')
        expected_id = {id_field: str(self.plan.objects[kind])}
        if not isinstance(fields, dict) or not matches(fields, expected_id, schema):
            raise ProbeRejected('REMOTE_IDENTITY_MISMATCH')
        known = self.state['records'].get(kind)
        if known and known['record_id'] != rid:
            raise ProbeRejected('REMOTE_RECORD_REPLACED')
        return rid, fields

    async def write_record(self, kind, phase):
        if kind not in PHASES or phase not in PHASES[kind]:
            raise ProbeRejected('WRITE_OUTSIDE_PLAN')
        step_key = kind + ':' + phase
        if self.state['steps'].get(step_key, {}).get('status') == 'verified':
            return
        config, schema = await self.guard(kind)
        mapping = config.mappings[kind]
        raw = raw_fixture(self.plan, kind, phase)
        values = project(kind, raw, SOURCE_FIELDS[kind] | COMMON_FIELDS)
        values['synced_at'] = self.plan.created_at.isoformat()
        links = {}
        for source, parent in RELATIONS.items():
            if source in mapping.fields and values.get(source):
                if parent not in KINDS or values[source] != str(self.plan.objects[parent]):
                    raise ProbeRejected('NON_SYNTHETIC_RELATION_REFUSED')
                known = self.state['records'].get(parent)
                if not known:
                    raise ProbeRejected('SYNTHETIC_PARENT_NOT_VERIFIED')
                links[source] = [known['record_id']]
        fields = await self.service.project_fields(None, {'object_kind': kind}, config, mapping,
                                                    values, raw, schema, links)
        step = self.state['steps'].get(step_key)
        if step and step.get('fields_hash') != fingerprint(fields):
            raise ProbeRejected('WRITE_PLAN_CHANGED')
        rid, remote = await self.record(config, kind, schema)
        known = self.state['records'].get(kind)
        if step and step['status'] == 'unknown':
            if rid and matches(remote, fields, schema):
                self.state['records'][kind] = {'record_id': rid,
                                               'fields': {**(known or {}).get('fields', {}), **fields}}
                self.state['steps'][step_key] = {**step, 'status': 'verified'}
                self.save()
                return
            raise ProbeRejected('RECORD_RESULT_UNKNOWN')
        if rid and not known:
            raise ProbeRejected('UNOWNED_REMOTE_RECORD')
        if known and (not rid or not matches(remote, known['fields'], schema)):
            raise ProbeRejected('REMOTE_RECORD_CHANGED')
        if phase == 'archived' and not rid:
            # Unknown creates are not proof of absence; do not declare cleanup.
            if any(key.startswith(kind + ':') and value['status'] == 'unknown'
                   for key, value in self.state['steps'].items()):
                raise ProbeRejected('RECORD_RESULT_UNKNOWN')
            self.state['steps'][step_key] = {'status': 'verified', 'absent': True}
            self.save()
            return
        # Check configuration/schema again immediately before issuing the write.
        await self.guard(kind)
        self.state['steps'][step_key] = {'status': 'unknown', 'fields_hash': fingerprint(fields)}
        self.save()
        try:
            if rid:
                await self.client.update_record(config.base_token, mapping.table_id, rid, fields)
            else:
                token = UUID(bytes=hashlib.sha256(
                    f'{self.plan.run_id}:{kind}'.encode()).digest()[:16], version=4)
                rid = await self.client.create_record(config.base_token, mapping.table_id, fields, token)
        except FeishuError as exc:
            if not exc.unknown:
                self.state['steps'][step_key]['status'] = 'rejected'
                self.state['steps'][step_key]['error'] = exc.code
                self.save()
            raise
        # A positive HTTP response alone is not verified remote state.
        actual_id, actual = await self.record(config, kind, schema)
        if actual_id != rid or not matches(actual, fields, schema):
            raise ProbeRejected('WRITE_READBACK_MISMATCH')
        self.state['records'][kind] = {'record_id': rid, 'fields': {**(known or {}).get('fields', {}), **fields}}
        self.state['steps'][step_key]['status'] = 'verified'
        self.save()

    async def card(self, update):
        key = 'card:update' if update else 'card:create'
        step = self.state['steps'].get(key)
        if step and step['status'] == 'verified':
            return
        if step and step['status'] == 'unknown' and not step.get('message_id'):
            raise ProbeRejected('MESSAGE_RESULT_UNKNOWN')
        config, _ = await self.guard('opportunity')
        change_id = uuid5(self.plan.run_id, 'opportunity-update') if update else None
        event = SourceEvent(uuid5(self.plan.run_id, key), config.workspace_id, 'opportunity',
                            self.plan.objects['opportunity'], not update, change_id=change_id)
        plans = notification_plans(config, event)
        if len(plans) != 1 or plans[0].chat_id != self.plan.chat_id:
            raise ProbeRejected('NOTIFICATION_TARGET_CHANGED')
        receipt = self.state['records'].get('opportunity')
        if not receipt:
            raise ProbeRejected('OPPORTUNITY_NOT_VERIFIED')
        if update:
            before = raw_fixture(self.plan, 'opportunity', 'initial')['expected_close_date']
            after = raw_fixture(self.plan, 'opportunity', 'updated')['expected_close_date']
            snapshot = {'opportunity_name': self.plan.prefix + 'opportunity',
                'customer_name': self.plan.prefix + 'customer', 'owner_name': self.plan.prefix + '合成负责人',
                'actor_name': self.plan.prefix + '外部投影探针', 'changed_at': self.plan.created_at.isoformat(),
                'currency': 'CNY', 'changes': [{'label': '预计关单日期', 'before': before, 'after': after}]}
            card = opportunity_update_card(snapshot, config, receipt['record_id'])
        else:
            values = project('opportunity', raw_fixture(self.plan, 'opportunity', 'initial'),
                             SOURCE_FIELDS['opportunity'] | COMMON_FIELDS)
            card = notification_card('opportunity', values, config, receipt['record_id'])
        card['header']['title']['content'] = '【上线验收】' + card['header']['title']['content']
        if not step or not step.get('message_id'):
            await self.guard('opportunity')
            self.state['steps'][key] = {'status': 'unknown'}
            self.save()
            try:
                message_id = await self.client.send_card(self.plan.chat_id, card, plans[0].dedupe_key[:50])
            except FeishuError as exc:
                if not exc.unknown:
                    self.state['steps'][key] = {'status': 'rejected', 'error': exc.code}
                    self.save()
                raise
            self.state['steps'][key]['message_id'] = message_id
            self.save()
        message_id = self.state['steps'][key]['message_id']
        if not re.fullmatch(r'om_[A-Za-z0-9]+', message_id):
            raise ProbeRejected('INVALID_MESSAGE_RECEIPT')
        data = await self.client.request('GET', '/im/v1/messages/' + message_id)
        items = data.get('items', [])
        if (len(items) != 1 or items[0].get('message_id') != message_id
                or items[0].get('chat_id') != self.plan.chat_id or items[0].get('msg_type') != 'interactive'):
            raise ProbeRejected('MESSAGE_READBACK_MISMATCH')
        self.state['steps'][key]['status'] = 'verified'
        self.save()

    async def retire(self):
        failures = []
        for kind in reversed(KINDS):
            try:
                # Reconcile an interrupted create/update by exact readback,
                # without replaying it or sending any pending notifications.
                for key, step in list(self.state['steps'].items()):
                    if key.startswith(kind + ':') and step['status'] == 'unknown':
                        await self.write_record(kind, key.split(':')[1])
                await self.write_record(kind, 'archived')
            except (ProbeRejected, FeishuError) as exc:
                failures.append(exc.args[0])
        self.state['status'] = 'retirement_incomplete' if failures else 'retired'
        self.save()
        if failures:
            raise ProbeRejected('RETIREMENT_INCOMPLETE')

    async def run(self):
        if self.state['status'] in {'retired', 'retirement_incomplete'}:
            raise ProbeRejected('RETIRED_RUN_CANNOT_RESTART')
        for kind in KINDS:
            await self.write_record(kind, 'initial')
        await self.card(False)
        await self.write_record('opportunity', 'updated')
        await self.card(True)
        await self.retire()
        self.state['status'] = 'passed_and_retired'
        self.save()
