"""Initialize or explicitly revise one paused connection from a secret-free template.

Existing configuration requires its exact revision and must already be paused.
Uses an existing administrator resolved through the normal maintenance identity
lookup. Run using the API service environment and user. No credential is accepted.
"""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from sales_backend.config import get_settings
from sales_backend.db import Database, json_value
from sales_backend.domain.feishu_sync.config import SyncConfig
from sales_backend.repositories.feishu_sync import FeishuRepository
from sales_backend.repositories.identity import IdentityRepository


async def run(args):
    template = json.loads(Path(args.template).read_text())
    if set(template) - {'template_version', 'app_id', 'base_token', 'base_url', 'mappings', 'notification'}:
        raise ValueError('Secret-free template required')
    if template.pop('template_version') != 1:
        raise ValueError('Unsupported template')
    if template['notification'].get('enabled'):
        raise ValueError('Initial notification must remain disabled')
    database = Database(get_settings())
    await database.connect()
    try:
        async with database.connection() as connection:
            identity = await IdentityRepository().find_maintenance_administrator(connection,
                workspace_external_id=args.workspace, account_code=args.administrator)
        if identity is None:
            raise ValueError('Existing administrator required')
        repository = FeishuRepository()
        async with database.transaction(identity.context) as connection:
            existing = await repository.config(connection, identity.context.workspace_id, lock=True)
            if existing and (existing["enabled"] or existing["revision"] != args.expected_revision):
                raise ValueError('Connection must be paused at expected revision')
            if not existing and args.expected_revision != 0:
                raise ValueError('Expected existing connection is missing')
            previous = json_value(existing["settings"]) if existing else {}
            config = SyncConfig(**template, workspace_id=identity.context.workspace_id,
                connection_id=previous.get("connection_id", uuid4()),
                credential_ref=previous.get("credential_ref", uuid4()),
                revision=args.expected_revision+1, enabled=False)
            await repository.save(connection, identity.context, config, args.expected_revision)
        print(json.dumps({'configured': True, 'enabled': False, 'notifications': False,
                          'object_count': len(config.mappings),
                          'credential_preserved': bool(existing and existing['has_credential'])}))
    finally:
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--administrator', required=True)
    parser.add_argument('--template', required=True)
    parser.add_argument('--expected-revision', type=int, default=0)
    args = parser.parse_args()
    try:
        asyncio.run(asyncio.wait_for(run(args), timeout=60))
    except Exception:
        print(json.dumps({'configured': False, 'code': 'FEISHU_PREPARATION_FAILED'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
