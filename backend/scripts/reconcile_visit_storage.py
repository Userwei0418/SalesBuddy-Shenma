"""Inspect/recover one retained original in an explicitly selected installation."""
import argparse
import asyncio
import json
import os
from uuid import UUID

import asyncpg

from sales_backend.db import normalize_database_url, _initialize_connection
from sales_backend.services.material_storage import MaterialStorage
from sales_backend.services.storage_reconciliation import reconcile, list_pending


async def run(args,dsn):
    connection=await asyncpg.connect(normalize_database_url(dsn))
    try:
        await _initialize_connection(connection)
        if await connection.fetchval('SELECT current_database()')!=args.database:
            raise ValueError('目标数据库名称不一致')
        if args.action=='list': return await list_pending(connection,args.workspace,args.after)
        return await reconcile(connection,MaterialStorage(),args.staging_root,args.workspace,args.import_id,args.action,args.reason)
    finally: await connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--workspace',required=True,type=UUID)
    parser.add_argument('--import-id',type=UUID)
    parser.add_argument('--after',type=UUID)
    parser.add_argument('--staging-root',required=True)
    parser.add_argument('--action',choices=['list','inspect','recover','release-unpublished'],default='inspect')
    parser.add_argument('--reason',default='')
    args=parser.parse_args()
    if args.action!='list' and not args.import_id: parser.error('此动作需要 --import-id')
    dsn=os.environ.get('STORAGE_RECONCILE_DATABASE_URL','')
    if not dsn: parser.error('必须显式配置 STORAGE_RECONCILE_DATABASE_URL（安装维护身份）')
    try: print(json.dumps(asyncio.run(run(args,dsn)),ensure_ascii=False))
    except Exception as exc:
        # Driver errors may carry SQL parameters or credentials: never print
        # their raw message into the handoff/console logs.
        print(json.dumps({'outcome':'not_reconciled','error_type':type(exc).__name__,
            'message':'对账未完成，原件和额度保留。请核对目标环境、权限、原件完整性及当前状态。'},ensure_ascii=False))
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
