"""Human-confirmed opportunity workflow shared by manual save and visit archival.

Caller supplies one transaction: permissions, version, write, change card and review job
commit together. Pure rules and SQL are maintained in separate domain/repository modules.
"""

from uuid import uuid4

from sales_backend.domain.concurrency import require_version
from sales_backend.domain.opportunities import (
    change_tone,
    differences,
    normalize_forecasts,
    prepare_change,
    stage_for_probability,
    validate_forecast_completeness,
)
from sales_backend.domain.policy import AgentModeForbidden, assert_opportunity_create_allowed
from sales_backend.repositories.business_changes import record_change
from sales_backend.repositories.collaboration import effective_ids, members_by_opportunity, validated_fde, write_members
from sales_backend.repositories.customer_risk import enqueue_customer_risk_review
from sales_backend.repositories.jobs import enqueue_battle_map_review
from sales_backend.repositories.opportunities import NAME_CONFLICT_MESSAGE, opportunity_name_available
from sales_backend.repositories.opportunity_changes import enqueue_change_review
from sales_backend.repositories.opportunity_mutations import (
    customer_for_opportunity,
    forecasts,
    lock_opportunity,
    validate_opportunity_owner,
    write_forecasts,
    write_opportunity,
)
from sales_backend.repositories.partners import resolve_opportunity_partner
from sales_backend.services.capabilities import require_capability


async def save_opportunity(connection, actor, *, customer_id, data, visit_context=None):
    await require_capability(connection, actor, "opportunity.edit")
    customer = await customer_for_opportunity(connection, customer_id)
    if not customer:
        raise LookupError("客户不存在或不在当前权限范围内")
    updating = data.get("action") == "update"
    oid = data.get("opportunity_id") if updating else str(uuid4())
    before = None
    old_forecasts = []
    if updating:
        if not oid:
            raise ValueError("请选择要更新的商机")
        row = await lock_opportunity(connection, oid, customer_id)
        if not row:
            raise LookupError("商机不存在或不属于此客户")
        if actor.role.value == "sales" and str(row["owner_user_ref_id"]) != actor.user_id:
            raise PermissionError("只能修改本人的商机")
        before = dict(row)
        require_version(before["version_no"], data.get("version_no"))
        old_forecasts = await forecasts(connection, oid)
    else:
        try:
            assert_opportunity_create_allowed(actor)
        except AgentModeForbidden as exc:
            raise PermissionError("当前角色不能创建商机") from exc
    owner = None
    if actor.role.value in {"operations", "administrator"} and not updating:
        if not data.get("owner_user_ref_id"):
            raise ValueError("请选择商机负责人")
        owner = await validate_opportunity_owner(connection, data["owner_user_ref_id"])
    if (
        updating
        and data.get("owner_user_ref_id")
        and str(data["owner_user_ref_id"]) != str(before["owner_user_ref_id"])
    ):
        raise ValueError("此处保留商机负责人，人员交接需单独处理")
    after = prepare_change(before, data)
    after.update(await resolve_opportunity_partner(connection, before, data))
    if not await opportunity_name_available(connection, customer_id, after["name"], oid if updating else None):
        raise FileExistsError(NAME_CONFLICT_MESSAGE)
    stage = after["status"] if after["status"] != "open" else stage_for_probability(after["probability"])
    new_forecasts = normalize_forecasts(old_forecasts, data.get("quarterly_forecasts"))
    validate_forecast_completeness(after, new_forecasts)
    changes = differences(before, after, old_forecasts, new_forecasts)
    fde_ids = None
    if data.get("fde_member_ids") is not None:
        fde_people = await validated_fde(connection, data["fde_member_ids"])
        fde_ids = {r["id"] for r in fde_people}
        old_fde = await effective_ids(connection, oid) if updating else set()
        if fde_ids != old_fde:
            changes.append(
                {
                    "field": "fde_members",
                    "label": "协助 FDE",
                    "before": "、".join(
                        p["name"] for p in (await members_by_opportunity(connection, [oid])).get(oid, [])
                    )
                    or "未设置",
                    "after": "、".join(p["name"] for p in fde_people) or "未设置",
                }
            )
    if not changes:
        return {
            **after,
            "id": oid,
            "customer_id": customer_id,
            "customer_name": customer["name"],
            "version_no": before["version_no"],
            "changed": False,
            "operation": "unchanged",
            "quarterly_forecasts": old_forecasts,
            "stage_code": stage,
        }
    version = await write_opportunity(
        connection, actor, customer, customer_id, oid, after, stage, updating, owner=owner
    )
    await write_forecasts(connection, actor, oid, data.get("quarterly_forecasts"))
    if fde_ids is not None:
        await write_members(connection, actor, oid, fde_ids)
    tone, title = change_tone(before, after)
    event = await record_change(
        connection,
        actor,
        customer,
        opportunity_id=oid,
        version=version,
        changes=changes,
        tone=tone,
        title=title,
        name=after["name"],
        assess_change=updating,
    )
    if updating:
        await enqueue_change_review(
            connection, actor, event_id=event, opportunity_id=oid, before=before, after=after,
            old_forecasts=old_forecasts, new_forecasts=new_forecasts, changes=changes,
            fallback={"tone": tone, "title": title}, visit_context=visit_context,
        )
    await enqueue_battle_map_review(
        connection,
        actor,
        customer_id=customer_id,
        trigger_type="opportunity.updated" if updating else "opportunity.created",
        trigger_id=event,
    )
    await enqueue_customer_risk_review(
        connection,
        actor,
        customer_id=customer_id,
        trigger_type="opportunity.updated" if updating else "opportunity.created",
        trigger_id=event,
    )
    return {
        **after,
        "id": oid,
        "customer_id": customer_id,
        "customer_name": customer["name"],
        "version_no": version,
        "changed": True,
        "event_id": event,
        "stage_code": stage,
        "operation": "updated" if updating else "created",
        "quarterly_forecasts": new_forecasts,
        "fde_members": (await members_by_opportunity(connection, [oid])).get(oid, []),
    }
