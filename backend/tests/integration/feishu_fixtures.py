"""Explicit owner-only synthetic seeds; runtime assertions keep the caller's role.

Never use these helpers with a business database. Every elevation is limited to
a runner-created disposable database and a savepoint, and restores the original
role before returning (including when a seed statement raises).
"""
from contextlib import asynccontextmanager

import pytest


@asynccontextmanager
async def fixture_owner(connection):
    database, role, owner = await connection.fetchrow(
        "SELECT current_database(),current_user,"
        "(SELECT rolsuper FROM pg_roles WHERE rolname=session_user)"
    )
    if not database.startswith(("salegent_verify_integration_", "feishu_archive_")) or not owner:
        pytest.skip("Feishu owner seeds require a disposable test database and explicit owner session")
    try:
        async with connection.transaction():
            await connection.execute("RESET ROLE")
            yield connection
    finally:
        # Names originate from pg_roles, never from supplied SQL or business data.
        await connection.execute('SET LOCAL ROLE "' + role.replace('"', '""') + '"')


async def seed_execute(connection, statement, *args):
    async with fixture_owner(connection):
        return await connection.execute(statement, *args)


async def seed_fetchval(connection, statement, *args):
    async with fixture_owner(connection):
        return await connection.fetchval(statement, *args)


async def seed_fetchrow(connection, statement, *args):
    async with fixture_owner(connection):
        return await connection.fetchrow(statement, *args)


async def assert_restricted(connection):
    assert not await connection.fetchval(
        "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
    )
