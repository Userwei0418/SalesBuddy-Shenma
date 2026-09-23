from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.collaboration import archive_fde_collaboration
from sales_backend.services.visit_access import validate_visit_attendance

FDE = "10000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_visit_roster_union_reads_after_lock(monkeypatch):
    """A concurrent archive adds C while this visit is waiting to append B."""
    db = SimpleNamespace(fetchval=AsyncMock())
    actor = SimpleNamespace(role=RoleCode.SALES)
    states = []

    async def effective(*args):
        states.append("read")
        return {"A"} if len(states) == 1 else {"A", "C"}

    async def lock(*args):
        states.append("lock")

    write = AsyncMock()
    prefix = "sales_backend.repositories.collaboration."
    monkeypatch.setattr(prefix + "effective_ids", effective)
    monkeypatch.setattr(prefix + "manageable_version", AsyncMock(return_value=2))
    monkeypatch.setattr(prefix + "bump_membership_version", lock)
    monkeypatch.setattr(prefix + "write_members", write)
    await archive_fde_collaboration(
        db, actor, {"id": "visit", "opportunity_id": "o", "customer_id": "c", "customer_name": "客户"}, ["B"]
    )
    assert states == ["read", "lock", "read"]
    assert write.await_args.args[3] == {"A", "B", "C"}


@pytest.mark.asyncio
async def test_empty_attendance_never_clears_members(monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr("sales_backend.repositories.collaboration.write_members", write)
    await archive_fde_collaboration(None, None, {"opportunity_id": "o"}, [])
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_attendance_requires_opportunity_and_rejects_legacy_roster(monkeypatch):
    actor = SimpleNamespace(role=RoleCode.SALES)
    validate = AsyncMock(return_value=[FDE])
    monkeypatch.setattr("sales_backend.services.visit_access.validated_fde", validate)
    with pytest.raises(ValueError, match="先关联商机"):
        await validate_visit_attendance(None, actor, [FDE], None, None)
    with pytest.raises(ValueError, match="仅选择本次"):
        await validate_visit_attendance(None, actor, [], "o", {"fde_member_ids": []})
    validate.assert_not_awaited()
    assert await validate_visit_attendance(None, actor, [FDE, FDE], "o", None) == [FDE]
    assert await validate_visit_attendance(None, actor, [FDE], None, {"action": "create"}) == [FDE]


@pytest.mark.asyncio
async def test_fde_cannot_register_other_attendees():
    actor = SimpleNamespace(role=RoleCode.FDE, user_id="other")
    with pytest.raises(PermissionError, match="不能代其他"):
        await validate_visit_attendance(None, actor, [FDE], "o", None)
