"""Authorize each visit action independently from recording/statistical identity."""

from sales_backend.repositories.collaboration import member_ids, validated_fde
from sales_backend.services.authorization import require_permission


async def require_opportunity_mutation_scope(connection, mutation, *, hide_unreadable=False):
    """Run before either receipt cache: original write authorization can expire."""
    if not mutation:
        return
    updating = mutation.get("action") == "update"
    oid = mutation.get("opportunity_id") if updating else None
    if updating:
        try:
            await require_permission(connection, "opportunity.read", opportunity_id=oid)
        except PermissionError as exc:
            if not hide_unreadable:
                raise
            # Conceal the target only for read denial; write denials stay 403.
            raise LookupError("商机不存在或不在当前权限范围内") from exc
    await require_permission(connection, "opportunity.update" if updating else "opportunity.create", opportunity_id=oid)
    if mutation.get("fde_member_ids") is not None or "primary_fde_id" in mutation:
        await require_permission(connection, "opportunity.fde_members", opportunity_id=oid)


async def validate_visit_attendance(connection, actor, values, opportunity_id, mutation, *, customer_id):
    participants = member_ids(values)
    if mutation and mutation.get("fde_member_ids") is not None and mutation.get("fields_schema_version") != 2:
        raise ValueError("请重新进入拜访核对页，仅选择本次参与的FDE；商机完整名单请在商机详情维护")
    if mutation and (mutation.get("fde_member_ids") is not None or "primary_fde_id" in mutation):
        # An explicit roster mutation is a separate reviewed business action;
        # attendance never supplies or replaces its values.
        await require_permission(connection, "opportunity.fde_members", opportunity_id=(
            mutation.get("opportunity_id") if mutation.get("action") == "update" else None
        ))
    if set(participants) - {actor.user_id}:
        if mutation:
            await require_permission(connection, "visit.attendance_manage", opportunity_id=opportunity_id)
        else:
            # Attendance belongs to this visit, including customer-only visits.
            # Check the actual customer scope rather than an unscoped capability.
            await require_visit_recording_scope(
                connection, actor, customer_id, opportunity_id, permission="visit.attendance_manage"
            )
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
    await require_opportunity_mutation_scope(connection, mutation)
    if collaborators:
        await require_visit_recording_scope(connection, actor, customer_id, opportunity_id, permission="visit.attendance_manage")
    if first_visit:
        await require_visit_recording_scope(connection, actor, customer_id, opportunity_id, permission="visit.first_visit")
