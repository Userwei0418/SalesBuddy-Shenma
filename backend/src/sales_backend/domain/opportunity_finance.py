"""One resolver for API and Feishu: a period has one chosen actual source.

Forecasts remain separate. Never distribute customer-only actuals to an opportunity,
add a historical total to its entries, or turn absent evidence into zero.
"""

import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256

RULE_VERSION = 1
ENTRY_FIELDS = (
    "id",
    "opportunity_id",
    "kind",
    "amount",
    "occurred_on",
    "voided_at",
    "void_reason",
    "source_ref",
    "created_at",
    "updated_at",
)
HISTORY_FIELDS = (
    "id",
    "opportunity_id",
    "year",
    "quarter",
    "kind",
    "raw_amount",
    "source_unit",
    "tax_basis",
    "source_field",
    "source_record_id",
    "import_batch_id",
)
KINDS = ("recognized", "collection")


def decimal_text(value):
    if value is None:
        return None
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("财务金额必须为有限数值")
    return format(number.normalize(), "f") if number else "0"


def _canonical(row, fields):
    result = {}
    for field in fields:
        value = row.get(field)
        if field in {"amount", "raw_amount"}:
            value = decimal_text(value)
        elif value is not None and field.endswith("_at"):
            stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            value = stamp.astimezone(UTC).isoformat() if stamp.tzinfo else stamp.isoformat()
        elif value is not None:
            value = str(value) if field not in {"year", "quarter"} else int(value)
        result[field] = value
    return result


def _entry_canonical(row):
    result = _canonical(row, ENTRY_FIELDS)
    # Keep pre-V201 confirmations stable when the old tax basis is unknown.
    if row.get("tax_basis") not in (None, "unknown"):
        result["tax_basis"] = row["tax_basis"]
    return result


def quarter_field(key):
    match = re.fullmatch(r"quarter_(20\d{2}|2100)_q([1-4])_(recognized_exclusive|collection|quality)", key)
    return (int(match[1]), int(match[2]), match[3]) if match else None


