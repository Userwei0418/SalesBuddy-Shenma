"""Bind human archival to the stored review, never to a client supplied score."""

import hashlib
import json
from uuid import UUID

from sales_backend.domain.capabilities import FDE_ROLES
from sales_backend.domain.company_rules import VisitAdmissionPolicy
from sales_backend.repositories.visit_reviews import VisitReviewRepository
from sales_backend.services.agent_access import require_agent_access


async def consume_review(connection, actor, customer_id, fields):
    try:
        run_id = str(UUID(str(fields.get("_quality_review_run_id", ""))))
    except ValueError as exc:
        raise ValueError("请重新执行AI审核后再确认归档") from exc
    row = await VisitReviewRepository().lock_review(connection, actor, run_id)
    if not row or row["status"] not in {"pending_confirm", "pending_supplement"}:
        raise ValueError("审核记录不存在、已使用或不属于当前账号")
    if actor.role.value in FDE_ROLES:
        context = row["business_context"] or {}
        identity = row["identity_context"] or {}
        if context.get("customer_id") != customer_id or context.get("opportunity_id") != fields.get("opportunity_id"):
            raise ValueError("关联商机已修改，请按当前商机重新执行AI审核")
        if not identity.get("permission_version"):
            raise PermissionError("审核权限信息已失效，请重新执行AI审核")
        await require_agent_access(
            connection,
            actor,
            "visit_entry",
            customer_id,
            opportunity_id=fields.get("opportunity_id"),
            permission_version=identity["permission_version"],
        )
    from sales_backend.contracts.visit_flow import archival_snapshot, reviewed_archival_snapshot
    from sales_backend.repositories.company_rules import CompanyRulesRepository

    context = row["business_context"] or {}
    request = context.get("visit_request") or {}
    if request.get("stage") != "quality" or row["payload"].get("visit_stage") != "quality":
        raise ValueError("请完成独立质检后再确认归档")
    reviewed = reviewed_archival_snapshot(request)
    if context.get("customer_id") != customer_id or reviewed != archival_snapshot(fields):
        raise ValueError("拜访内容或关联已修改，请重新质检")
    current_policy = await CompanyRulesRepository().active(connection, "visit_admission")
    prior_policy = row["payload"].get("company_policy") or {}
    if current_policy.get("id") != prior_policy.get("id") or current_policy["definition"] != prior_policy.get(
        "definition"
    ):
        raise ValueError("审核规则已更新，请重新质检")
    payload = row["payload"]
    quality = payload.get("quality_review", {})
    score = quality.get("follow_up_score")
    policy = payload.get("company_policy")
    admission = VisitAdmissionPolicy(**((policy or {}).get("definition") or {}))
    if not admission.admits(score):
        raise ValueError(f"AI 质量审核须{admission.requirement()}，请修改后重新审核")
    next_action = quality.get("next_action")
    # Current canonical review wins even when false. Legacy compatibility
    # must never turn an explicit rejection into permission to archive.
    if isinstance(next_action, dict) and "passed" in next_action:
        next_review_passed = next_action["passed"] is True
    elif "next_action_passed" in quality:
        next_review_passed = quality["next_action_passed"] is True
    else:
        next_review_passed = payload.get("next_action", {}).get("passed") is True
    if isinstance(next_action, dict) and any(next_action.get(k) is False for k in ("time_found", "goal_or_plan_found")):
        next_review_passed = False
    if not next_review_passed:
        raise ValueError("服务端下一步审核未通过")
    trusted_fields = {
        **fields,
        "_follow_up_quality_score": score,
        "_next_action_review_passed": True,
        "_company_policy": policy,
    }
    payload_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    await VisitReviewRepository().confirm(connection, actor, row["id"], payload_hash, trusted_fields)
    return trusted_fields, row["id"]
