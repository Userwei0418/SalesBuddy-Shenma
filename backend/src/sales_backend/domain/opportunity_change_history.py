"""Read saved evidence without resolving historical identities from today's directories."""

import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sales_backend.domain.opportunity_field_catalog import ENUM_OPTIONS, FIELD_CATALOG, PARTICIPANT_ROLES

MISSING = object()
UNAVAILABLE = "历史值未记录或格式不可识别"
MAX_FIELDS = 200
MAX_ROWS = 100
ROLE_LABELS = {row["value"]: row["label"] for row in PARTICIPANT_ROLES}
ENUMS = {key: dict(rows) for key, rows in ENUM_OPTIONS.items()}
LABELS = {
    key: FIELD_CATALOG[key]["label"]
    for key in (
        "name",
        "amount",
        "expected_close_date",
        "product_line",
        "follow_up_plan",
        "customer_source",
        "priority",
        "is_key",
        "trial_status",
        "summary",
        "note",
        "ppl_status",
        "external_system_status",
        "primary_fde_id",
        "participants",
        "fde_support_stage",
        "fde_progress",
        "sales_channel",
        "stage",
        "status",
    )
}
LABELS.update(
    partner_name="所属伙伴",
    partner_id="所属伙伴标识",
    fde_members="长期FDE名单",
    fde_member_ids="长期协助FDE",
    intent_product_line_ids="意向产品线",
    material_lines="交易物料",
    trade_relation="交易关系",
    material_reason="物料维护说明",
    field_edit_reason="商机字段维护说明",
    operational_actual="运营期间实际",
    collection_actual="实际回款（元）",
    recognized_actual="季度实际不含税确收（元）",
)
LEGACY_LABELS = {
    "商机阶段": "stage",
    "商机名称": "name",
    "ACV（万元）": "amount",
    "预计关单日期": "expected_close_date",
    "所属伙伴": "partner_name",
    "产品线": "product_line",
    "跟进计划": "follow_up_plan",
}
FORECAST_LABEL = re.compile(
    r"(?P<year>20\d{2}|2100) Q(?P<quarter>[1-4]) "
    r"(?P<metric>预测含税确收（万元）|预测回款（万元）|回款信心度)"
)
FORECAST_KEYS = {"预测含税确收（万元）": "recognized", "预测回款（万元）": "collection", "回款信心度": "confidence"}
ID_FIELDS = {"primary_fde_id", "partner_id", "intent_product_line_ids", "fde_members", "fde_member_ids"}
PEOPLE_FIELDS = {"primary_fde_id", "fde_members", "fde_member_ids", "participants"}
REASON_FIELDS = {"material_reason", "field_edit_reason"}
MATERIAL_LABELS = {
    "quantity": "数量",
    "unit_price": "成交单价（元）",
    "usage": "用途",
    "expected_delivery_on": "预计交付日期",
    "expected_start_on": "预计服务开始日期",
    "expected_oa_on": "预计OA日期",
    "service_months": "服务月数",
    "expected_account_count": "预计账号数",
    "expected_consumption_amount": "预计消耗金额（元）",
    "expected_consumption_year": "预计消耗年份",
    "expected_consumption_quarter": "预计消耗季度",
    "note": "备注",
    "is_active": "在用",
}


def _plain(value):
    if isinstance(value, str):
        return value.replace("\x00", "").encode("utf-8", errors="replace").decode("utf-8")
    if type(value) is bool:
        return "是" if value else "否"
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return str(value)
    return None


def _id(value):
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


@dataclass
class Budget:
    """An event cannot expand into unbounded rows/text even with malformed JSON."""

    rows: int = 400
    chars: int = 80000

    def text(self, value, limit):
        text = _plain(value)
        if text is None:
            return None, False
        bound = min(limit, self.chars)
        self.chars -= min(len(text), bound)
        return text[:bound], len(text) > bound


