from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

import asyncpg

from sales_backend.contracts.models import OpportunityCreate
from sales_backend.contracts.visit_flow import archival_snapshot, reviewed_archival_snapshot
from sales_backend.db import json_value
from sales_backend.domain.agent import ActorContext
from sales_backend.domain.capabilities import FDE_ROLES
from sales_backend.repositories.collaboration import archive_fde_collaboration, member_ids
from sales_backend.repositories.customer_risk import enqueue_customer_risk_review
from sales_backend.repositories.jobs import enqueue_battle_map_review
from sales_backend.repositories.profile import ProfileRepository  # compatibility seam for archive recovery tests
from sales_backend.repositories.visit_reviews import VisitReviewRepository
from sales_backend.repositories.visits import VisitRepository
from sales_backend.services.capabilities import require_capability
from sales_backend.services.opportunities import save_opportunity
from sales_backend.services.visit_access import require_visit_recording_scope, validate_visit_attendance, validate_visit_optional_actions
from sales_backend.services.visit_review_gate import consume_review

COMPETENCY_SUBJECT_ROLES = frozenset({"sales", "supervisor"})


async def archive_visit(
    connection: asyncpg.Connection,
    actor: ActorContext,
    *,
    customer_id: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    """拜访归档的跨聚合编排：落拜访 → 触发战场地图复盘 → 刷新当日能力评估。

    三步共用调用方的事务，任一失败整体回滚，不会留下只入队没落库的拜访。
    """
    await require_capability(connection, actor, "visit.create")
    await require_visit_recording_scope(connection, actor, customer_id, fields.get("opportunity_id"))
    participants = member_ids(fields.get("_fde_participant_ids", []))
    mutation = fields.get("_opportunity_mutation")
    await validate_visit_optional_actions(connection, actor, customer_id, fields.get("opportunity_id"),
        mutation=mutation, collaborators=fields.get("collaborator_ids", []), first_visit=fields.get("is_first_visit", False))
    if actor.role.value in FDE_ROLES and fields.get("opportunity_id"):
        # Keep the real recording identity; extra permissions never create a sales appointment.
        participants = sorted(set(participants) | {actor.user_id})
    mutation_data = OpportunityCreate.model_validate(mutation).model_dump() if mutation else None
    await validate_visit_attendance(
        connection, actor, fields.get("_fde_participant_ids", []), fields.get("opportunity_id"), mutation_data,
        customer_id=customer_id,
    )
    # The visit INSERT takes a parent KEY SHARE through the opportunity FK;
    # acquire the workspace finance lock before that or any review/row lock.
    await connection.execute("SELECT security.finance_write_lock()")
    mutation_hash = (
        hashlib.sha256(json.dumps(mutation, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if mutation
        else None
    )
    run_id = str(UUID(str(fields.get("_quality_review_run_id", ""))))
    previous = await VisitReviewRepository().lock_previous_archive(connection, actor, run_id)
    if previous:
        previous = {**dict(previous), **(json_value(previous.get("first_visit_profile")) or {})}
        request = (previous["business_context"] or {}).get("visit_request") or {}
        reviewed = reviewed_archival_snapshot(request)
        if previous["customer_id"] != customer_id or reviewed != archival_snapshot(fields):
            raise ValueError("内容或关联已修改，请重新审核")
        if previous.get("save_only"):
            if sorted(previous.get("fde_participant_ids", [])) != participants:
                raise ValueError("本次参与人员已修改，请重新审核后归档")
            return {key: previous[key] for key in
                    ("id", "status", "customer_id", "opportunity_id", "completed_count", "total_count")} | {
                        "replayed": True, "can_read_visit": False,
                    }
        detail = await VisitRepository().detail(connection, previous["id"])
        if sorted(detail.get("fde_participant_ids", [])) != participants:
            raise ValueError("本次参与人员已修改，请重新审核后归档")
        return {
            **detail,
            "status": "archived",
            "fields": detail,
            "replayed": True,
            **await VisitRepository().archived_counts(connection, detail),
        }
    fields, artifact_id = await consume_review(connection, actor, customer_id, fields)
    fields["_fde_participant_ids"] = participants
    if mutation_data:
        opportunity = await save_opportunity(
            connection, actor, customer_id=customer_id, data=mutation_data, visit_context=fields,
        )
        fields["opportunity_id"] = opportunity["id"]
        fields.pop("_opportunity_mutation", None)
    visit = await VisitRepository().create(connection, actor, customer_id=customer_id, fields=fields)
    # Only an explicit authorized opportunity mutation changes its long-term
    # roster. Attendance records this event and never adds persistent access.
    await archive_fde_collaboration(connection, actor, visit, participants)
    await VisitReviewRepository().bind_archive(connection, visit["id"], artifact_id, mutation_hash)
    # Optional customer analyses require their original customer visibility.
    # Recording a project does not authorize reading its customer's facts.
    can_read_customer = await connection.fetchval(
        "SELECT security.has_customer_access($1::uuid)", visit["customer_id"]
    )
    if can_read_customer:
        await enqueue_battle_map_review(
            connection,
            actor,
            customer_id=visit["customer_id"],
            trigger_type="visit.archived",
            trigger_id=visit["id"],
        )
        await enqueue_customer_risk_review(
            connection,
            actor,
            customer_id=visit["customer_id"],
            trigger_type="visit.archived",
            trigger_id=visit["id"],
        )
    # Binding the review advances the visit version. Preserve the creation
    # receipt fields/counts, but return the final row for immediate supplements.
    detail = await VisitRepository().detail(connection, visit["id"])
    if actor.role.value in COMPETENCY_SUBJECT_ROLES:
        from sales_backend.repositories.competency_lifecycle import can_review

        if await can_review(connection, actor):
            await ProfileRepository().ensure_daily_competency_review(connection, actor)
    return {**visit, **detail}
