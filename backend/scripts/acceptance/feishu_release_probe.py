#!/usr/bin/env python3
"""Read-only preflight / explicitly applied external Feishu acceptance.

Run inside the reviewed release's existing environment. Credentials are loaded
only through the dedicated worker identity and existing credential adapter;
they are never CLI arguments, serialized state or console output.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from uuid import UUID

import asyncpg

from sales_backend.config import get_settings
from sales_backend.db import json_value
from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.feishu_worker import verify_worker_role
from sales_backend.maintenance.feishu_release_probe import (
    LiveConnection,
    ProbeJournal,
    ProbePlan,
    ProbeRejected,
    ReleaseProbe,
    fingerprint,
    prepare,
    validate_state,
)
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.services.feishu_sync import FeishuSyncService


async def execute(args):
    journal = ProbeJournal(args.directory)
    if args.action == 'status':
        with journal.lock():
            plan = ProbePlan.model_validate(journal.read('PLAN.json'))
            state = journal.read('STATE.json')
            validate_state(plan, state)
        return {'plan_sha256': plan.digest, 'status': state['status'],
                'steps': {key: value['status'] for key, value in state['steps'].items()},
                'core_crm_writes': 0, 'end_to_end_business_acceptance': False}
    if args.action == 'prepare':
        if not args.workspace or not args.connection:
            raise ProbeRejected('EXPLICIT_WORKSPACE_AND_CONNECTION_REQUIRED')
        workspace, connection_id = args.workspace, args.connection
        plan = None
    else:
        plan = ProbePlan.model_validate(journal.read('PLAN.json'))
        if args.plan_sha256 != plan.digest:
            raise ProbeRejected('EXACT_PLAN_HASH_REQUIRED')
        workspace, connection_id = plan.workspace_id, plan.connection_id
    dsn = os.environ.get('FEISHU_SYNC_DATABASE_URL', '')
    if not dsn:
        raise ProbeRejected('DEDICATED_FEISHU_DATABASE_URL_REQUIRED')
    # The tool does not acquire a queue lease or invoke mutating helpers. The
    # server also rejects every SQL write, even if one is accidentally added.
    conn = await asyncpg.connect(dsn=dsn.replace('postgresql+asyncpg://', 'postgresql://', 1),
        server_settings={'default_transaction_read_only': 'on'}, command_timeout=30)
    client = None
    try:
        await verify_worker_role(conn)
        repository = FeishuRepository()

        async def row():
            value = await repository.worker_config(conn, connection_id)
            if not value or value['workspace_id'] != workspace:
                raise ProbeRejected('ACTIVE_VALIDATED_CONNECTION_REQUIRED')
            return value

        def credential_revision(value):
            return fingerprint({'key_id': value['encryption_key_id'],
                                'updated_at': value['credential_updated_at'].isoformat()})

        initial_row = await row()
        client_revision = credential_revision(initial_row)

        async def loader():
            value = await row()
            config = SyncConfig.model_validate(json_value(value['settings']) | {'enabled': value['enabled']})
            if config.revision != value['revision']:
                raise ProbeRejected('CONFIG_REVISION_MISMATCH')
            revision = credential_revision(value)
            if revision != client_revision:
                raise ProbeRejected('LIVE_CREDENTIAL_CHANGED')
            return LiveConnection(config, revision)

        _, client = FeishuSyncService(get_settings()).client(initial_row)
        if args.action == 'prepare':
            plan = await prepare(journal, loader, client, workspace, connection_id)
            return {'status': 'prepared_no_external_writes', 'plan_sha256': plan.digest,
                    'objects': {key: str(value) for key, value in plan.objects.items()},
                    'tables': plan.tables, 'chat_id': plan.chat_id,
                    'maximum_records': 3, 'maximum_cards': 2, 'core_crm_writes': 0}
        with journal.lock():
            # Reload under lock; another invocation may have completed meanwhile.
            locked_plan = ProbePlan.model_validate(journal.read('PLAN.json'))
            if locked_plan.digest != plan.digest:
                raise ProbeRejected('JOURNAL_PLAN_CHANGED')
            state = journal.read('STATE.json')
            probe = ReleaseProbe(journal, plan, state, loader, client)
            if args.action == 'run':
                await probe.run()
            else:
                await probe.retire()
            return {'status': state['status'], 'plan_sha256': plan.digest,
                    'core_crm_writes': 0, 'end_to_end_business_acceptance': False}
    finally:
        if client:
            await client.close()
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'retire', 'status'))
    parser.add_argument('--directory', required=True, help='Existing absolute private directory (mode 0700)')
    parser.add_argument('--workspace', type=UUID)
    parser.add_argument('--connection', type=UUID)
    parser.add_argument('--plan-sha256', help='Exact reviewed prepare digest; required for run and retire')
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(execute(args)), ensure_ascii=False, indent=2))
    except Exception as exc:
        # Unknown errors may contain URLs, ciphertext or provider payloads.
        code = str(exc) if isinstance(exc, ProbeRejected) else type(exc).__name__
        print(json.dumps({'status': 'stopped', 'error': code,
                          'resume': 'Read private STATE.json; run retirement separately if required'}))
        sys.exit(1)


if __name__ == '__main__':
    main()
