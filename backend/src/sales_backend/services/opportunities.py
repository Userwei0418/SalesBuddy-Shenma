"""Human-confirmed opportunity workflow shared by manual save and visit archival.

Caller supplies one transaction: permissions, version, write, change card and review job
commit together. Pure rules and SQL are maintained in separate domain/repository modules.
"""

import asyncpg

from sales_backend.domain.opportunity_creation import translate_creation_error
from sales_backend.services.collection_actuals import save_collection_actuals
from sales_backend.services.recognized_actuals import save_recognized_actuals
from sales_backend.domain.recognized_actual_errors import RecognizedActualError
from uuid import uuid4
from decimal import Decimal

from sales_backend.domain.company_rules import OpportunityAmountPolicy
from sales_backend.domain.concurrency import require_version
from sales_backend.domain.opportunities import (
    change_tone,
    differences,
    normalize_forecasts,
    prepare_change,
    stage_for_probability,
    validate_forecast_completeness,
)
from sales_backend.repositories.opportunity_creation import require_creation_customer
from sales_backend.repositories.business_changes import record_change
from sales_backend.repositories.company_rules import CompanyRulesRepository
from sales_backend.repositories.collaboration import members_by_opportunity, write_members
from sales_backend.repositories.opportunity_fields import (
    prepare_people, profile_changes, profile_projection, write_project_people,
)
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
from sales_backend.services.authorization import opportunity_creation_owner, require_permission
from sales_backend.repositories.materials import OpportunityMaterialRepository
from sales_backend.repositories.opportunity_product_lines import legacy_for_save, prepare_intent_lines


async def require_amount_confirmation(connection, data, before=None):
    """Read-only guard shared by direct saves and pre-confirmation visit archival."""
    amount = Decimal(str(data["amount"]))
    if before is not None and before.get("amount") == amount:
        return
    snapshot = await CompanyRulesRepository().active(connection, "opportunity_amount")
    confirmed = data.get("amount_confirmed_value")
    if confirmed is not None:
        confirmed = Decimal(str(confirmed))
    OpportunityAmountPolicy(**snapshot["definition"]).require_confirmation(amount, confirmed)


async def save_opportunity_materials(connection, actor, opportunity_id, data):
    """Model a historical transaction without inventing its ACV or exact date."""
    await require_permission(connection, "opportunity.update", opportunity_id=opportunity_id)
    await connection.execute("SELECT security.finance_write_lock()")
    before = await connection.fetchrow("""SELECT * FROM crm.opportunity WHERE id=$1::uuid
      AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL FOR NO KEY UPDATE""", str(opportunity_id))
    if not before:
        raise LookupError("商机不存在或不可修改")
    require_version(before["version_no"], data["version_no"])
    materials = OpportunityMaterialRepository()
    relation, upstream, old, changes = await materials.prepare(connection, opportunity_id, data, dict(before))
    if not changes:
        return {**await materials.read(connection, opportunity_id), "changed": False}
    version = await connection.fetchval("""UPDATE crm.opportunity SET trade_relation=$2,
      upstream_opportunity_id=$3::uuid WHERE id=$1::uuid RETURNING version_no""", str(opportunity_id), relation, upstream)
    await materials.save(connection, actor, opportunity_id, data["material_lines"], old)
    await record_change(connection, actor, {"id":before["customer_id"]}, opportunity_id=str(opportunity_id),
                        version=version, changes=changes+[{"field":"material_reason","label":"物料维护说明",
                        # Structured audit context is deliberately excluded by
                        # V188's scalar field-change notification capture.
                        "before":None,"after":{"reason":data["reason"]}}], notify=False)
    return {**await materials.read(connection, opportunity_id), "changed": True}


