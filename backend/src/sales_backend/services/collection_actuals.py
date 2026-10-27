"""Direct collection edits share the opportunity transaction and mutation receipt."""

import asyncpg

from sales_backend.domain.collection_actual_errors import COLLECTION_ACTUAL_SQL_ERRORS, CollectionActualError
from sales_backend.repositories.business_changes import record_change
from sales_backend.repositories.collection_actuals import read_collection_actual


async def save_collection_actuals(connection, actor, opportunity_id, customer, data):
    try:
        return await _save_collection_actuals(connection, actor, opportunity_id, customer, data)
    except asyncpg.PostgresError as exc:
        code = COLLECTION_ACTUAL_SQL_ERRORS.get((exc.sqlstate, exc.message))
        if code is None:
            raise
        raise CollectionActualError(code) from exc


async def _save_collection_actuals(connection, actor, opportunity_id, customer, data):
    inputs = data.get("collection_actuals") or []
    if not inputs:
        return None
    if data.get("action") != "update":
        raise ValueError("实际回款请在已有商机修改中登记")
    results = []
    for item in inputs:
        before = await read_collection_actual(connection, opportunity_id, item["year"], item["quarter"])
        if not before["can_edit"]:
            raise CollectionActualError("OPPORTUNITY_COLLECTION_FORBIDDEN")
        if before["actual"]["revision"] != item["expected_actual_revision"]:
            raise CollectionActualError("OPPORTUNITY_COLLECTION_VERSION_CONFLICT")
        if before["actual"]["requires_source_replacement"] and not item.get("replace_source"):
            raise CollectionActualError("OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED")
        changed = await connection.fetchval(
            """SELECT security.save_opportunity_collection_actual(
            $1::uuid,$2,$3,$4::numeric,$5,$6,$7,$8)""",
            str(opportunity_id),
            item["year"],
            item["quarter"],
            item["amount"],
            item["source_ref"],
            item.get("note", ""),
            item["expected_actual_revision"],
            item.get("replace_source", False),
        )
        after = await read_collection_actual(connection, opportunity_id, item["year"], item["quarter"])
        if changed:
            version = await connection.fetchval(
                "SELECT version_no FROM crm.opportunity WHERE id=$1::uuid", str(opportunity_id)
            )
            await record_change(
                connection,
                actor,
                customer,
                opportunity_id=str(opportunity_id),
                version=version,
                notify=False,
                changes=[
                    {
                        "field": "collection_actual",
                        "label": "实际回款（元）",
                        "year": item["year"],
                        "quarter": item["quarter"],
                        "source_ref": item["source_ref"],
                        "note": item.get("note", ""),
                        "before": before["actual"],
                        "after": after["actual"],
                    }
                ],
                title="商机回款修改",
            )
        results.append(after)
    return results