def source_fingerprint(workspace_id, opportunity_id, year, quarter, kind, entries, history):
    document = {
        "rule_version": RULE_VERSION,
        "workspace_id": str(workspace_id),
        "opportunity_id": str(opportunity_id),
        "year": year,
        "quarter": quarter,
        "kind": kind,
        "entries": sorted((_entry_canonical(r) for r in entries), key=lambda r: r["id"]),
        "history": sorted((_canonical(r, HISTORY_FIELDS) for r in history), key=lambda r: r["id"]),
    }
    return sha256(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _history_amount(row):
    multiplier = {"cny": Decimal(1), "wan_cny": Decimal(10000)}.get(row.get("source_unit"))
    return None if multiplier is None or row.get("raw_amount") is None else Decimal(str(row["raw_amount"])) * multiplier


def financial_overview(raw):
    """Accept the same scoped, single-snapshot raw arrays from SQL and sync.

    Money is returned as canonical decimal strings so fingerprints and worker
    JSON encoding never depend on binary floats. API clients may display yuan.
    """
    if 'finance_effective' in raw:
        from sales_backend.domain.effective_finance import effective_financial_overview
        return effective_financial_overview(raw)
    workspace, opportunity = str(raw["workspace_id"]), str(raw["id"])
    buckets = defaultdict(lambda: {"entries": [], "history": [], "decision": None})
    for row in raw.get("finance_actuals") or []:
        if str(row.get("opportunity_id")) != opportunity:
            continue
        when = date.fromisoformat(str(row["occurred_on"])[:10])
        buckets[(when.year, (when.month - 1) // 3 + 1, row["kind"])]["entries"].append(row)
    for row in raw.get("finance_history") or []:
        if str(row.get("opportunity_id")) == opportunity:
            buckets[(int(row["year"]), int(row["quarter"]), row["kind"])]["history"].append(row)
    for row in raw.get("finance_decisions") or []:
        buckets[(int(row["year"]), int(row["quarter"]), row["kind"])]["decision"] = row
    periods = []
    for (year, quarter, kind), facts in sorted(buckets.items()):
        if kind not in KINDS:
            raise ValueError("未知财务金额类型")
        entries, history, decision = facts["entries"], facts["history"], facts["decision"]
        entries = sorted(entries, key=lambda r: str(r["id"]))
        history = sorted(history, key=lambda r: str(r["id"]))
        active = [r for r in entries if not r.get("voided_at")]
        digest = source_fingerprint(workspace, opportunity, year, quarter, kind, entries, history)
        entry_amount = sum((Decimal(str(r["amount"])) for r in active), Decimal(0)) if active else None
        amount, source, selected, status = None, None, None, "missing"
        if decision:
            if decision["source_fingerprint"] != digest:
                status = "stale_confirmation"
            elif decision["source"] == "entries" and active:
                amount, source, status = entry_amount, "entries", "confirmed"
            elif decision["source"] == "historical":
                selected = next((r for r in history if str(r["id"]) == str(decision.get("selected_history_id"))), None)
                if selected and _history_amount(selected) is not None:
                    amount, source, status = _history_amount(selected), "historical", "confirmed"
                else:
                    status = "stale_confirmation"
            else:
                status = "stale_confirmation"
        elif (active and history) or len(history) > 1:
            status = "needs_confirmation"
        elif active:
            amount, source, status = entry_amount, "entries", "single_source"
        elif len(history) == 1:
            selected = history[0]
            amount = _history_amount(selected)
            source, status = "historical", "single_source" if amount is not None else "needs_confirmation"
        tax_unknown = bool(selected and selected.get("tax_basis") not in {"inclusive", "exclusive", "not_applicable"})
        tax_basis = selected.get("tax_basis") if selected else None
        if source == "entries":
            bases = {r.get("tax_basis", "unknown") for r in active}
            tax_basis = next(iter(bases)) if len(bases) == 1 else "mixed"
            tax_unknown = kind == "recognized" and tax_basis not in {"inclusive", "exclusive"}
        periods.append(
            {
                "year": year,
                "quarter": quarter,
                "kind": kind,
                "amount": decimal_text(amount),
                "source": source,
                "status": status,
                "source_fingerprint": digest,
                "entry_amount": decimal_text(entry_amount),
                "entries": entries,
                "history": [{**r, "amount_yuan": decimal_text(_history_amount(r))} for r in history],
                "decision": decision,
                "tax_basis": tax_basis,
                "tax_unknown": tax_unknown,
            }
        )
    totals = {}
    for kind in KINDS:
        selected = [p for p in periods if p["kind"] == kind]
        conflicts = sum(p["status"] in {"needs_confirmation", "stale_confirmation"} for p in selected)
        resolved = [Decimal(p["amount"]) for p in selected if p["amount"] is not None]
        subtotal = decimal_text(sum(resolved, Decimal(0))) if resolved else None
        totals[kind] = {
            "amount": None if conflicts else subtotal,
            "confirmed_subtotal": subtotal,
            "conflict_period_count": conflicts,
            "resolved_period_count": len(resolved),
            "tax_unknown_period_count": sum(p["tax_unknown"] for p in selected),
        }
    conflict_count = sum(t["conflict_period_count"] for t in totals.values())
    any_data = any(t["amount"] is not None or t["confirmed_subtotal"] is not None for t in totals.values())
    quality = "待核对" if conflict_count else "来源明确" if any_data else "未登记"
    confirmed_count = sum(p["status"] == "confirmed" for p in periods)
    if confirmed_count:
        quality += f"；{confirmed_count} 个期间已人工确认"
    if any(p["status"] == "stale_confirmation" for p in periods):
        quality += "；来源变化，原确认已失效"
    if any(p["tax_unknown"] for p in periods):
        quality += "；税口径待核实"
    if {p["tax_basis"] for p in periods if p["amount"] is not None} >= {"inclusive", "exclusive"}:
        quality += "；历史税口径混合，未换算"
    return {
        "schema_version": RULE_VERSION,
        "currency": "CNY",
        "opportunity_id": opportunity,
        "totals": totals,
        "conflict_period_count": conflict_count,
        "quality": quality,
        "periods": periods,
        "coverage_note": (
            "累计口径为全部已登记实绩，按原发生日/原季度归期；仅做元/万元换算，不换算税额。"
            "未登记不视为零，客户级未关联商机的金额不分摊。"
        ),
    }


def financial_projection(raw, field_names=()):
    result = financial_overview(raw)
    values = {"finance_conflict_period_count": result["conflict_period_count"], "finance_quality": result["quality"]}
    for kind in KINDS:
        values[f"actual_{kind}_total"] = result["totals"][kind]["amount"]
        values[f"actual_{kind}_confirmed_subtotal"] = result["totals"][kind]["confirmed_subtotal"]
    periods = {(p["year"], p["quarter"], p["kind"]): p for p in result["periods"]}
    for key in field_names:
        parsed = quarter_field(key)
        if not parsed:
            continue
        year, quarter, metric = parsed
        recognized = periods.get((year, quarter, "recognized"))
        collection = periods.get((year, quarter, "collection"))
        if metric == "quality":
            notes = []
            for label, period in (("确收", recognized), ("回款", collection)):
                if not period or period["status"] == "missing":
                    notes.append(label + "未登记")
                elif period["amount"] is None:
                    notes.append(label + "来源待核对")
                elif label == "确收" and period["tax_basis"] != "exclusive":
                    notes.append("确收税口径待核对（未换算）")
                else:
                    notes.append(label + "来源明确")
            values[key] = "；".join(notes)
        else:
            period = recognized if metric == "recognized_exclusive" else collection
            valid = period and period["amount"] is not None
            if metric == "recognized_exclusive":
                valid = valid and period["tax_basis"] == "exclusive"
            values[key] = decimal_text(Decimal(period["amount"]) / 10000) if valid else None
    return values
