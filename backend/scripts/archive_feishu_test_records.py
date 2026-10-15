"""Dry-run by default; retire only an explicitly receipted, authorized test batch.

Run under the existing API service user/environment. Never accepts credentials.
--apply additionally requires the preceding dry-run plan hash.
"""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.maintenance.feishu_test_archive import ArchiveManifest, ArchiveRejected, archive
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata


async def run(args):
    manifest = ArchiveManifest.model_validate_json(Path(args.manifest).read_text())
    if args.apply and not args.expected_plan:
        raise ArchiveRejected('APPLY_REQUIRES_DRY_RUN_DIGEST')
    database = Database(get_settings())
    await database.connect()
    metadata = request_metadata.set(RequestMetadata(request_id=str(uuid4()),
        user_agent='salegent-feishu-test-archive'))
    try:
        async with database.connection() as connection:
            identity = await IdentityRepository().find_maintenance_administrator(connection,
                workspace_external_id=args.workspace, account_code=args.administrator)
        if identity is None:
            raise ArchiveRejected('EXISTING_ADMINISTRATOR_REQUIRED')
        async with database.transaction(identity.context) as connection:
            # The guarded database operation may take brief dependency-table locks.
            # Bound their duration and commit immediately; no network work in this transaction.
            await connection.execute("SET LOCAL statement_timeout = '5s'")
            await connection.execute("SET LOCAL lock_timeout = '500ms'")
            result = await archive(connection, identity.context, manifest,
                                   expected_plan=args.expected_plan if args.apply else None)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        request_metadata.reset(metadata)
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--administrator', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan')
    args = parser.parse_args()
    try:
        asyncio.run(asyncio.wait_for(run(args), timeout=60))
    except ArchiveRejected as exc:
        print(json.dumps({'archived': False, 'code': str(exc)}))
        return 1
    except Exception:
        print(json.dumps({'archived': False, 'code': 'ARCHIVE_CHECK_FAILED'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
