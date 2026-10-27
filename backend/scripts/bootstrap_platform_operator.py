"""Offline, owner-only platform bootstrap. No public signup and no password in argv.

PLATFORM_BOOTSTRAP_DATABASE_URL must point to the intended installation. This
command creates one operator only; it never resets an existing account, deploys
services or grants company data access. The password is entered interactively.
"""
import argparse
import asyncio
import getpass
import os
import re

import asyncpg

from sales_backend.auth.passwords import encode_password
from sales_backend.db import normalize_database_url


async def create_operator(dsn, account, name, password):
    account = account.strip().upper()
    if not re.fullmatch(r'[A-Z0-9][A-Z0-9_.-]{2,63}', account) or not 1 <= len(name.strip()) <= 100:
        raise ValueError('账号或姓名格式无效')
    encoded = encode_password(password)
    connection = await asyncpg.connect(normalize_database_url(dsn))
    try:
        async with connection.transaction():
            if not await connection.fetchval("SELECT has_table_privilege(current_user,'control.operator','INSERT')"):
                raise PermissionError('仅安装维护身份可创建首个平台账号')
            await connection.execute('INSERT INTO control.operator(account_code,display_name,password_hash) VALUES($1,$2,$3)',
                                     account, name.strip(), encoded)
    finally:
        await connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    dsn = os.environ.get('PLATFORM_BOOTSTRAP_DATABASE_URL', '')
    if not dsn:
        parser.error('必须显式配置 PLATFORM_BOOTSTRAP_DATABASE_URL')
    password = getpass.getpass('新平台管理员密码（8–128 位）：')
    if password != getpass.getpass('再次输入：'):
        parser.error('两次输入不一致')
    try:
        asyncio.run(create_operator(dsn, args.account, args.name, password))
    except Exception:
        print('创建失败：请核对目标数据库、安装维护权限及账号是否已存在；未重置已有账号。')
        return 1
    print('独立平台账号已创建；公司数据权限未授予。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