def unavailable(message=UNAVAILABLE):
    return dict(kind="unavailable", status="unavailable", rows=[], empty_label=message, truncated=False)


def _value(kind, rows, budget, *, empty="无", truncated=False):
    result = []
    for row in rows[:MAX_ROWS]:
        if budget.rows <= 0:
            truncated = True
            break
        budget.rows -= 1
        name, cut = budget.text(row.get("name"), 5000)
        detail, cut_detail = budget.text(row.get("detail"), 4000)
        truncated |= cut or cut_detail
        result.append(
            dict(
                name=name,
                id=row.get("id"),
                missing_name=row.get("missing_name", False),
                id_label=row.get("id_label"),
                detail=detail,
            )
        )
    truncated |= len(rows) > len(result)
    return dict(kind=kind, status="recorded" if rows else "empty", rows=result, empty_label=empty, truncated=truncated)


def _scalar(value, budget, key):
    if value is MISSING:
        return unavailable()
    if value is None or value == "":
        return _value("scalar", [], budget, empty="未填写")
    text = _plain(value)
    if text is None:
        return unavailable()
    if key == "is_key" and type(value) is not bool:
        return unavailable()
    numeric = key == "amount" or (key.startswith("forecast.") and not key.endswith(".confidence"))
    if numeric and text != "未填写":
        if type(value) is bool or len(text) > 80:
            return unavailable()
        try:
            if not Decimal(text).is_finite():
                return unavailable()
        except InvalidOperation:
            return unavailable()
    elif key != "is_key" and not isinstance(value, str):
        return unavailable()
    text = ENUMS.get(key, {}).get(text, text)
    return _value("scalar", [dict(name=text)], budget, empty="未填写")


def _snapshot_names(change, side, ids):
    snapshot = change.get("display_snapshot")
    if (
        not isinstance(snapshot, dict)
        or type(snapshot.get("version")) is not int
        or snapshot["version"] != 1
        or snapshot.get("basis") != "directory_at_change"
    ):
        return {}
    rows = snapshot.get(side)
    if (
        not isinstance(rows, list)
        or len(rows) != len(ids)
        or len(set(ids)) != len(ids)
        or any(not isinstance(row, dict) or _id(row.get("id")) is None for row in rows)
    ):
        return {}
    by_id = {_id(row["id"]): row for row in rows}
    if len(by_id) != len(rows) or set(by_id) != set(ids):
        return {}
    return {
        key: row["name"]
        for key, row in by_id.items()
        if row.get("status") == "recorded" and isinstance(row.get("name"), str) and row["name"]
    }


def _references(value, change, side, key, budget):
    if value is MISSING:
        return unavailable()
    single = key in {"primary_fde_id", "partner_id"}
    values = ([] if value is None else [value]) if single else value
    if not isinstance(values, list) or any(_id(item) is None for item in values):
        return unavailable()
    ids = [_id(item) for item in values]
    names = _snapshot_names(change, side, ids)
    people = key in PEOPLE_FIELDS
    return _value(
        "people" if people else "list",
        [
            dict(
                id=identifier,
                name=names.get(identifier),
                missing_name=identifier not in names,
                id_label="人员ID" if people else "产品线ID" if key == "intent_product_line_ids" else "伙伴ID",
            )
            for identifier in ids[:MAX_ROWS]
        ],
        budget,
        truncated=len(ids) > MAX_ROWS,
    )


def _participants(value, change, side, budget):
    if not isinstance(value, dict) or any(
        _id(key) is None or not isinstance(role, str) or role not in ROLE_LABELS for key, role in value.items()
    ):
        return unavailable()
    ids = [_id(key) for key in value]
    names = _snapshot_names(change, side, ids)
    return _value(
        "people",
        [
            dict(
                id=_id(identifier),
                name=names.get(_id(identifier)),
                missing_name=_id(identifier) not in names,
                id_label="人员ID",
                detail=ROLE_LABELS[role],
            )
            for identifier, role in list(value.items())[:MAX_ROWS]
        ],
        budget,
        truncated=len(value) > MAX_ROWS,
    )


