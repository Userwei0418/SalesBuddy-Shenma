"""Exercise v2 self admission with the existing offline repository witness.

These cases verify application checks, not PostgreSQL RLS or model execution.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from sales_backend.contracts.review_scope import ReviewResolvedScopeV2
from sales_backend.contracts.work_report import ShareTargets, SnapshotSelection
from sales_backend.domain.work_report import WorkReportError
from sales_backend.repositories.work_report import WorkReportRepository
from sales_backend.services import work_report_access
from tests.test_work_report_repository import (
    ACTOR,
    PERIOD,
    READER,
    FakeReportConnection,
    no_network,  # noqa: F401 -- reuse the existing no-network fixture
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("no_network")]


def resolved_self(user_id=ACTOR.user_id):
    return ReviewResolvedScopeV2(
        kind="self", label="本人", subject_ids=(UUID(user_id),),
        subjects=({"scope_type": "person", "id": user_id, "label": "本人"},),
        resolved_member_ids=(UUID(user_id),),
        scope_key="review-scope:v2:" + "a" * 64, scope_revision="b" * 64,
    )


def selection(revision="b" * 64):
    return SnapshotSelection(
        sources=["visit"], scope={"contract_version": "work_report.scope_selection.v2",
                                 "kind": "self", "expected_scope_revision": revision},
    )


async def test_v2_self_accepts_the_authenticated_subject_without_client_member_ids():
    connection = FakeReportConnection()
    result = await WorkReportRepository().create_snapshot(
        connection, ACTOR, selection(), period=PERIOD, scope=resolved_self(),
    )
    assert result.status == "preparing"
    assert result.scope.subject_ids == (UUID(ACTOR.user_id),)
    stored = connection.rows["snapshot"][str(result.id)]
    assert stored["owner_user_ref_id"] == ACTOR.user_id
    assert stored["selection"]["scope"]["member_ids"] == []


@pytest.mark.parametrize("fault", ["other_subject", "changed_revision"])
async def test_v2_self_rejects_forged_subject_and_changed_scope_revision_before_insert(fault):
    connection = FakeReportConnection()
    scope = resolved_self(READER.user_id if fault == "other_subject" else ACTOR.user_id)
    request = selection("c" * 64 if fault == "changed_revision" else "b" * 64)
    with pytest.raises(WorkReportError) as failure:
        await WorkReportRepository().create_snapshot(
            connection, ACTOR, request, period=PERIOD, scope=scope,
        )
    assert failure.value.code == "WORK_REPORT_PERMISSION_CHANGED"
    assert failure.value.status == 403
    assert not connection.rows["snapshot"] and not connection.events


@pytest.mark.parametrize("permission", ["work_report.read", "work_report.write", "visit.read"])
async def test_v2_self_does_not_bypass_report_or_selected_source_permissions(permission):
    connection = FakeReportConnection()
    connection.denied.add(permission)
    with pytest.raises(WorkReportError) as failure:
        await WorkReportRepository().create_snapshot(
            connection, ACTOR, selection(), period=PERIOD, scope=resolved_self(),
        )
    assert failure.value.code == "WORK_REPORT_PERMISSION_CHANGED"
    assert not connection.rows["snapshot"] and not connection.events


@pytest.mark.parametrize("members,expected", [
    ([ACTOR.user_id], ["self"]),
    ([READER.user_id], ["member"]),
    ([ACTOR.user_id, READER.user_id], ["self", "member"]),
])
async def test_generation_options_preserve_every_already_authorized_member(monkeypatch, members, expected):
    grants = {"work_report.read", "work_report.write", "agent.work_report", "visit.read"}
    effective = SimpleNamespace(allows=lambda permission: permission in grants,
                                require=lambda permission: None)
    monkeypatch.setattr(work_report_access.AuthorizationRepository, "effective",
                        AsyncMock(return_value=effective))
    member_query = AsyncMock(return_value=[{"id": uid, "name": "已授权成员"} for uid in members])
    monkeypatch.setattr(work_report_access, "_members", member_query)
    monkeypatch.setattr(work_report_access, "_teams", AsyncMock(return_value=[]))
    monkeypatch.setattr(work_report_access, "share_targets",
                        AsyncMock(return_value=ShareTargets(allowed_kinds=[], members=[], teams=[])))
    connection = FakeReportConnection()
    result = await work_report_access.report_options(connection, ACTOR)
    assert result.available_scope_kinds == expected
    assert [str(member.id) for member in result.members] == members
    member_query.assert_awaited_once_with(connection, ACTOR, "work_report.read", write=True)
