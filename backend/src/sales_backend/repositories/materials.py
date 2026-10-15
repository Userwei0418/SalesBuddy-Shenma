# ruff: noqa: S608 -- Column names below are a fixed server-side allowlist.
"""Tenant catalog and stable transaction lines; no financial fact inference."""

from decimal import Decimal
import hashlib
import json
from uuid import uuid4

from pydantic_core import to_jsonable_python

from sales_backend.db import json_value
from sales_backend.domain.concurrency import VersionConflict, require_version
from sales_backend.domain.materials import material_forecast_preview
from sales_backend.repositories.authorization_checks import require_permission

LINE_FIELDS = (
    "quantity", "unit_price", "usage", "expected_delivery_on", "expected_start_on", "expected_oa_on",
    "service_months", "expected_account_count", "expected_consumption_amount",
    "expected_consumption_year", "expected_consumption_quarter", "note",
)


def line_document(row):
    item = dict(row)
    item["catalog_snapshot"] = json_value(item["catalog_snapshot"])
    price = Decimal(str(item["catalog_snapshot"].get("catalog_price") or 0))
    item["discount_rate"] = Decimal(str(item["unit_price"])) / price if price else None
    return to_jsonable_python(item)


async def lock_material_catalog(connection):
    # Match the product-line directory writer. Row locks on crm.material would
    # apply management-only UPDATE RLS and incorrectly deny sales users.
    await connection.fetchval("""SELECT pg_advisory_xact_lock(hashtextextended(
        'material_catalog:'||common.current_workspace_id()::text,212))""")


class MaterialRepository:
    async def product_lines(self, connection, *, status="active"):
        if status == "all":
            await self.require_manage(connection)
        return json_value(await connection.fetchval("SELECT security.material_product_lines($1)", status == "all"))

    async def save_product_lines(self, connection, data):
        await self.require_manage(connection)
        return json_value(await connection.fetchval("SELECT security.save_material_product_lines($1,$2,$3::jsonb)",
                                                    data['version_no'], data['reason'], data['items']))

    async def require_manage(self, connection):
        if not await connection.fetchval("""SELECT security.management_actor()
          AND security.authorization_has('access.console') AND security.authorization_has('material.manage')"""):
            raise PermissionError("仅具有物料管理权限的运营或管理员可维护目录")

    async def catalog(self, connection, *, q=None, product_line_id=None, status="active", limit=50, offset=0):
        await require_permission(connection, "material.read")
        if status == "all":
            await self.require_manage(connection)
        rows = await connection.fetch("""SELECT to_jsonb(m)||jsonb_build_object('product_line_name',i.item_label,
          'catalog_price',m.catalog_price::text,'tax_rate',m.tax_rate::text) AS item,
          count(*) OVER() AS total FROM crm.material m JOIN config.dictionary_item i ON i.id=m.product_line_id
          WHERE m.workspace_id=common.current_workspace_id() AND ($1='all' OR (m.is_active AND i.is_active))
          AND ($2::text IS NULL OR strpos(lower(m.code||' '||m.name),lower($2))>0)
          AND ($3::uuid IS NULL OR m.product_line_id=$3::uuid)
          ORDER BY m.code,m.id LIMIT $4 OFFSET $5""", status, q, product_line_id, limit, offset)
        total = rows[0]["total"] if rows else await connection.fetchval("""SELECT count(*) FROM crm.material m
          JOIN config.dictionary_item i ON i.id=m.product_line_id WHERE m.workspace_id=common.current_workspace_id()
          AND ($1='all' OR (m.is_active AND i.is_active))
          AND ($2::text IS NULL OR strpos(lower(m.code||' '||m.name),lower($2))>0)
          AND ($3::uuid IS NULL OR m.product_line_id=$3::uuid)""", status, q, product_line_id)
        return {"items": [self.catalog_document(json_value(r["item"])) for r in rows],
                "total": total, "has_more": offset + len(rows) < total}

    @staticmethod
    def catalog_document(item):
        item = dict(item)
        for key in ("catalog_price", "tax_rate"):
            item[key] = str(item[key])
        item.pop("workspace_id", None)
        return item

    async def save(self, connection, actor, data, material_id=None):
        await self.require_manage(connection)
        await lock_material_catalog(connection)
        # Older clients do not know business_definition. Keep the currently
        # versioned notes on omission/null; an explicit {} clears them.
        data = {**data, 'rule_parameters': dict(data['rule_parameters'])}
        fields = ("code", "name", "product_line_id", "unit", "catalog_price", "currency", "tax_rate",
                  "recognition_rule", "rule_parameters", "is_active", "reason")
        if material_id:
            before = await connection.fetchrow("SELECT * FROM crm.material WHERE id=$1::uuid FOR UPDATE", material_id)
            if not before:
                raise LookupError("物料不存在或不在本公司")
            require_version(before["version_no"], data["version_no"])
            if before["code"] != data["code"]:
                raise ValueError("物料编码不可修改；请新增其他物料")
            if data['rule_parameters'].get('business_definition') is None:
                previous = json_value(before['rule_parameters']).get('business_definition')
                if previous is not None:
                    data['rule_parameters']['business_definition'] = previous
                else:
                    data['rule_parameters'].pop('business_definition', None)
            values = [data[k] for k in fields]
            assignments = ",".join(f"{field}=${i+1}" for i, field in enumerate(fields))
            row = await connection.fetchrow(f"""UPDATE crm.material SET {assignments},version_no=version_no+1,
              updated_by_user_ref_id=$12::uuid WHERE id=$13::uuid RETURNING *""", *values, actor.user_id, material_id)
        else:
            if data['rule_parameters'].get('business_definition') is None:
                data['rule_parameters'].pop('business_definition', None)
            values = [data[k] for k in fields]
            row = await connection.fetchrow(f"""INSERT INTO crm.material ({','.join(fields)},workspace_id,updated_by_user_ref_id)
              VALUES($1,$2,$3::uuid,$4,$5,$6,$7,$8,$9::jsonb,$10,$11,$12::uuid,$13::uuid) RETURNING *""",
                                           *values, actor.workspace_id, actor.user_id)
        item = dict(row)
        item["rule_parameters"] = json_value(item["rule_parameters"])
        item["product_line_name"] = await connection.fetchval("SELECT item_label FROM config.dictionary_item WHERE id=$1", row["product_line_id"])
        return to_jsonable_python(self.catalog_document(item))