def _materials(value, budget):
    if not isinstance(value, list):
        return unavailable()
    rows = []
    for item in value[:MAX_ROWS]:
        if not isinstance(item, dict) or _id(item.get("material_id")) is None:
            return unavailable()
        identifier = _id(item["material_id"])
        snapshot = item.get("catalog_snapshot")
        name = None
        if (
            isinstance(snapshot, dict)
            and _id(snapshot.get("id")) == identifier
            and isinstance(snapshot.get("name"), str)
            and snapshot["name"]
        ):
            name = snapshot["name"]
        details = []
        for key, label in MATERIAL_LABELS.items():
            if key not in item:
                continue
            text = _plain(item[key])
            if item[key] is None:
                text = "未填写"
            if text is None:
                return unavailable()
            if key == "usage":
                text = {"self_use": "自用", "resale": "转售"}.get(text, text)
            details.append(f"{label}：{text}")
        rows.append(
            dict(id=identifier, name=name, missing_name=name is None, id_label="物料ID", detail="；".join(details))
        )
    return _value("materials", rows, budget, truncated=len(value) > MAX_ROWS)


def _project(value, key, change, side, budget, *, actual_allowed):
    if key in ID_FIELDS:
        return _references(value, change, side, key, budget)
    if key == "participants":
        return _participants(value, change, side, budget)
    if key == "material_lines":
        return _materials(value, budget)
    if key == "trade_relation":
        if (
            not isinstance(value, dict)
            or "relation" not in value
            or value.get("relation") not in (None, "direct", "distributor", "downstream")
        ):
            return unavailable()
        label = {None: "未填写", "direct": "直签", "distributor": "总代交易", "downstream": "下游交易"}[
            value.get("relation")
        ]
        upstream = value.get("upstream")
        if upstream is not None and _id(upstream) is None:
            return unavailable()
        rows = [dict(name=label)]
        if upstream:
            rows.append(dict(id=_id(upstream), name=None, missing_name=True, id_label="上游商机ID"))
        return _value("list", rows, budget)
    if key in REASON_FIELDS:
        return _scalar(value.get("reason", MISSING) if isinstance(value, dict) else value, budget, key)
    if key == "customer_source":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return unavailable()
        return _value(
            "list",
            [dict(name=ENUMS[key].get(item, item)) for item in value[:MAX_ROWS]],
            budget,
            truncated=len(value) > MAX_ROWS,
        )
    if key == "recognized_actual":
        if not actual_allowed:
            return {**unavailable("无权查看此字段（财务实际）"), "status": "restricted"}
        if not isinstance(value, dict) or not isinstance(value.get("items"), list):
            return unavailable()
        rows = []
        year, quarter = change.get("year"), change.get("quarter")
        if type(year) is int and 2000 <= year <= 2100 and type(quarter) is int and 1 <= quarter <= 4:
            rows.append(dict(name="季度", detail=f"{year} Q{quarter}"))
        for item in value["items"][:MAX_ROWS]:
            if not isinstance(item, dict):
                continue
            name = _plain(item.get("material_name")) or "物料"
            amount = _plain(item.get("amount"))
            rows.append(dict(name=name, detail=amount if amount is not None else "未登记"))
            for field, label in (("source_ref", "登记依据"), ("note", "备注")):
                text = _plain(item.get(field))
                if text:
                    rows.append(dict(name=label, detail=text))
        return _value("list", rows, budget, truncated=len(value["items"]) > MAX_ROWS)
    if key == "collection_actual":
        # This is the same narrow collection projection available to any current
        # opportunity reader; do not expose revision or arbitrary finance facts.
        if not isinstance(value, dict) or "amount" not in value:
            return unavailable()
        rows = []
        year, quarter = change.get("year"), change.get("quarter")
        if type(year) is int and 2000 <= year <= 2100 and type(quarter) is int and 1 <= quarter <= 4:
            rows.append(dict(name="季度", detail=f"{year} Q{quarter}"))
        amount = _plain(value["amount"])
        rows.append(dict(name="累计实际回款（元）", detail=amount if amount is not None else "未登记或暂无有效金额"))
        source = {"operations": "人工登记", "entries": "逐笔记录", "historical": "历史汇总"}.get(value.get("source"))
        if source:
            rows.append(dict(name="来源", detail=source))
        for field, label in (("source_ref", "登记依据"), ("note", "备注")):
            text = _plain(value.get(field))
            if text:
                rows.append(dict(name=label, detail=text))
        return _value("list", rows, budget)
    if key == "operational_actual":
        if not actual_allowed:
            return {**unavailable("无权查看此字段（财务实际）"), "status": "restricted"}
        # This event stores an entire finance read model. Only an explicit
        # period summary is displayed here; never expose arbitrary nested facts.
        if not isinstance(value, dict):
            return unavailable()
        labels = {
            "year": "年份",
            "quarter": "季度",
            "kind": "类型",
            "source": "来源",
            "amount": "金额（元）",
            "effective_amount": "有效金额（元）",
            "status": "状态",
        }
        rows = []
        for field, label in labels.items():
            if field in value and _plain(value[field]) is not None:
                rows.append(dict(name=label, detail=_plain(value[field])))
        return _value("list", rows, budget) if rows else unavailable()
    return _scalar(value, budget, key)


