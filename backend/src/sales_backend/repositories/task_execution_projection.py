"""Shared task responsibility and action projection; feedback never implies state."""

from sales_backend.domain.task_outcome import task_end_reason

TASK_RESPONSIBILITY_SQL = """(t.execution_mode IN ('execution_v1','execution_v2','execution_v3') AND EXISTS(
    SELECT 1 FROM workflow.task_execution x WHERE x.task_id=t.id AND (
    x.state->>'current_reviewer'=common.current_user_ref_id()::text
    OR (x.state->'execution_handover'->>'status'='pending'
        AND x.state->'execution_handover'->>'target_user_id'=common.current_user_ref_id()::text)
    OR (x.state->'reviewer_handover'->>'status'='pending'
        AND x.state->'reviewer_handover'->>'target_user_id'=common.current_user_ref_id()::text))))"""

# Count only currently actionable responsibilities, including pending confirmations.
# RLS still bounds the task set and each capability checks current scoped grants.
TASK_REQUIRES_ACTION_SQL = """CASE WHEN t.execution_mode='legacy' THEN
    CASE WHEN t.status='pending_review' THEN t.creator_user_ref_id=common.current_user_ref_id()
      AND security.authorization_task('task.review',t.id)
    ELSE EXISTS(SELECT 1 FROM workflow.v_task_action_recipient ar
      WHERE ar.task_id=t.id AND ar.assignee_user_ref_id=common.current_user_ref_id()) END
    ELSE COALESCE((SELECT
    (e->'capabilities'->>'review_completion')::boolean
    OR (e->'protocol_version'='3'::jsonb AND (
      COALESCE((e->'capabilities'->>'append_feedback')::boolean,false)
      OR COALESCE((e->'capabilities'->>'continue_original')::boolean,false)
      OR COALESCE((e->'capabilities'->>'resume')::boolean,false)))
    OR (e->'capabilities'->>'accept_task')::boolean
    OR (e->'capabilities'->>'complete_self')::boolean
    OR (e->'capabilities'->>'save_checklist')::boolean
    OR (e->'capabilities'->>'set_item_progress')::boolean
    OR (e->'execution_handover'->>'status'='pending'
        AND (e->'capabilities'->>'respond_executor_handover')::boolean)
    OR (e->'reviewer_handover'->>'status'='pending'
        AND (e->'capabilities'->>'respond_reviewer_handover')::boolean)
    OR (e->'pending_requirements'->'required_user_ids' ? common.current_user_ref_id()::text
        AND NOT (e->'pending_requirements'->'confirmed_user_ids' ? common.current_user_ref_id()::text)
        AND (e->'capabilities'->>'respond_requirements')::boolean)
    OR ((e->'pause'->>'recheck_at')::timestamptz<=statement_timestamp()
        AND (e->'capabilities'->>'recheck_pause')::boolean)
    FROM (SELECT workflow.task_execution_summary(t.id) AS e) execution),false) END"""


def normalize_execution(item):
    execution = item.get("execution") or {}
    mode = item.get("execution_mode") or execution.get("execution_mode")
    # Existing V1 projections predate mode; absent execution is never promoted.
    mode = mode or ({1: "execution_v1", 2: "execution_v2", 3: "execution_v3"}.get(execution.get("protocol_version"), "legacy"))
    item["execution_mode"] = mode
    item["end_reason"] = task_end_reason({**item, "execution_mode": mode})
    item.setdefault("ended_at", item.get("completed_at") if item.get("status") == "completed" else None)
    item["execution_protocol_version"] = {"execution_v1": 1, "execution_v2": 2, "execution_v3": 3}.get(mode)
    item["execution_compatibility"] = {
        "mode": mode,
        "protocol_version": item["execution_protocol_version"],
        "legacy_mutations_supported": (["accept", "reject", "complete", "approve_completion",
            "reject_completion", "reassign", "cancel"] if mode == "legacy" else ["cancel"] if mode == "execution_v1" else []),
        "code": None if mode == "legacy" else "TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED",
        "message": ("此任务使用原有接受、完成和验收流程。" if mode == "legacy" else
                    "此任务使用四状态任务流程，请刷新页面或升级客户端。" if mode == "execution_v3" else
                    "此任务使用新版推进清单；请升级客户端后执行、验收或交接。"),
    }
    if mode == "legacy":
        item["execution"] = None
        item["execution_capabilities"] = {}
        item["current_reviewer"] = item.get("creator_user_ref_id")
        return item
    if not execution:
        return item
    for key in (
        "progress",
        "current_reviewer",
        "reviewer",
        "executor",
        "executor_user_id",
        "requirements_version",
        "responsibility_version",
        "checklist_revision",
        "self_managed",
        "review_required",
        "requirements_revalidation",
        "requirements_incomplete",
        "missing_deliverables",
        "coordination_required",
        "pause",
        "execution_handover",
        "reviewer_handover",
        "pending_requirements",
        "first_started_at",
        "last_execution_feedback_at",
        "processing_reason",
        "processing_detail",
        "current_submission",
        "submission_round",
        "latest_feedback",
        "next_step",
        "completion_result",
        "completion_action",
        "completion_detail",
    ):
        item[key] = execution.get(key)
    item["execution_capabilities"] = execution.get("capabilities", {})
    if mode == "execution_v3":
        item["status"] = execution.get("status", item.get("status"))
        item["progress"] = None
        item["can_cancel"] = False
        item["can_coordinate"] = False
    item["execution_flags"] = {
        "paused": bool(execution.get("pause")) or execution.get("processing_reason") == "paused",
        "execution_handover_pending": (execution.get("execution_handover") or {}).get("status") == "pending",
        "reviewer_handover_pending": (execution.get("reviewer_handover") or {}).get("status") == "pending",
        "requirements_pending": bool(execution.get("pending_requirements")),
        "awaiting_review": execution.get("status") == "pending_review" or execution.get("processing_reason") == "awaiting_review",
        "progress_basis_missing": (execution.get("progress") or {}).get("basis") == "missing_checklist",
    }
    # These are legacy event permissions, not the new execution command grants.
    permissions = item.get("action_permissions")
    if permissions:
        for key in ("task.accept", "task.decline", "task.complete", "task.review", "task.coordinate"):
            permissions[key] = False
        if mode in {"execution_v2", "execution_v3"}:
            permissions["task.cancel"] = False
    return item