async def save_opportunity(connection, actor, *, customer_id, data, visit_context=None):
    updating = data.get("action") == "update"
    await require_permission(connection, "opportunity.update" if updating else "opportunity.create")
    await connection.execute("SELECT security.finance_write_lock()")
    customer = (await customer_for_opportunity(connection, customer_id) if updating
                else await require_creation_customer(connection, customer_id))
    if not customer:
        raise LookupError("客户不存在或不在当前权限范围内")
    if not updating and "customer" not in customer.get("business_identities", ["customer"]):
        raise ValueError("纯合作伙伴不能作为商机客户，请先添加客户身份及有效客户类型")
    oid = data.get("opportunity_id") if updating else str(uuid4())
    before = None
    old_forecasts = []
    if updating:
        if not oid:
            raise ValueError("请选择要更新的商机")
        row = await lock_opportunity(connection, oid, customer_id)
        if not row:
            raise LookupError("商机不存在或不属于此客户")
        await require_permission(connection, "opportunity.update", opportunity_id=oid)
        before = dict(row)
        require_version(before["version_no"], data.get("version_no"))
        old_forecasts = await forecasts(connection, oid)
    owner = None
    if "owner_user_ref_id" in data and not updating:
        if not data.get("owner_user_ref_id"):
            raise ValueError("请选择商机负责人")
        owner = await validate_opportunity_owner(connection, data["owner_user_ref_id"])
    if not updating:
        owner = await opportunity_creation_owner(connection, actor, data, delegated_owner=owner)
    if (
        updating
        and data.get("owner_user_ref_id")
        and str(data["owner_user_ref_id"]) != str(before["owner_user_ref_id"])
    ):
        raise ValueError("此处保留商机负责人，人员交接需单独处理")
    data = {**data, "product_line": await legacy_for_save(connection, data, before)}
    after = prepare_change(before, data)
    profile, changed_profile = profile_changes(before, data)
    after.update(profile)
    await require_amount_confirmation(connection, data, before)
    if after["status"] != (before["status"] if before else "open"):
        permission = "opportunity.reopen" if after["status"] == "open" else "opportunity.close"
        if updating:
            await require_permission(connection, permission, opportunity_id=oid)
        else:
            from sales_backend.domain.authorization import ObjectScope
            from sales_backend.repositories.authorization import AuthorizationRepository

            (await AuthorizationRepository().effective(connection)).require(permission, ObjectScope(
                actor.workspace_id, owner["user_id"], owner["team_id"], frozenset({owner["user_id"]})))
    after.update(await resolve_opportunity_partner(connection, before, data))
    if not await opportunity_name_available(connection, customer_id, after["name"], oid if updating else None):
        raise FileExistsError(NAME_CONFLICT_MESSAGE)
    stage = after["status"] if after["status"] != "open" else stage_for_probability(after["probability"])
    new_forecasts = normalize_forecasts(old_forecasts, data.get("quarterly_forecasts"))
    validate_forecast_completeness(after, new_forecasts, existing=old_forecasts,
                                   schema_version=data.get("forecast_schema_version", 1))
    changes = differences(before, after, old_forecasts, new_forecasts)
    changes.extend(changed_profile)
    materials = OpportunityMaterialRepository()
    relation, upstream, old_lines, material_changes = await materials.prepare(connection, oid, data, before)
    if data.get("recognized_actuals") and material_changes:
        raise RecognizedActualError("OPPORTUNITY_RECOGNIZED_MATERIALS_CHANGED")
    final_lines = data.get("material_lines")
    has_materials = bool(final_lines) if final_lines is not None else any(line['is_active'] for line in old_lines)
    after['intent_product_line_ids'], _ = await prepare_intent_lines(connection, data, before, has_materials=has_materials)
    after.update(trade_relation=relation, upstream_opportunity_id=upstream)
    changes.extend(material_changes)
    people = await prepare_people(connection, actor, oid, data, before)
    changes.extend(people["changes"])
    if not changes:
        result = {
            **after,
            "id": oid,
            "customer_id": customer_id,
            "customer_name": customer["name"],
            "version_no": before["version_no"],
            "changed": False,
            "operation": "unchanged",
            "quarterly_forecasts": old_forecasts,
            "material_lines": [r for r in old_lines if r["is_active"]],
            "stage_code": stage,
            "fde_members": (await members_by_opportunity(connection, [oid])).get(oid, []),
        }
        actuals = await save_collection_actuals(connection, actor, oid, customer, data)
        if actuals is not None:
            result["collection_actuals"] = actuals
        recognized = await save_recognized_actuals(connection, actor, oid, customer, data)
        if recognized is not None:
            result["recognized_actuals"] = recognized
        return (await profile_projection(connection, [result]))[0]
    # Legacy business/forecast change cards have labels but no field key.
    # They still require normal assessment; only explicit material keys opt out.
    only_materials = updating and all(change.get('field') in {'trade_relation', 'material_lines'} for change in changes)
    try:
        version = await write_opportunity(
            connection, actor, customer, customer_id, oid, after, stage, updating, owner=owner
        )
    except asyncpg.PostgresError as error:
        translate_creation_error(error)
    await write_forecasts(connection, actor, oid, data.get("quarterly_forecasts"))
    # Unchanged legacy material payloads must not bump line versions between
    # the read revision and the atomic actual save. Real changes were rejected above.
    await materials.save(connection, actor, oid,
                         None if data.get("recognized_actuals") else data.get("material_lines"), old_lines)
    if people["fde_ids"] is not None:
        await write_members(connection, actor, oid, people["fde_ids"])
    await write_project_people(connection, actor, oid, people)
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
        assess_change=updating and not only_materials,
        notify=not only_materials,
    )
    if updating and not only_materials:
        await enqueue_change_review(
            connection, actor, event_id=event, opportunity_id=oid, before=before, after=after,
            old_forecasts=old_forecasts, new_forecasts=new_forecasts, changes=changes,
            fallback={"tone": tone, "title": title}, visit_context=visit_context,
        )
    if not only_materials:
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
    result = {
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
        "material_lines": [r for r in await materials.rows(connection, oid) if r['is_active']],
        "fde_members": (await members_by_opportunity(connection, [oid])).get(oid, []),
    }
    actuals = await save_collection_actuals(connection, actor, oid, customer, data)
    if actuals is not None:
        result["collection_actuals"] = actuals
    recognized = await save_recognized_actuals(connection, actor, oid, customer, data)
    if recognized is not None:
        result["recognized_actuals"] = recognized
    return (await profile_projection(connection, [result]))[0]
