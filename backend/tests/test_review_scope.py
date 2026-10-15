"""Scope identity/freeze boundaries; existing subject authorization stays shared."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.contracts.review_scope import ReviewResolvedScopeV2, ReviewScopeSelectionV2
from sales_backend.contracts.work_report_overview import OverviewResolvedScope
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.work_report import WorkReportError
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.services import review_scope as service

WS, USER, OTHER, TEAM_A, TEAM_B = [UUID(int=i) for i in range(1, 6)]
NOW = datetime(2026, 10, 14, 4, tzinfo=UTC)


def test_repeated_ids_and_legacy_aliases_have_one_canonical_serialization():
    repeated = ReviewScopeSelectionV2(scope="member", member_ids=[str(OTHER).upper(), USER, OTHER])
    assert repeated.member_ids == [USER, OTHER]
    assert ReviewScopeSelectionV2.model_validate(repeated.model_dump()) == repeated
    old = ReviewScopeSelectionV2(scope="team", team_id=TEAM_A)
    new = ReviewScopeSelectionV2(scope="team", team_ids=[TEAM_A], member_ids=[])
    assert old.model_dump() == new.model_dump()
    assert old.team_id is None
    assert ReviewScopeSelectionV2(scope="team", team_id=TEAM_A, team_ids=None) == old


@pytest.mark.parametrize("payload", [
    {"scope": "member"}, {"scope": "team", "team_ids": []},
    {"scope": "member", "member_ids": [USER], "team_ids": [TEAM_A]},
    {"scope": "member", "team_ids": [TEAM_A]}, {"scope": "self", "member_ids": [USER]},
    {"scope": "member", "member_id": USER, "member_ids": []},
    {"scope": "team", "team_id": TEAM_A, "member_ids": []},
    {"scope": "member", "member_id": USER, "team_id": TEAM_A},
    {"scope": "member", "member_ids": ["not-a-uuid"]},
    {"scope": "member", "member_ids": [USER] * 101},
    {"scope": "self", "workspace_id": str(WS)},
    {"scope": "self", "resolved_member_ids": [USER]},
    {"scope": "self", "expected_scope_revision": "old"},
])
def test_invalid_or_untrusted_selection_is_rejected(payload):
    with pytest.raises(ValidationError):
        ReviewScopeSelectionV2.model_validate(payload)


@pytest.fixture
def environment(monkeypatch):
    actor = ActorContext(workspace_id=str(WS), user_id=str(USER), role="operations", data_scope="workspace")
    state = dict(
        isolation="repeatable read", readonly="on", rls="on", workspace_id=str(WS), user_id=str(USER),
        role="operations", data_as_of=NOW,
    )
    connection = AsyncMock()
    connection.is_in_transaction = lambda: True
    connection.fetchrow.return_value = state
    connection.fetch.return_value = [
        dict(user_id=str(USER), team_id=str(TEAM_A), membership_role="sales"),
        dict(user_id=str(OTHER), team_id=str(TEAM_B), membership_role="sales"),
    ]
    snapshot = AsyncMock(return_value=dict(workspace_id=str(WS), user_id=str(USER), permission_version="rbac:1"))
    monkeypatch.setattr(AuthorizationRepository, "snapshot", snapshot)
    members = {TEAM_A: [USER, OTHER], TEAM_B: [OTHER]}
    labels = {USER: "本人名称", OTHER: "第二成员", TEAM_A: "甲团队", TEAM_B: "乙团队"}

    async def old_resolve(conn, current_actor, selection):
        assert conn is connection and current_actor is actor
        ident = selection.team_id if selection.kind == "team" else selection.member_id or USER
        return OverviewResolvedScope(**selection.model_dump(), label=labels[ident],
                                     member_ids=members[ident] if selection.kind == "team" else [ident])

    resolver = AsyncMock(side_effect=old_resolve)
    monkeypatch.setattr(service.access, "resolve_read_scope", resolver)
    return actor, connection, state, snapshot, resolver, members, labels


@pytest.mark.asyncio
async def test_union_authorizes_sorted_distinct_subjects_and_keeps_legacy_scopes_internal(environment):
    actor, connection, _, snapshot, resolver, _, _ = environment
    result = await service.resolve_review_scope(
        connection, actor, dict(scope="team", team_ids=[TEAM_B, TEAM_A, TEAM_B]))
    assert [call.args[2].team_id for call in resolver.await_args_list] == [TEAM_A, TEAM_B]
    assert result.scope.subject_ids == (TEAM_A, TEAM_B)
    assert result.scope.resolved_member_ids == (USER, OTHER)
    assert result.scope.label == "甲团队、乙团队" and result.scope.kind == "team"
    assert all(subject.scope_type == "team" for subject in result.scope.subjects)
    assert [legacy.team_id for legacy in result.legacy_scopes] == [TEAM_A, TEAM_B]
    assert result.data_as_of == NOW and result.permission_version == "rbac:1"
    public = result.freeze()
    assert "legacy_scopes" not in public and "member_ids" not in public
    assert ReviewResolvedScopeV2.model_validate(public) == result.scope
    assert snapshot.await_count == 1
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_key_and_revision_ignore_order_duplicates_labels_and_self_alias(environment):
    actor, connection, _, _, resolver, _, labels = environment
    first = await service.resolve_review_scope(connection, actor, dict(scope="team", team_ids=[TEAM_B, TEAM_A]))
    labels[TEAM_A] = "重命名"
    second = await service.resolve_review_scope(
        connection, actor, dict(scope="team", team_ids=[TEAM_A, TEAM_A, TEAM_B]))
    assert first.scope.label != second.scope.label
    assert first.scope.scope_key == second.scope.scope_key
    assert first.scope.scope_revision == second.scope.scope_revision
    myself = await service.resolve_review_scope(connection, actor, {"scope": "self"})
    explicit = await service.resolve_review_scope(connection, actor, {"scope": "member", "member_id": USER})
    assert myself.scope.kind == "self" and explicit.scope.kind == "member"
    assert myself.scope.scope_key == explicit.scope.scope_key
    assert myself.scope.scope_revision == explicit.scope.scope_revision
    assert resolver.await_count == 6  # Re-authorized on every call; no cache.


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["workspace_id", "user_id"])
async def test_identity_hash_is_bound_to_authoritative_workspace_and_actor(environment, identity):
    actor, connection, state, snapshot, resolver, _, _ = environment
    selection = dict(scope="member", member_ids=[USER])
    original = await service.resolve_review_scope(connection, actor, selection)
    replacement = str(UUID(int=100))
    changed_actor = actor.model_copy(update={identity: replacement})
    state[identity] = snapshot.return_value[identity] = replacement
    resolver.side_effect = None
    resolver.return_value = original.legacy_scopes[0]
    changed = await service.resolve_review_scope(connection, changed_actor, selection)
    assert changed.scope.subject_ids == original.scope.subject_ids
    assert changed.scope.scope_key != original.scope.scope_key
    assert changed.scope.scope_revision != original.scope.scope_revision


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("readonly", "off"), ("isolation", "read committed"), ("rls", "off"),
    ("workspace_id", str(OTHER)), ("user_id", str(OTHER)), ("role", "sales"),
])
async def test_read_snapshot_identity_cannot_be_bypassed(environment, field, value):
    actor, connection, state, snapshot, resolver, _, _ = environment
    state[field] = value
    with pytest.raises(WorkReportError) as failure:
        await service.resolve_review_scope(connection, actor, {"scope": "self"})
    assert failure.value.status == 403
    snapshot.assert_not_awaited()
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_autocommit_and_foreign_authorization_snapshot_are_rejected(environment):
    actor, connection, _, snapshot, resolver, _, _ = environment
    connection.is_in_transaction = lambda: False
    with pytest.raises(WorkReportError) as failure:
        await service.resolve_review_scope(connection, actor, {"scope": "self"})
    assert failure.value.status == 403
    connection.is_in_transaction = lambda: True
    snapshot.return_value["user_id"] = str(OTHER)
    with pytest.raises(WorkReportError) as failure:
        await service.resolve_review_scope(connection, actor, {"scope": "self"})
    assert failure.value.status == 403
    resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["permission", "members", "team_membership", "rules"])
async def test_live_revision_change_requires_refresh_but_preserves_identity(environment, monkeypatch, change):
    actor, connection, _, snapshot, _, members, _ = environment
    selection = dict(scope="team", team_ids=[TEAM_A])
    before = await service.resolve_review_scope(connection, actor, selection)
    if change == "permission":
        snapshot.return_value["permission_version"] = "rbac:2"
    elif change == "members":
        members[TEAM_A] = [USER]
    elif change == "team_membership":
        connection.fetch.return_value[0]["team_id"] = str(TEAM_B)
    else:
        monkeypatch.setattr(service, "SCOPE_RULES_VERSION", "review.scope.rules.v2.next")
    after = await service.resolve_review_scope(connection, actor, selection)
    assert before.scope.scope_key == after.scope.scope_key
    assert before.scope.scope_revision != after.scope.scope_revision
    with pytest.raises(WorkReportError) as failure:
        await service.resolve_review_scope(connection, actor,
                                           selection | {"expected_scope_revision": before.scope.scope_revision})
    assert failure.value.status == 409 and failure.value.code == "WORK_REPORT_SCOPE_CHANGED"


@pytest.mark.asyncio
async def test_one_revoked_subject_rejects_whole_union_before_stale_revision(environment):
    actor, connection, _, _, resolver, _, _ = environment
    resolver.side_effect = [
        OverviewResolvedScope(kind="team", team_id=TEAM_A, label="甲团队", member_ids=[USER]),
        WorkReportError("WORK_REPORT_SCOPE_CHANGED", "主体不可读", status=403),
    ]
    with pytest.raises(WorkReportError) as failure:
        await service.resolve_review_scope(connection, actor, dict(
            scope="team", team_ids=[TEAM_A, TEAM_B], expected_scope_revision="0" * 64,
        ))
    assert failure.value.status == 403 and resolver.await_count == 2
    connection.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_freeze_reentry_reauthorizes_and_rejects_tampered_or_changed_scope(environment):
    actor, connection, _, _, resolver, _, labels = environment
    first = await service.resolve_review_scope(connection, actor, {"scope": "self"})
    frozen = first.freeze()
    labels[USER] = "新显示名"
    again = await service.assert_review_scope(connection, actor, frozen)
    assert again.scope.scope_revision == first.scope.scope_revision
    assert again.scope.label == "新显示名" and resolver.await_count == 2
    frozen["subject_ids"] = [str(OTHER)]
    frozen["subjects"][0]["id"] = str(OTHER)
    with pytest.raises(WorkReportError) as failure:
        await service.assert_review_scope(connection, actor, frozen)
    assert failure.value.status == 409
    resolver.side_effect = WorkReportError("WORK_REPORT_SCOPE_CHANGED", "已撤权", status=403)
    with pytest.raises(WorkReportError) as failure:
        await service.assert_review_scope(connection, actor, first.freeze())
    assert failure.value.status == 403

@pytest.mark.asyncio
async def test_report_write_reauthorization_keeps_identity_guard_without_readonly_requirement(environment):
    actor, connection, *_ = environment
    connection.fetchrow.return_value.update(isolation='read committed', readonly='off')
    with pytest.raises(WorkReportError):
        await service.assert_read_context(connection, actor)
    assert await service.assert_read_context(connection, actor, facts_snapshot=False) == NOW
    connection.fetchrow.return_value['user_id'] = str(OTHER)
    with pytest.raises(WorkReportError):
        await service.assert_read_context(connection, actor, facts_snapshot=False)
