"""Read-only draft assembly using current catalog or an existing frozen line."""
import json

from sales_backend.domain.concurrency import VersionConflict, require_version
from sales_backend.domain.materials import (
    CALCULATOR_VERSION, TRACE_ENCODING, _finish_trace, _line_trace, _period, _trace_value,
    material_forecast_preview,
)
from sales_backend.repositories.authorization_checks import require_permission
from sales_backend.repositories.opportunity_mutations import customer_for_opportunity


def incomplete_line(line, *, year, quarter, relation, missing):
    """Keep known frozen evidence visible without inventing calculation inputs."""
    line_id = str(line.get("id") or line["client_key"])
    months = [year * 12 + (quarter - 1) * 3 + i for i in range(3)]
    if line.get("catalog_snapshot"):
        trace = _line_trace({**line, "id": line_id, "trade_relation": relation}, year, quarter, months)
    else:
        trace = {
            "calculator_version": CALCULATOR_VERSION, "canonical_encoding": TRACE_ENCODING,
            "period": _period(year, quarter, months),
            "identity": {"line_id": line_id, "line_version": _trace_value(line.get("version_no")),
                         "catalog_id": _trace_value(line.get("material_id")), "catalog_version": None},
            "catalog_snapshot": None, "catalog_snapshot_sha256": None,
            "rule": None, "rule_sha256": None,
            "business_definition": {"status": "catalog_not_selected", "participates_in_calculation": False,
                                    "value": None},
            "inputs": {key: _trace_value(line.get(key)) for key in (
                "quantity", "unit_price", "usage", "expected_delivery_on", "expected_start_on",
                "expected_oa_on", "service_months", "expected_account_count", "expected_consumption_amount",
                "expected_consumption_year", "expected_consumption_quarter")},
            "pricing": {"status": "not_calculated"}, "monthly_allocation": None,
            "tax": {"status": "not_calculated"}, "steps": [],
        }
        trace["inputs"]["trade_relation"] = relation
    trace["steps"].append({"step": "required_inputs", "status": "not_calculated", "missing_fields": missing})
    return _finish_trace(trace, {
        "line_id": line_id, "status": "incomplete", "amount_inclusive": None,
        "amount_exclusive": None, "missing_fields": missing,
    })


def draft_reference(lines, *, year, quarter, relation):
    ready, incomplete = [], {}
    for line in lines:
        missing = [field for field in ("material_id", "catalog_version_no", "quantity", "unit_price", "usage")
                   if line.get(field) is None]
        if not line.get("catalog_snapshot") and "material_id" not in missing:
            missing.append("catalog_snapshot")
        if missing:
            incomplete[line["client_key"]] = incomplete_line(
                line, year=year, quarter=quarter, relation=relation, missing=missing)
        else:
            ready.append({**line, "id": str(line.get("id") or line["client_key"]), "trade_relation": relation})
    result = material_forecast_preview(ready, year, quarter)
    # Input order is the identity within a draft. Do not key calculated rows by
    # saved ID: an unsaved client key can coincide with another row's saved ID.
    calculated = iter(result["lines"])
    result["lines"] = [
        {**(incomplete[line["client_key"]] if line["client_key"] in incomplete else next(calculated)),
         "client_key": line["client_key"], "catalog_version_no": line.get("catalog_version_no"),
         "snapshot_source": line["snapshot_source"]}
        for line in lines
    ]
    result["missing_fields"] = [f"{r['client_key']}:{key}" for r in result["lines"] for key in r["missing_fields"]]
    if not lines:
        result["missing_fields"] = ["material_lines"]
    if incomplete:
        result.update(status="incomplete", recognized_inclusive=None, recognized_exclusive=None)
        trace = result["calculation_trace"]
        trace.update(calculation_status="not_calculated", not_calculated_reasons=result["missing_fields"],
                     sum_line_ids=[], line_results=[{key: row[key] for key in (
                         "line_id", "status", "amount_inclusive", "amount_exclusive")} for row in result["lines"]],
                     result={key: result[key] for key in ("status", "recognized_inclusive", "recognized_exclusive")})
    return result


