"""Preview never writes; selected values use one authorized versioned transaction."""

from sales_backend.domain.concurrency import require_version, VersionConflict
from sales_backend.domain.opportunity_initialization import suggestions, empty
from sales_backend.repositories import opportunity_initialization as repository
from sales_backend.repositories.opportunity_fields import prepare_people
from sales_backend.services.opportunity_fields import patch_fields


async def preview(connection, actor, ids):
    await repository.authorize(connection, ids)
    result = []
    for row in await repository.records(connection, ids):
        suggested = suggestions(row["source"], row)
        values = suggested["values"]
        identifiers = set(values.get("fde_member_ids", [])) | {p["user_id"] for p in values.get("participants", [])}
        if values.get("primary_fde_id"):
            identifiers.add(values["primary_fde_id"])
        available = await repository.active_people(connection, sorted(identifiers))
        for key in ("primary_fde_id", "fde_member_ids", "participants"):
            if key not in values:
                continue
            persons = values[key] if key != "primary_fde_id" else [values[key]]
            desired = (
                {p["user_id"]: p["role"] for p in persons} if key == "participants" else {uid: "fde" for uid in persons}
            )
            if any(
                uid not in available or (role == "fde" and not available[uid]["is_fde"])
                for uid, role in desired.items()
            ):
                values.pop(key)
                suggested["conflicts"].append(
                    {
                        "field": key,
                        "code": "person_requires_review",
                        "source_fields": suggested["sources"].pop(key),
                        "message": "旧人员不存在、已停用或职责不适用，请人工选择",
                    }
                )
        # The primary and roster are independent historical facts. Never derive
        # or repair one from the other; the administrator must resolve conflict.
        primary = values.get("primary_fde_id")
        roster = set(values.get("fde_member_ids", row["fde_member_ids"]))
        if primary and primary not in roster:
            values.pop("primary_fde_id")
            suggested["conflicts"].append(
                {
                    "field": "primary_fde_id",
                    "code": "current_roster_conflict",
                    "source_fields": suggested["sources"].pop("primary_fde_id"),
                    "message": "确认后的长期名单须包含主责，请人工核对并选择",
                }
            )
        visible_ids = set(values.get("fde_member_ids", [])) | {p["user_id"] for p in values.get("participants", [])}
        if values.get("primary_fde_id"):
            visible_ids.add(values["primary_fde_id"])
        suggested["people"] = [{"id": uid, "name": available[uid].get("name") or "成员"} for uid in sorted(visible_ids)]
        result.append({"opportunity_id": row["id"], "version_no": row["version_no"], **suggested})
    return {"items": result, "requires_confirmation": True}


async def apply(connection, actor, body):
    ids = [item.opportunity_id for item in body.items]
    await authorize_apply(connection, body)
    rows = {r["id"]: r for r in await repository.records(connection, ids, lock=True)}
    prepared = []
    # Validate every record before the first write; DB checks still govern the
    # final state. All writes and the mutation receipt share the API transaction.
    for item in body.items:
        oid = str(item.opportunity_id)
        before = rows[oid]
        require_version(before["version_no"], item.version_no)
        values = item.values.model_dump(mode="json", exclude_unset=True)
        if any(not empty(before.get(key)) for key in values):
            raise VersionConflict("部分字段已有正式值，初始化不能覆盖；请刷新预览或使用字段维护")
        people = bool(set(values) & {"primary_fde_id", "fde_member_ids", "participants"})
        if people:
            await prepare_people(connection, actor, oid, values, before)
        prepared.append((item, values, people))
    results = []
    for item, values, people in prepared:
        result = await patch_fields(
            connection,
            actor,
            str(item.opportunity_id),
            version_no=item.version_no,
            reason=body.reason,
            values=values,
            people_only=people,
        )
        results.append({"opportunity_id": str(item.opportunity_id), **result})
    return {"items": results, "applied_count": sum(r["changed"] for r in results)}


async def authorize_apply(connection, body):
    await repository.authorize(connection, [i.opportunity_id for i in body.items])
    for item in body.items:
        if set(item.values.model_fields_set) & {"primary_fde_id", "fde_member_ids"}:
            await repository.authorize(connection, [item.opportunity_id], people=True)
