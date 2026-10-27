"""Startup remains fail-closed after admitting the two readonly helpers."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.feishu_worker import verify_worker_role


@pytest.mark.asyncio
async def test_non_elevated_worker_with_only_allowed_functions_starts():
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=[True, False]))
    await verify_worker_role(connection)


@pytest.mark.asyncio
async def test_worker_still_rejects_elevated_or_business_writing_role():
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=False))
    with pytest.raises(RuntimeError, match='Dedicated non-elevated'):
        await verify_worker_role(connection)
    assert connection.fetchval.await_count == 1


@pytest.mark.asyncio
async def test_worker_still_rejects_an_unapproved_security_definer():
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=[True, True]))
    with pytest.raises(RuntimeError, match='Dedicated non-elevated'):
        await verify_worker_role(connection)
