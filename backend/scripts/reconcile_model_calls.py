"""Inspect uncertain calls and settle verified accounting; never invoke models."""
import argparse
import asyncio
from datetime import datetime
import json
import os
from uuid import UUID

import asyncpg

from sales_backend.db import _initialize_connection, normalize_database_url
from sales_backend.services.call_reconciliation import pending, reconcile


async def run(args, dsn):
    connection = await asyncpg.connect(normalize_database_url(dsn))
    try:
        await _initialize_connection(connection)
        if await connection.fetchval('SELECT current_database()')!=args.database:
            raise ValueError('目标数据库名称不一致')
        if args.action=='list': return await pending(connection,args.workspace,args.after)
        return await reconcile(connection,args.workspace,args.invocation_id,args.action,
            expected_at=args.expected_at,reason=args.reason,evidence=args.evidence_ref,http_status=args.http_status)
    finally: await connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True)
    parser.add_argument('--workspace',required=True,type=UUID)
    parser.add_argument('--action',choices=['list','release-unstarted','confirm-not-sent','confirm-response'],default='list')
    parser.add_argument('--after',type=UUID)
    parser.add_argument('--invocation-id',type=UUID)
    parser.add_argument('--expected-at',type=datetime.fromisoformat)
    parser.add_argument('--reason',default='')
    parser.add_argument('--evidence-ref',default='')
    parser.add_argument('--http-status',type=int)
    args=parser.parse_args()
    if args.action!='list' and (not args.invocation_id or not args.expected_at or args.expected_at.tzinfo is None):
        parser.error('对账需指定 --invocation-id 和含时区的 --expected-at')
    dsn=os.environ.get('CALL_RECONCILE_DATABASE_URL','')
    if not dsn: parser.error('必须显式配置 CALL_RECONCILE_DATABASE_URL（安装维护身份）')
    try: print(json.dumps(asyncio.run(run(args,dsn)),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'outcome':'not_reconciled','error_type':type(exc).__name__,
            'message':'对账未完成，额度保留。请核对目标、权限、状态及证据。'},ensure_ascii=False))
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
