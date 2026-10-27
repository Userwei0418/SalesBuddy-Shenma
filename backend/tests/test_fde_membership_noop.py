"""A roster that looked unchanged must still pass the database's locked check."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest

from sales_backend.services import collaboration


@pytest.mark.asyncio
async def test_apparent_noop_does_not_skip_a_concurrent_version_conflict(monkeypatch):
    member = "10000000-0000-0000-0000-000000000001"
    monkeypatch.setattr(collaboration, "manageable_version", AsyncMock(return_value=7))
    monkeypatch.setattr(collaboration, "effective_ids", AsyncMock(return_value={member}))
    monkeypatch.setattr(collaboration, "validated_fde", AsyncMock(return_value=[{"id": member}]))
    result_members = AsyncMock()
    monkeypatch.setattr(collaboration, "members_by_opportunity", result_members)
    connection = SimpleNamespace(fetchval=AsyncMock(side_effect=[
        None,  # No primary FDE prevents this otherwise valid request.
        asyncpg.SerializationError("another request advanced the version after the initial reads"),
    ]))
    with pytest.raises(asyncpg.SerializationError):
        await collaboration.set_opportunity_members(connection, None, "opportunity", [member], 7)
    result_members.assert_not_awaited()
