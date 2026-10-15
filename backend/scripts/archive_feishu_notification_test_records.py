"""Dry-run/apply the one-time, fingerprint-bound notification smoke-test archive.

The descriptor is supplied as a local file so the reviewed test names are never
stored in the repository. ``--apply`` requires the plan hash from the preceding
dry run and performs no network work while the database transaction is open.
"""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from sales_backend.config import get_settings
from sales_backend.db import Database
from sales_backend.maintenance.feishu_notification_test_archive import (
    NotificationTestDescriptor,
    archive,
)
from sales_backend.maintenance.feishu_test_archive import ArchiveRejected
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.request_metadata import RequestMetadata, request_metadata


async def run(args):
    descriptor = NotificationTestDescriptor.model_validate_json(Path(args.descriptor).read_text())
    if args.apply and not args.expected_plan:
        raise ArchiveRejected('APPLY_REQUIRES_DRY_RUN_DIGEST')
    database = Database(get_settings())
    await database.connect()
    metadata = request_metadata.set(RequestMetadata(request_id=str(uuid4()),
        user_agent='salegent-feishu-notification-test-archive'))
    try:
        async with database.connection() as connection:
            identity = await IdentityRepository().find_maintenance_administrator(
                connection, workspace_external_id=args.workspace, account_code=args.administrator)
        if identity is None:
            raise ArchiveRejected('EXISTING_ADMINISTRATOR_REQUIRED')
        async with database.transaction(identity.context) as connection:
            await connection.execute("SET LOCAL statement_timeout = '5s'")
            await connection.execute("SET LOCAL lock_timeout = '500ms'")
            result = await archive(connection, identity.context, descriptor,
                                   expected_plan=args.expected_plan if args.apply else None)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        request_metadata.reset(metadata)
        await database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--administrator', required=True)
    parser.add_argument('--descriptor', required=True,
                        help='local JSON file containing the reviewed seven-field descriptor')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan')
    args = parser.parse_args()
    try:
        asyncio.run(asyncio.wait_for(run(args), timeout=60))
    except ArchiveRejected as exc:
        print(json.dumps({'archived': False, 'code': str(exc)}, ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({'archived': False, 'code': 'ARCHIVE_CHECK_FAILED'}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
