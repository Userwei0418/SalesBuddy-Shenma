"""Net recognition edits join the opportunity's transaction and save receipt."""

import json
from decimal import Decimal

import asyncpg

from sales_backend.domain.collection_actual_errors import CollectionActualError
from sales_backend.domain.recognized_actual_errors import RECOGNIZED_ACTUAL_ERRORS, RecognizedActualError
from sales_backend.repositories.business_changes import record_change
from sales_backend.repositories.recognized_actuals import read_recognized_actual


async def save_recognized_actuals(connection, actor, opportunity_id, customer, data):
    inputs = data.get("recognized_actuals") or []
    if not inputs:
        return None
    if data.get("action") != "update":
        raise RecognizedActualError("OPPORTUNITY_RECOGNIZED_INPUT_INVALID")
    results = []
    try:
        for item in inputs:
            before = await read_recognized_actual(connection, opportunity_id, item["year"], item["quarter"])
            if not before["can_edit"]:
                reason = before.get("editable_reason")
                if reason == "OPPORTUNITY_TRADE_RELATION_REQUIRED":
                    raise CollectionActualError(reason)
                raise RecognizedActualError(
                    reason if reason in RECOGNIZED_ACTUAL_ERRORS else "OPPORTUNITY_RECOGNIZED_FORBIDDEN"
                )
            changed = await connection.fetchval(
                "SELECT security.save_opportunity_recognized_actual($1::uuid,$2,$3,$4::jsonb,$5,$6)",
                str(opportunity_id),
                item["year"],
                item["quarter"],
                json.dumps(
                    [{**entry, "amount": format(Decimal(str(entry["amount"])), "f")} for entry in item["entries"]],
                    default=str,
                ),
                item["expected_actual_revision"],
                item.get("replace_source", False),
            )
            after = await read_recognized_actual(connection, opportunity_id, item["year"], item["quarter"])
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
                    title="商机实际确收修改",
                    changes=[
                        {
                            "field": "recognized_actual",
                            "label": "季度实际不含税确收（元）",
                            "year": item["year"],
                            "quarter": item["quarter"],
                            "before": {"actual": before["actual"], "items": before["items"]},
                            "after": {"actual": after["actual"], "items": after["items"]},
                        }
                    ],
                )
            results.append(after)
    except asyncpg.PostgresError as exc:
        if (exc.sqlstate, exc.message) in {
            ("23514", "OPPORTUNITY_TRADE_RELATION_REQUIRED"),
            ("23514", "请先明确商机交易关系"),
        }:
            raise CollectionActualError("OPPORTUNITY_TRADE_RELATION_REQUIRED") from exc
        reviewed = {
            ("23514", "总代转售部分须在下游终端商机确认确收"): "OPPORTUNITY_RECOGNIZED_MATERIAL_FORBIDDEN",
            ("23514", "实际确收必须引用本商机物料行"): "OPPORTUNITY_RECOGNIZED_MATERIAL_FORBIDDEN",
            ("40001", "ACTUAL_REFERENCE_CHANGED"): "OPPORTUNITY_RECOGNIZED_VERSION_CONFLICT",
            ("23514", "ACTUAL_EXPECTED_MATERIALS_INVALID"): "OPPORTUNITY_RECOGNIZED_MATERIALS_CHANGED",
        }
        if (exc.sqlstate, exc.message) in reviewed:
            raise RecognizedActualError(reviewed[(exc.sqlstate, exc.message)]) from exc
        if exc.message in RECOGNIZED_ACTUAL_ERRORS and exc.sqlstate in {"22023", "23514", "40001", "42501"}:
            raise RecognizedActualError(exc.message) from exc
        raise
    return results
