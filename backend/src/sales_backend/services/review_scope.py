"""Resolve every selected subject in one authorized read-only RR snapshot.

Scope identity is not a permission credential or a facts snapshot. Callers must
still enforce each source's grants; future generation must also enforce writes.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sales_backend.contracts.review_scope import (
    ReviewResolvedScopeV2,
    ReviewScopeSelectionV2,
    ReviewScopeSubjectV2,
)
from sales_backend.contracts.work_report_overview import OverviewResolvedScope, OverviewScopeSelection
from sales_backend.domain.work_report import WorkReportError
from sales_backend.services import work_report_access as access

SCOPE_RULES_VERSION = "review.scope.rules.v2.1"


@dataclass(frozen=True)
class ReviewScopeResolution:
    scope: ReviewResolvedScopeV2
    legacy_scopes: tuple[OverviewResolvedScope, ...]
    permission_version: str
    data_as_of: datetime

    def freeze(self):
        """Detached scope payload for same-transaction facts; no legacy internals."""
        return self.scope.model_dump(mode="json")


def _digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def assert_read_context(connection, actor, *, facts_snapshot=True):
    if not connection.is_in_transaction():
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "范围解析需要显式只读可重复读事务", status=403)
    row = await connection.fetchrow(
        "SELECT current_setting('transaction_isolation') AS isolation,"
        "current_setting('row_security') AS rls,current_setting('transaction_read_only') AS readonly,"
        "common.current_workspace_id()::text AS workspace_id,common.current_user_ref_id()::text AS user_id,"
        "common.current_role_code() AS role,transaction_timestamp() AS data_as_of"
    )
    expected = dict(rls="on", workspace_id=actor.workspace_id,
                    user_id=actor.user_id, role=actor.role.value)
    if facts_snapshot:
        expected.update(isolation="repeatable read", readonly="on")
    if not row or {key: row[key] for key in expected} != expected:
        raise WorkReportError("WORK_REPORT_PERMISSION_CHANGED", "范围解析事务与当前用户身份不一致", status=403)
    return row["data_as_of"]


async def _memberships(connection, actor, member_ids, data_as_of):
    # Only internal revision material: never expose a new membership directory.
    # Table RLS still applies; these are members already resolved by old grants.
    rows = await connection.fetch(
        """SELECT DISTINCT tm.user_ref_id::text AS user_id,tm.team_id::text AS team_id,tm.membership_role
          FROM platform.team_membership tm
          JOIN platform.user_ref u ON u.id=tm.user_ref_id AND u.workspace_id=tm.workspace_id
          JOIN platform.team t ON t.id=tm.team_id AND t.workspace_id=tm.workspace_id
          WHERE tm.workspace_id=$1::uuid AND tm.user_ref_id=ANY($2::uuid[])
          AND u.status='active' AND u.deleted_at IS NULL
          AND t.status='active' AND t.deleted_at IS NULL
          AND $3::timestamptz>=tm.valid_from AND $3::timestamptz<tm.valid_to
          AND $3::timestamptz>=t.valid_from AND $3::timestamptz<t.valid_to
          AND security.authorization_subject('work_report.read','person',u.id,NULL)
          ORDER BY user_id,team_id,tm.membership_role""",
        actor.workspace_id, list(member_ids), data_as_of,
    )
    return sorted({(str(UUID(row["user_id"])), str(UUID(row["team_id"])), row["membership_role"]) for row in rows})


async def resolve_review_scope(connection, actor, selection, *, facts_snapshot=True) -> ReviewScopeResolution:
    # Work-report snapshots use the public ``kind`` selector while the
    # read-only overview API retains its historical ``scope`` field.
    if isinstance(selection, dict) and selection.get("contract_version") == "work_report.scope_selection.v2":
        from sales_backend.contracts.review_scope import WorkReportScopeSelectionV2
        selection = WorkReportScopeSelectionV2.model_validate(selection).review_selection()
    elif getattr(selection, "contract_version", None) == "work_report.scope_selection.v2":
        selection = selection.review_selection()
    selection = ReviewScopeSelectionV2.model_validate(selection)
    data_as_of = await assert_read_context(connection, actor, facts_snapshot=facts_snapshot)
    # This old helper also verifies the snapshot's workspace/user identity.
    permission_version = await access.permission_version(connection, actor)
    subject_type = "team" if selection.scope == "team" else "person"
    ids = (selection.team_ids if subject_type == "team" else selection.member_ids)
    ids = ids if selection.scope != "self" else [UUID(actor.user_id)]
    legacy, subjects = [], []
    for ident in ids:
        query = OverviewScopeSelection(
            kind=selection.scope,
            member_id=ident if selection.scope == "member" else None,
            team_id=ident if selection.scope == "team" else None,
        )
        # Any forbidden, inactive, foreign or non-leaf subject aborts the union.
        resolved = await access.resolve_read_scope(connection, actor, query)
        legacy.append(resolved)
        subjects.append(ReviewScopeSubjectV2(scope_type=subject_type, id=ident, label=resolved.label))
    member_ids = tuple(sorted({uid for scope in legacy for uid in scope.member_ids}, key=str))
    memberships = await _memberships(connection, actor, member_ids, data_as_of)
    scope_key = "review-scope:v2:" + _digest(dict(
        version=2, workspace_id=str(UUID(actor.workspace_id)), actor_id=str(UUID(actor.user_id)),
        subject_type=subject_type, subject_ids=[str(ident) for ident in ids],
    ))
    by_subject = []
    for ident, resolved in zip(ids, legacy, strict=True):
        members = sorted({str(uid) for uid in resolved.member_ids})
        by_subject.append(dict(subject_id=str(ident), member_ids=members,
                               memberships=[list(row) for row in memberships if row[0] in members]))
    revision = _digest(dict(scope_key=scope_key, permission_version=permission_version,
                            scope_rules_version=SCOPE_RULES_VERSION, subjects=by_subject))
    if selection.expected_scope_revision is not None and selection.expected_scope_revision != revision:
        raise WorkReportError("WORK_REPORT_SCOPE_CHANGED", "所选范围或授权已变化，请重新取材", status=409)
    scope = ReviewResolvedScopeV2(
        kind=selection.scope, label="、".join(subject.label for subject in subjects),
        subject_ids=tuple(ids), subjects=tuple(subjects), resolved_member_ids=member_ids,
        scope_key=scope_key, scope_revision=revision,
    )
    return ReviewScopeResolution(scope, tuple(legacy), permission_version, data_as_of)


async def assert_review_scope(connection, actor, frozen) -> ReviewScopeResolution:
    """Re-authorize all frozen subjects; never restore authority from its hash."""
    scope = ReviewResolvedScopeV2.model_validate(frozen)
    selection = dict(scope=scope.kind, expected_scope_revision=scope.scope_revision)
    if scope.kind != "self":
        selection["team_ids" if scope.kind == "team" else "member_ids"] = list(scope.subject_ids)
    current = await resolve_review_scope(connection, actor, selection, facts_snapshot=False)
    if (
        current.scope.scope_key != scope.scope_key
        or current.scope.subject_ids != scope.subject_ids
        or current.scope.resolved_member_ids != scope.resolved_member_ids
    ):
        raise WorkReportError("WORK_REPORT_SCOPE_CHANGED", "冻结范围与当前授权范围不一致", status=409)
    return current