class OpportunityMaterialRepository:
    async def rows(self, connection, opportunity_id):
        return [line_document(r) for r in await connection.fetch(
            "SELECT * FROM crm.opportunity_material WHERE opportunity_id=$1::uuid ORDER BY line_no,id", str(opportunity_id))]

    async def read(self, connection, opportunity_id):
        await require_permission(connection, "opportunity.read", opportunity_id=opportunity_id)
        parent = await connection.fetchrow("""SELECT id::text AS opportunity_id,name,customer_id::text,version_no,trade_relation,
          upstream_opportunity_id::text FROM crm.opportunity WHERE id=$1::uuid AND deleted_at IS NULL""", str(opportunity_id))
        if not parent:
            raise LookupError("商机不存在或不可见")
        rows = await self.rows(connection, opportunity_id)
        return {**dict(parent), "items": [r for r in rows if r["is_active"]], "retired_items": [r for r in rows if not r["is_active"]]}

    async def preview(self, connection, opportunity_id, year, quarter):
        data = await self.read(connection, opportunity_id)
        result = material_forecast_preview([{**r, "trade_relation": data["trade_relation"]} for r in data["items"]], year, quarter)
        reference = {**result, 'reference_kind': 'sales_forecast_reference',
                     'opportunity_id': str(opportunity_id), 'period': {'year': year, 'quarter': quarter},
                     "opportunity_version": data["version_no"],
                     "material_versions": {r["id"]: r["version_no"] for r in data["items"]}}
        # Content identity of this scoped reference, not an authorization token,
        # actual-finance decision fingerprint, or a new financial ledger.
        raw = json.dumps(reference, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        return {**reference, 'reference_fingerprint': hashlib.sha256(raw.encode('utf-8')).hexdigest()}

    async def prepare(self, connection, opportunity_id, data, before):
        old = await self.rows(connection, opportunity_id) if before else []
        relation = data.get("trade_relation") or (before or {}).get("trade_relation")
        upstream = (data.get("upstream_opportunity_id") if data.get("trade_relation") is not None
                    else (before or {}).get("upstream_opportunity_id"))
        if relation != "downstream": upstream = None
        if relation == "downstream" and not upstream:
            raise ValueError("下游交易必须选择上游总代商机")
        if upstream:
            await require_permission(connection, "opportunity.read", opportunity_id=upstream)
        lines = data.get("material_lines")
        if lines and not relation:
            raise ValueError("使用物料时请明确选择交易关系")
        changes = []
        old_relation = (before or {}).get("trade_relation")
        old_upstream = (before or {}).get("upstream_opportunity_id")
        if (relation, str(upstream or "")) != (old_relation, str(old_upstream or "")):
            changes.append({"field": "trade_relation", "label": "交易关系", "before": {"relation": old_relation, "upstream": old_upstream},
                            "after": {"relation": relation, "upstream": upstream}})
        if lines is not None:
            old_by_id = {r["id"]: r for r in old}
            new_ids = sorted({str(line["material_id"]) for line in lines if not line.get("id")})
            catalog_versions = {}
            if new_ids:
                # Hold through the caller's atomic opportunity/forecast/line
                # transaction so the insert trigger captures this same catalog.
                await lock_material_catalog(connection)
                catalog = await connection.fetch("""SELECT m.id::text,m.version_no FROM crm.material m
                    JOIN config.dictionary_item i ON i.id=m.product_line_id
                    WHERE m.id=ANY($1::uuid[]) AND m.workspace_id=common.current_workspace_id()
                    AND m.is_active AND i.is_active""", new_ids)
                catalog_versions = {row["id"]: row["version_no"] for row in catalog}
            seen = set()
            for line in lines:
                lid = str(line["id"]) if line.get("id") else None
                if lid:
                    if lid in seen or lid not in old_by_id:
                        raise ValueError("物料行标识重复或不属于本商机")
                    if str(line["material_id"]) != old_by_id[lid]["material_id"]:
                        raise ValueError("已有交易行不能更换SKU，请新增行")
                    require_version(old_by_id[lid]["catalog_snapshot"].get("version_no"),
                                    line.get("catalog_version_no"))
                    seen.add(lid)
                else:
                    mid = str(line["material_id"])
                    if mid not in catalog_versions:
                        raise LookupError("物料不存在、已停用或不可见，请刷新目录")
                    try:
                        require_version(catalog_versions[mid], line.get("catalog_version_no"))
                    except VersionConflict as exc:
                        raise VersionConflict("物料目录已更新，请重新选择物料并重新计算后再保存") from exc
            def comparable(rows):
                numeric = {"quantity", "unit_price", "expected_consumption_amount"}
                return [{key: (Decimal(str(r[key])) if key in numeric else str(r[key]))
                         if r.get(key) is not None else None
                         for key in ("id", "material_id", *LINE_FIELDS)} for r in rows]
            if comparable(lines) != comparable([r for r in old if r["is_active"]]):
                changes.append({"field": "material_lines", "label": "交易物料", "before": old, "after": to_jsonable_python(lines)})
        return relation, str(upstream) if upstream else None, old, changes

    async def save(self, connection, actor, opportunity_id, updates, old):
        if updates is None: return
        old_by_id = {r["id"]: r for r in old}
        active = []
        for index, item in enumerate(updates, 1):
            lid = str(item["id"]) if item.get("id") else str(uuid4())
            active.append(lid)
            args = [item.get(k) for k in LINE_FIELDS]
            if lid in old_by_id:
                assignments = ",".join(f"{field}=${i+1}" for i, field in enumerate(LINE_FIELDS))
                await connection.execute(f"""UPDATE crm.opportunity_material SET {assignments},line_no=$13,is_active=true,
                  version_no=version_no+1,updated_by_user_ref_id=$14::uuid WHERE id=$15::uuid AND opportunity_id=$16::uuid""",
                                         *args, index, actor.user_id, lid, str(opportunity_id))
            else:
                await connection.execute(f"""INSERT INTO crm.opportunity_material
                  ({','.join(LINE_FIELDS)},line_no,updated_by_user_ref_id,id,opportunity_id,workspace_id,material_id,catalog_snapshot)
                  VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::uuid,$15::uuid,$16::uuid,$17::uuid,$18::uuid,'{{}}')""",
                                         *args, index, actor.user_id, lid, str(opportunity_id), actor.workspace_id, item["material_id"])
        await connection.execute("""UPDATE crm.opportunity_material SET is_active=false,version_no=version_no+1,
          updated_by_user_ref_id=$1::uuid WHERE opportunity_id=$2::uuid AND is_active AND NOT(id=ANY($3::uuid[]))""",
                                 actor.user_id, str(opportunity_id), active)
