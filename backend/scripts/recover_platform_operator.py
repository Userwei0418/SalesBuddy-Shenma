"""Owner maintenance only: rotate password, clear lost MFA and revoke all sessions.

No public reset endpoint. Keep the dedicated MFA keyring separately from database
backups. Losing the keyring requires verified operator recovery, never a bypass.
"""
import argparse
import asyncio
import getpass
import os

import asyncpg
from sales_backend.auth.passwords import encode_password
from sales_backend.db import normalize_database_url


async def recover(dsn, account, password, reason):
    if not 1 <= len(reason.strip()) <= 500:
        raise ValueError('必须记录恢复原因')
    encoded=encode_password(password)
    conn=await asyncpg.connect(normalize_database_url(dsn))
    try:
        async with conn.transaction():
            operator=await conn.fetchval('UPDATE control.operator SET password_hash=$2,mfa_secret=NULL,mfa_revision=mfa_revision+1,mfa_last_step=-1 WHERE account_code=upper($1) AND enabled RETURNING id',account,encoded)
            if operator is None:
                raise ValueError('没有对应的有效平台管理员')
            await conn.execute('UPDATE control.session SET revoked_at=clock_timestamp(),mfa_pending=NULL WHERE operator_id=$1 AND revoked_at IS NULL',operator)
            await conn.execute("INSERT INTO control.audit(operator_id,action,reason) VALUES($1,'platform.owner_recovery',$2)",operator,reason.strip())
    finally:
        await conn.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account',required=True)
    parser.add_argument('--reason',required=True)
    args=parser.parse_args()
    dsn=os.environ.get('PLATFORM_BOOTSTRAP_DATABASE_URL','')
    if not dsn:
        parser.error('必须指定安装维护数据库连接')
    password=getpass.getpass('恢复后新密码：')
    if password!=getpass.getpass('再次输入：'):
        parser.error('两次输入不一致')
    try:
        asyncio.run(recover(dsn,args.account,password,args.reason))
    except Exception:
        print('恢复未完成，请核对安装维护权限和账号；不要将连接或凭据写入日志。')
        return 1
    print('平台旧会话已撤销；下次登录必须绑定新的动态验证器。')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
