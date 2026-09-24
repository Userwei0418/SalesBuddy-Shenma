import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from sales_backend.contracts.visit_flow import archival_snapshot
from sales_backend.domain.agent import RoleCode
from sales_backend.domain.company_rules import VisitAdmissionPolicy
from sales_backend.domain.visit_review import review_text
from sales_backend.services.visit_review_gate import consume_review
from tests.authorization_fixtures import visit_authorization_query

FIELDS = {
    "_quality_review_run_id": "11111111-1111-1111-1111-111111111111",
    "visit_goal": "确认试点需求",
    "follow_up_record": "拜访内容",
    "next_action": "9月10日提交方案",
    "_follow_up_quality_score": 100,
}
ACTOR = SimpleNamespace(workspace_id="workspace", user_id="user", role=RoleCode.SALES, team_ids=(),
                        model_dump=lambda **kwargs: {"workspace_id": "workspace", "user_id": "user"})


def connection(**changes):
    policy = {"id": "current", "definition": VisitAdmissionPolicy().model_dump()}
    payload = {"quality_review": {"follow_up_score": 75, "next_action": {"passed": True}}}
    payload.update(changes.pop("payload", {}))
    payload.update(visit_stage="quality", company_policy=policy)
    row = {
        "id": "artifact",
        "identity_context": {"permission_version": "rbac-test:1"},
        "status": "pending_confirm",
        "text_content": review_text(FIELDS),
        "payload": payload,
        "business_context": {
            "customer_id": "customer",
            "visit_request": {**archival_snapshot(FIELDS), "stage": "quality"},
        },
    }
    row.update(changes)

    async def read(sql, *args):
        if "config.rule_set" in sql:
            return {
                "id": "current",
                "definition": policy["definition"],
                "rule_code": "visit_admission",
                "version_no": 1,
                "workspace_id": None,
            }
        return row

    return SimpleNamespace(fetchrow=AsyncMock(side_effect=read), execute=AsyncMock(), row=row,
                           fetchval=AsyncMock(side_effect=visit_authorization_query(ACTOR)))


def test_uses_stored_score_and_records_confirmation():
    db = connection()
    fields, artifact = asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))
    assert fields["_follow_up_quality_score"] == 75
    assert artifact == "artifact"
    assert db.execute.await_count == 2
    assert db.fetchrow.call_args_list[0].args[1:] == (FIELDS["_quality_review_run_id"], "workspace", "user")


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "applied"},
        {"business_context": {}},
        {"payload": {"quality_review": {"follow_up_score": 60}}},
        {"payload": {"quality_review": {"follow_up_score": 100, "next_action": {"passed": False}}}},
    ],
)
def test_rejects_replay_changed_content_and_failed_review(changes):
    db = connection(**changes)
    with pytest.raises(ValueError):
        asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))
    db.execute.assert_not_awaited()


def test_rejects_missing_or_unauthorized_review():
    db = connection()
    db.fetchrow.side_effect = None
    db.fetchrow.return_value = None
    with pytest.raises(ValueError):
        asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))
    with pytest.raises(ValueError):
        asyncio.run(consume_review(db, ACTOR, "customer", {}))
    db.execute.assert_not_awaited()


@pytest.mark.parametrize(
    "changed_fields",
    [
        {"follow_up_record": "拜访"},
        {"next_action": "提交方案"},
        {"follow_up_record": FIELDS["next_action"], "next_action": FIELDS["follow_up_record"]},
    ],
)
def test_rejects_substrings_and_swapped_fields(changed_fields):
    db = connection()
    with pytest.raises(ValueError, match="修改|独立质检"):
        asyncio.run(consume_review(db, ACTOR, "customer", {**FIELDS, **changed_fields}))
    db.execute.assert_not_awaited()


def test_raw_transcription_is_only_evidence_not_the_reviewed_snapshot():
    db = connection(text_content="最初原文")
    fields, _ = asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))
    assert fields["_follow_up_quality_score"] == 75


def test_accepts_worker_top_level_next_action_result():
    db = connection(payload={"quality_review": {"follow_up_score": 75}, "next_action": {"passed": True}})
    fields, _ = asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))
    assert fields["_follow_up_quality_score"] == 75


def test_first_extraction_never_grants_archive_permission():
    db = connection()
    db.row["business_context"]["visit_request"]["stage"] = "structure"
    with pytest.raises(ValueError, match="独立质检"):
        asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))
    db.execute.assert_not_awaited()


def test_legacy_extraction_cannot_reuse_score_even_with_same_fields():
    db = connection(business_context={"customer_id": "customer"})
    with pytest.raises(ValueError, match="独立质检"):
        asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))


@pytest.mark.parametrize(
    "quality,legacy",
    [
        ({"follow_up_score": 90, "next_action": {"passed": False}, "next_action_passed": True}, {"passed": True}),
        ({"follow_up_score": 90, "next_action_passed": False}, {"passed": True}),
    ],
)
def test_legacy_success_cannot_override_explicit_current_review_rejection(quality, legacy):
    db = connection(payload={"quality_review": quality, "next_action": legacy})
    with pytest.raises(ValueError, match="下一步审核未通过"):
        asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))
    db.execute.assert_not_awaited()


def test_permission_change_blocks_archive_without_confirmation_writes():
    db = connection()
    db.row["identity_context"]["permission_version"] = "rbac-test:old"
    with pytest.raises(PermissionError, match="权限已变化"):
        asyncio.run(consume_review(db, ACTOR, "customer", FIELDS))
    db.execute.assert_not_awaited()
