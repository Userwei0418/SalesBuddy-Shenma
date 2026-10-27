from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "database/migrations/V263__task_reviewer_handover_requires_confirmation.sql"
)


def test_reviewer_handover_removes_owner_self_review_shortcut():
    sql = MIGRATION.read_text(encoding="utf-8")

    # The creator/owner may use the self-review shortcut only while they are
    # still the current reviewer. After a reviewer handover, requirement
    # changes and completion must use the two-party confirmation path.
    assert "self_task:=a=owner_id AND owner_id=t.creator_user_ref_id AND reviewer_id=owner_id" in sql
    assert "self_task:=a=owner_id AND owner_id=t.creator_user_ref_id AND NOT" not in sql
