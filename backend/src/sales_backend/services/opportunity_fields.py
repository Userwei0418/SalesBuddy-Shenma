"""Narrow confirmed edits preserve unknown historic ACV and close-date precision."""

from sales_backend.domain.concurrency import require_version
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.business_changes import record_change
from sales_backend.repositories.collaboration import write_members
from sales_backend.repositories.opportunities import NAME_CONFLICT_MESSAGE, opportunity_name_available
from sales_backend.repositories.opportunity_fields import (
    PERSISTED_FIELDS, prepare_people, profile_changes, write_project_people,
)
from sales_backend.repositories.opportunity_mutations import customer_for_opportunity
from sales_backend.domain.opportunity_field_catalog import FIELD_CATALOG
from sales_backend.repositories.opportunity_product_lines import legacy_for_save, prepare_intent_lines


async def patch_fields(connection, actor, opportunity_id, *, version_no, reason, values, people_only=False):
    await require_permission(connection, "opportunity.update", opportunity_id=opportunity_id)
    await connection.execute("SELECT security.finance_write_lock()")
    row = await connection.fetchrow(
        "SELECT * FROM crm.opportunity WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() "
        "AND deleted_at IS NULL FOR UPDATE", opportunity_id,
    )
    if row is None:
        raise LookupError("商机不存在或不可维护")
    before = dict(row)
    require_version(before["version_no"], version_no)
    values = dict(values)
    if 'product_line' in values:
        values['product_line'] = await legacy_for_save(connection, values, before)
    if 'intent_product_line_ids' in values:
        has_materials = await connection.fetchval("""SELECT EXISTS(SELECT 1 FROM crm.opportunity_material
            WHERE opportunity_id=$1::uuid AND workspace_id=common.current_workspace_id() AND is_active)""", str(opportunity_id))
        values['intent_product_line_ids'], _ = await prepare_intent_lines(connection, values, before, has_materials=has_materials)
    profile, changes = profile_changes(before, values)
    assignments = {key: profile[key] for key in PERSISTED_FIELDS if key in values}
    for key in ("name", "product_line", "follow_up_plan"):
        if key in values:
            assignments[key] = values[key]
            if values[key] != before.get(key):
                changes.append({"field": key, "label": FIELD_CATALOG[key]["label"],
                                "before": before.get(key), "after": values[key]})
    if "name" in assignments and not await opportunity_name_available(
        connection, before["customer_id"], assignments["name"], opportunity_id,
    ):
        raise FileExistsError(NAME_CONFLICT_MESSAGE)
    people = await prepare_people(connection, actor, opportunity_id, values, before) if people_only else None
    if people:
        changes.extend(people["changes"])
    if not changes:
        return {"changed": False, "version_no": before["version_no"]}
    # Exact whitelist controls SQL identifiers. All values remain bound, and one
    # aggregate UPDATE advances the version once for either scalar or people edits.
    allowed = set(PERSISTED_FIELDS) | {"name", "product_line", "follow_up_plan"}
    if assignments.keys() - allowed:
        raise ValueError("不支持的商机修改字段")
    args = [str(opportunity_id)]
    fragments = []
    for key, value in assignments.items():
        args.append(value)
        cast = "uuid[]" if key == "intent_product_line_ids" else "uuid" if key == "primary_fde_id" else "text[]" if key == "customer_source" else "boolean" if key == "is_key" else "text"
        fragments.append(f"{key}=${len(args)}::{cast}")
    version = await connection.fetchval(
        "UPDATE crm.opportunity SET " + (",".join(fragments) or "updated_at=clock_timestamp()")
        + " WHERE id=$1::uuid RETURNING version_no", *args,
    )
    if people:
        if people["fde_ids"] is not None:
            await write_members(connection, actor, opportunity_id, people["fde_ids"])
        await write_project_people(connection, actor, opportunity_id, people)
    customer = await customer_for_opportunity(connection, before["customer_id"])
    await record_change(connection, actor, customer, opportunity_id=str(opportunity_id), version=version,
                        changes=[*changes, {"field": "field_edit_reason", "label": "商机字段维护说明",
                                           "before": None, "after": {"reason": reason}}], notify=False)
    return {"changed": True, "version_no": version}
