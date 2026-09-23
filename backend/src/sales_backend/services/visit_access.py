"""FDE may record their own assigned opportunities, even with wider read access."""

from sales_backend.domain.capabilities import FDE_ROLES
from sales_backend.repositories.collaboration import member_ids, validated_fde
from sales_backend.repositories.fde_recording import recording_opportunity, recording_visit


async def validate_visit_attendance(connection, actor, values, opportunity_id, mutation):
    """Visit attendance adds to the roster; only independent editing may replace it."""
    participants = member_ids(values)
    if mutation and mutation.get("fde_member_ids") is not None:
        raise ValueError("请重新进入拜访核对页，仅选择本次参与的FDE；商机完整名单请在商机详情维护")
    if actor.role.value in FDE_ROLES:
        if set(participants) - {actor.user_id}:
            raise PermissionError("FDE本人录入不能代其他FDE登记参与，请由对方本人填写")
    if participants and not (opportunity_id or mutation):
        raise ValueError("请先关联商机，再选择本次协助FDE")
    if participants:
        await validated_fde(connection, participants)
    return participants


async def require_visit_recording_scope(connection, actor, customer_id, opportunity_id):
    if actor.role.value not in FDE_ROLES:
        return
    if not customer_id or not opportunity_id:
        raise PermissionError("请先选择本人参与的商机，再录入拜访")
    if not await recording_opportunity(connection, opportunity_id, customer_id):
        raise PermissionError("你已不在该商机的FDE名单中，或商机不属于当前客户，请重新选择")


async def require_visit_supplement_scope(connection, actor, visit_id):
    if actor.role.value not in FDE_ROLES:
        return
    row = await recording_visit(connection, visit_id)
    if not row:
        raise LookupError("拜访记录不存在或已不在当前权限范围")
    if row["status"] != "archived" or any(
        row[key] != actor.user_id
        for key in ("created_by_user_ref_id", "recorder_user_ref_id", "confirmed_by_user_ref_id")
    ):
        raise PermissionError("仅本人创建并确认归档的拜访可以补充")
    await require_visit_recording_scope(connection, actor, row["customer_id"], row["opportunity_id"])