class MaterialDraftRepository:
    async def preview(self, connection, data):
        await require_permission(connection, "material.read")
        oid = data.get("opportunity_id")
        customer_id = str(data["customer_id"]) if data.get("customer_id") else None
        version = None
        old = {}
        if oid:
            await require_permission(connection, "opportunity.read", opportunity_id=oid)
            await require_permission(connection, "opportunity.update", opportunity_id=oid)
            parent = await connection.fetchrow("""SELECT customer_id::text,version_no FROM crm.opportunity
                WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL""", str(oid))
            if not parent:
                raise LookupError("商机不存在或不可见")
            version = parent["version_no"]
            customer_id = parent["customer_id"]
            require_version(version, data["expected_opportunity_version"])
            if data.get("customer_id") and str(data["customer_id"]) != parent["customer_id"]:
                raise ValueError("试算客户与商机不一致")
            rows = await connection.fetch("""SELECT id::text,material_id::text,catalog_snapshot::text,version_no
                FROM crm.opportunity_material WHERE opportunity_id=$1::uuid
                AND workspace_id=common.current_workspace_id()""", str(oid))
            old = {row["id"]: dict(row) for row in rows}
        else:
            await require_permission(connection, "opportunity.create")
            if not await customer_for_opportunity(connection, str(data["customer_id"])):
                raise LookupError("客户不存在或不可见")
        upstream = data.get("upstream_opportunity_id")
        if upstream:
            if oid and str(oid) == str(upstream):
                raise ValueError("上游商机不能为当前商机")
            await require_permission(connection, "opportunity.read", opportunity_id=upstream)
            upstream_parent = await connection.fetchrow("""SELECT trade_relation,customer_id::text FROM crm.opportunity
                WHERE id=$1::uuid AND workspace_id=common.current_workspace_id() AND deleted_at IS NULL
                AND security.authorization_opportunity('opportunity.read',id)""", str(upstream))
            if not upstream_parent or upstream_parent["trade_relation"] != "distributor":
                raise ValueError("请选择可见的上游总代商机")
            if upstream_parent["customer_id"] == customer_id:
                raise ValueError("下游商机须关联上游总代商机，并使用不同的终端客户")
        new_ids = sorted({str(line["material_id"]) for line in data["material_lines"]
                          if line.get("id") is None and line.get("material_id") is not None})
        catalog = {}
        if new_ids:
            rows = await connection.fetch("""SELECT m.id::text,(to_jsonb(m)-ARRAY[
                'workspace_id','reason','created_at','updated_at','updated_by_user_ref_id']
                ||jsonb_build_object('product_line_name',i.item_label,'catalog_price',m.catalog_price::text,
                                    'tax_rate',m.tax_rate::text))::text AS snapshot
                FROM crm.material m JOIN config.dictionary_item i ON i.id=m.product_line_id
                WHERE m.id=ANY($1::uuid[]) AND m.workspace_id=common.current_workspace_id()
                AND m.is_active AND i.is_active""", new_ids)
            catalog = {row["id"]: json.loads(row["snapshot"], parse_float=str) for row in rows}
        lines = []
        for original in data["material_lines"]:
            line = dict(original)
            lid, mid = line.get("id"), line.get("material_id")
            snapshot = None
            if lid:
                saved = old.get(str(lid))
                if not saved or str(mid) != saved["material_id"]:
                    raise ValueError("已有物料行不属于本商机或SKU已变更，请新增物料行")
                snapshot = json.loads(saved["catalog_snapshot"], parse_float=str)
                line["snapshot_source"] = "saved_line"
                line["version_no"] = saved["version_no"]
            elif mid:
                snapshot = catalog.get(str(mid))
                if snapshot is None:
                    raise LookupError("物料不存在、已停用或不可见，请刷新目录")
                line["snapshot_source"] = "current_catalog"
            else:
                line["snapshot_source"] = "missing"
            if snapshot is not None:
                try:
                    require_version(snapshot["version_no"], line.get("catalog_version_no"))
                except VersionConflict as exc:
                    if line["snapshot_source"] == "current_catalog":
                        raise VersionConflict("物料目录已更新，请重新选择物料并重新计算后再保存") from exc
                    raise
            line["catalog_snapshot"] = snapshot
            lines.append(line)
        return {**draft_reference(lines, year=data["year"], quarter=data["quarter"], relation=data["trade_relation"]),
                "reference_kind": "sales_forecast_draft_reference", "persisted": False,
                "draft_revision": data["draft_revision"], "period": {"year": data["year"], "quarter": data["quarter"]},
                "opportunity_id": str(oid) if oid else None, "opportunity_version": version}