def _field_identity(change):
    key = change.get("field")
    if isinstance(key, str):
        return (key, LABELS[key]) if key in LABELS else ("unknown", "未识别的历史字段")
    label = change.get("label")
    if not isinstance(label, str):
        return "unknown", "未识别的历史字段"
    if label in LEGACY_LABELS:
        return LEGACY_LABELS[label], label
    match = FORECAST_LABEL.fullmatch(label)
    if match:
        return f"forecast.{match['year']}.q{match['quarter']}.{FORECAST_KEYS[match['metric']]}", label
    return "unknown", "未识别的历史字段"


def change_document(row, *, actual_allowed=False):
    changes = row["changes"]
    if isinstance(changes, str):
        try:
            changes = json.loads(changes, parse_float=str) if len(changes) <= 2_000_000 else None
        except (ValueError, RecursionError):
            changes = None
    budget = Budget()
    fields, missing, reason = [], 0, None
    if not isinstance(changes, list):
        changes = [None]
    for change in changes[:MAX_FIELDS]:
        if not isinstance(change, dict):
            change = {}
        key, label = _field_identity(change)
        if key == "unknown":
            before = after = unavailable()
        else:
            before, after = (
                _project(change.get(side, MISSING), key, change, side, budget, actual_allowed=actual_allowed)
                for side in ("before", "after")
            )
        incomplete = any(
            v["status"] in {"unavailable", "restricted"} or v["truncated"] or any(r["missing_name"] for r in v["rows"])
            for v in (before, after)
        )
        missing += int(incomplete)
        fields.append(dict(field=key, label=label, before=before, after=after))
        if key in REASON_FIELDS and after["status"] == "recorded" and after["rows"]:
            reason = after["rows"][0]["name"]
    missing += max(0, len(changes) - MAX_FIELDS)
    actor_id = row["actor_id"]
    actor_name = row.get("actor_name")
    actor_name = actor_name[:200] if isinstance(actor_name, str) and actor_name else None
    no_details = not fields or all(
        all(field[side]["status"] in {"unavailable", "restricted"} for side in ("before", "after")) for field in fields
    )
    return dict(
        id=row["id"],
        version_no=row["version_no"],
        created_at=row["created_at"],
        actor=dict(id=actor_id, name=actor_name, name_status="current" if actor_name else "unavailable"),
        reason=reason[:1000] if reason else None,
        fields=fields,
        detail_status="unavailable" if no_details else "partial" if missing else "recorded",
        unavailable_field_count=missing,
    )
