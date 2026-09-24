"""Authorize each visit action independently from recording/statistical identity."""

from sales_backend.repositories.collaboration import member_ids, validated_fde
from sales_backend.services.authorization import require_permission


async def validate_visit_attendance(connection, actor, values, opportunity_id, mutation):
    participants = member_ids(values)
    if mutation and mutation.get("fde_member_ids") is not None:
        raise ValueError("请重新进入拜访核对页，仅选择本次参与的FDE；商机完整名单请在商机详情维护")
    if participants and not (opportunity_id or mutation):
        raise ValueError("请先关联商机，再选择本次协助FDE")
    if set(participants) - {actor.user_id}:
        await require_permission(connection, "visit.attendance_manage", opportunity_id=opportunity_id)
    if participants:
        await validated_fde(connection, participants)
    return participants


async def require_visit_recording_scope(connection, actor, customer_id, opportunity_id, *, permission="visit.create"):
    if not await connection.fetchval(
        "SELECT security.authorization_visit_target($1,$2::uuid,$3::uuid,$4::uuid,$5::uuid)",
        permission, customer_id, opportunity_id or None, actor.user_id, actor.team_ids[0] if actor.team_ids else None,
    ):
        raise PermissionError("客户或关联商机不在本次跟进操作的授权范围，请重新选择")


async def require_visit_supplement_scope(connection, actor, visit_id):
    await require_permission(connection, "visit.supplement", visit_id=visit_id)


async def validate_visit_optional_actions(connection, actor, customer_id, opportunity_id, *,
                                          mutation=None, collaborators=(), first_visit=False):
    if mutation:
        action = "opportunity.update" if mutation.get("action") == "update" else "opportunity.create"
        await require_permission(connection, action, opportunity_id=mutation.get("opportunity_id") if action.endswith("update") else None)
    if collaborators:
        await require_visit_recording_scope(connection, actor, customer_id, opportunity_id, permission="visit.attendance_manage")
    if first_visit:
        await require_visit_recording_scope(connection, actor, customer_id, opportunity_id, permission="visit.first_visit")
