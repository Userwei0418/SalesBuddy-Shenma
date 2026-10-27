"""Borrow the fixture's real PostgreSQL connection without owning its transaction.

Handlers exercise Database's awaitable acquire/release path. The fixture owns
the outer rollback transaction, so release only ends this serialized lease: it
must not reset, close or silently recover the underlying PostgreSQL connection.
"""
import asyncio
import re


class FixtureConnectionPool:
    def __init__(self, connection):
        self.connection = connection
        self._lease = asyncio.Lock()

    async def acquire(self, *, timeout=None):
        async with asyncio.timeout(timeout):
            await self._lease.acquire()
        return self.connection

    async def release(self, connection, *, timeout=None):
        if connection is not self.connection or not self._lease.locked():
            raise ValueError('Connection is not leased by this fixture pool')
        # No I/O is required, so the release completes within any positive
        # timeout. Database passes its ordinary bounded release policy here.
        self._lease.release()


class ScopedRolePool:
    """Use a real pool under the disposable integration runner's restricted role."""

    def __init__(self, pool, role):
        if not re.fullmatch(r"salegent_verify_role_[a-f0-9]+", role):
            raise ValueError("Expected a disposable integration role")
        self.pool = pool
        self.role = role

    async def acquire(self, *, timeout=None):
        connection = await self.pool.acquire(timeout=timeout)
        try:
            await connection.execute(f'SET ROLE "{self.role}"')
        except BaseException:
            await self.pool.release(connection, timeout=5)
            raise
        return connection

    async def release(self, connection, *, timeout=None):
        try:
            async with asyncio.timeout(timeout):
                await connection.execute("RESET ROLE")
        finally:
            # asyncpg resets/discards the real connection when returning it.
            # Even a failed RESET must release the lease and keep its error.
            await self.pool.release(connection, timeout=timeout)
