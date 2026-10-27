from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.domain.agent import RoleCode
from sales_backend.repositories.collaboration import archive_fde_collaboration
from sales_backend.services.visit_access import validate_visit_attendance
from tests.authorization_fixtures import permission_snapshot

FDE = "10000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
async def test_visit_attendance_never_changes_long_term_roster_or_its_version(monkeypatch):
    """A one-off attendee is not a new persistent opportunity member."""
    db = SimpleNamespace(fetchval=AsyncMock())
    actor = SimpleNamespace(role=RoleCode.SALES, user_id="sales", workspace_id="w", team_ids=())
    effective, lock = AsyncMock(), AsyncMock()
    write = AsyncMock()
    prefix = "sales_backend.repositories.collaboration."
    monkeypatch.setattr(prefix + "effective_ids", effective)
    monkeypatch.setattr(prefix + "manageable_version", AsyncMock(return_value=2))
    monkeypatch.setattr(prefix + "bump_membership_version", lock)
    monkeypatch.setattr(prefix + "write_members", write)
    await archive_fde_collaboration(
        db, actor, {"id": "visit", "opportunity_id": "o", "customer_id": "c", "customer_name": "客户"}, ["B"]
    )
    effective.assert_not_awaited()
    lock.assert_not_awaited()
    write.assert_not_awaited()
    db.fetchval.assert_awaited_once()
    assert "enqueue_fde_collaboration_notification" in db.fetchval.await_args.args[0]


@pytest.mark.asyncio
async def test_empty_attendance_never_clears_members(monkeypatch):
    write = AsyncMock()
    monkeypatch.setattr("sales_backend.repositories.collaboration.write_members", write)
    await archive_fde_collaboration(None, None, {"opportunity_id": "o"}, [])
    write.assert_not_awaited()


@pytest.mark.asyncio
async def test_opportunity_attendance_preserves_permissions_and_rejects_legacy_roster(monkeypatch):
    actor = SimpleNamespace(role=RoleCode.SALES, user_id="sales", workspace_id="w", team_ids=())
    async def permission(sql, *args):
        if "authorization_snapshot" in sql:
            return permission_snapshot(actor, {"visit.attendance_manage": "self"})
        assert "authorization_opportunity" in sql
        return True
    connection=SimpleNamespace(fetchval=AsyncMock(side_effect=permission))
    validate = AsyncMock(return_value=[FDE])
    monkeypatch.setattr("sales_backend.services.visit_access.validated_fde", validate)
    with pytest.raises(ValueError, match="仅选择本次"):
        await validate_visit_attendance(connection, actor, [], "o", {"fde_member_ids": []}, customer_id="c")
    validate.assert_not_awaited()
    assert await validate_visit_attendance(connection, actor, [FDE, FDE], "o", None, customer_id="c") == [FDE]
    assert await validate_visit_attendance(connection, actor, [FDE], None, {"action": "create"}, customer_id="c") == [FDE]


@pytest.mark.asyncio
async def test_customer_attendance_checks_customer_scope_and_validates_fde(monkeypatch):
    actor = SimpleNamespace(user_id="sales", team_ids=("team",))
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=True))
    validate = AsyncMock(return_value=[FDE])
    monkeypatch.setattr("sales_backend.services.visit_access.validated_fde", validate)
    assert await validate_visit_attendance(
        connection, actor, [FDE, FDE], None, None, customer_id="customer"
    ) == [FDE]
    sql, *args = connection.fetchval.await_args.args
    assert "authorization_visit_target" in sql
    assert args == ["visit.attendance_manage", "customer", None, "sales", "team"]
    validate.assert_awaited_once_with(connection, [FDE])


@pytest.mark.asyncio
async def test_customer_attendance_denies_out_of_scope_before_resolving_people(monkeypatch):
    actor = SimpleNamespace(user_id="sales", team_ids=())
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=False))
    validate = AsyncMock()
    monkeypatch.setattr("sales_backend.services.visit_access.validated_fde", validate)
    with pytest.raises(PermissionError, match="授权范围"):
        await validate_visit_attendance(connection, actor, [FDE], None, None, customer_id="denied")
    assert connection.fetchval.await_args.args[1:] == ("visit.attendance_manage", "denied", None, "sales", None)
    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_customer_attendance_rejects_invalid_fde(monkeypatch):
    actor = SimpleNamespace(user_id="sales", team_ids=())
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=True))
    monkeypatch.setattr("sales_backend.services.visit_access.validated_fde", AsyncMock(side_effect=ValueError("FDE已失效")))
    with pytest.raises(ValueError, match="FDE"):
        await validate_visit_attendance(connection, actor, [FDE], None, None, customer_id="c")


@pytest.mark.asyncio
@pytest.mark.parametrize("participants", [[], [FDE]])
async def test_customer_self_or_empty_attendance_does_not_require_managing_others(monkeypatch, participants):
    actor = SimpleNamespace(user_id=FDE, team_ids=())
    connection = SimpleNamespace(fetchval=AsyncMock(return_value=False))
    validate = AsyncMock()
    monkeypatch.setattr("sales_backend.services.visit_access.validated_fde", validate)
    assert await validate_visit_attendance(connection, actor, participants, None, None, customer_id="c") == participants
    connection.fetchval.assert_not_awaited()
    assert validate.await_count == bool(participants)


@pytest.mark.asyncio
async def test_customer_attendance_does_not_create_opportunity_collaboration(monkeypatch):
    connection = SimpleNamespace(fetchval=AsyncMock())
    write = AsyncMock()
    monkeypatch.setattr("sales_backend.repositories.collaboration.write_members", write)
    await archive_fde_collaboration(connection, None, {"id": "v", "opportunity_id": None}, [FDE])
    write.assert_not_awaited()
    connection.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_fde_cannot_register_other_attendees():
    actor = SimpleNamespace(role=RoleCode.FDE, user_id="other")
    connection=SimpleNamespace(fetchval=AsyncMock(return_value=False))
    with pytest.raises(PermissionError, match="授权"):
        await validate_visit_attendance(connection, actor, [FDE], "o", None, customer_id="c")
