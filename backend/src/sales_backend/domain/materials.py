"""Deterministic material references; never writes sales plans or actual facts."""

from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, getcontext
from hashlib import sha256
import json

CENT = Decimal("0.01")
# Any pricing, allocation, OA-window or rounding semantics change must publish
# a new calculator version; old catalog/line versions are independent of it.
CALCULATOR_VERSION = "material_reference.v1"
TRACE_ENCODING = "utf8_json_sorted_keys_compact_decimal_strings.v1"


def money(value):
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def text(value):
    return None if value is None else format(money(value), ".2f")


def line_amount(row, *, trace=None):
    # quantity is billed transaction units; unit text, months and account count
    # never cause another multiplication of the agreed transaction total.
    raw = Decimal(str(row["quantity"])) * Decimal(str(row["unit_price"]))
    total = money(raw)
    if trace is not None:
        trace.update(status="calculated", formula="quantity * unit_price", unrounded_total=format(raw, "f"),
                     total_inclusive=text(total), quantum="0.01", rounding="ROUND_HALF_UP",
                     explanation="交易数量乘以成交单价；单位文字、服务月数和账号数不再重复乘入成交总额")
    return total


def _date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _month(day):
    return day.year * 12 + day.month - 1


def _month_label(value):
    year, month = divmod(value, 12)
    return f"{year:04d}-{month + 1:02d}"


def _trace_value(value):
    """Trace/hash canonical form, including every frozen catalog field.

    Numerical values are decimal strings, never floats in output. Original
    decimal scale is retained; no current catalog or guessed default is read.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, (Decimal, int, float)):
        return format(Decimal(str(value)), "f")
    if isinstance(value, dict):
        return {str(key): _trace_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_trace_value(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return str(value)  # UUIDs in direct repository/domain callers.


def _trace_hash(value):
    return sha256(json.dumps(_trace_value(value), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _period(year, quarter, months):
    return {"year": str(year), "quarter": str(quarter),
            "start_on": _month_label(months[0]) + "-01",
            "end_exclusive_on": _month_label(months[-1] + 1) + "-01",
            "months": [_month_label(month) for month in months]}


def _line_trace(row, year, quarter, months):
    snapshot = row["catalog_snapshot"]
    parameters = snapshot.get("rule_parameters") or {}
    # Human-authored business descriptions never select or change an algorithm.
    # Old snapshots deliberately keep their missing explanation; do not copy a
    # newer catalog description into an existing transaction line.
    definition = parameters.get("business_definition")
    rule = {"recognition_rule": snapshot["recognition_rule"],
            "parameters": {key: value for key, value in parameters.items() if key != "business_definition"},
            "tax_rate": snapshot.get("tax_rate"), "calculator_version": CALCULATOR_VERSION,
            "money_quantum": "0.01", "rounding": "ROUND_HALF_UP"}
    return {
        "calculator_version": CALCULATOR_VERSION, "canonical_encoding": TRACE_ENCODING,
        "period": _period(year, quarter, months),
        "identity": {"line_id": str(row["id"]), "line_version": _trace_value(row.get("version_no")),
                     "catalog_id": _trace_value(snapshot.get("id", row.get("material_id"))),
                     "catalog_version": _trace_value(snapshot.get("version_no"))},
        "catalog_snapshot": _trace_value(snapshot), "catalog_snapshot_sha256": _trace_hash(snapshot),
        "rule": _trace_value(rule), "rule_sha256": _trace_hash(rule),
        "business_definition": {"status": "maintained" if definition is not None else "historical_description_not_maintained",
                                "participates_in_calculation": False, "value": _trace_value(definition)},
        "inputs": {key: _trace_value(row.get(key)) for key in (
            "quantity", "unit_price", "usage", "trade_relation", "expected_delivery_on", "expected_start_on",
            "expected_oa_on", "service_months", "expected_account_count", "expected_consumption_amount",
            "expected_consumption_year", "expected_consumption_quarter")},
        "decimal_context": {"precision": str(getcontext().prec), "rounding": getcontext().rounding,
                            "emin": str(getcontext().Emin), "emax": str(getcontext().Emax)},
        "pricing": {"status": "not_calculated"}, "monthly_allocation": None,
        "tax": {"status": "not_calculated"}, "steps": [],
    }


def _finish_trace(trace, result):
    trace["calculation_status"] = "calculated" if result["status"] == "complete" else "not_calculated"
    trace["not_calculated_reasons"] = (["distributor_resale"] if result["status"] == "excluded"
                                       else list(result["missing_fields"]))
    trace["result"] = {key: result.get(key) for key in ("status", "amount_inclusive", "amount_exclusive")}
    return {**result, "calculation_trace": trace}


def preview_line(row, year, quarter):
    snapshot = row["catalog_snapshot"]
    rule = snapshot["recognition_rule"]
    parameters = snapshot.get("rule_parameters") or {}
    missing = []
    inclusive = None
    months = [year * 12 + (quarter - 1) * 3 + i for i in range(3)]
    trace = _line_trace(row, year, quarter, months)
    if row.get("usage") == "resale" and row.get("trade_relation") == "distributor":
        trace["steps"].append({"step": "eligibility", "status": "excluded",
                               "explanation": "总代交易的转售物料不在本商机计算确收，确收在下游商机登记"})
        return _finish_trace(trace, {"line_id": str(row["id"]), "status": "excluded", "amount_inclusive": None,
                "amount_exclusive": None, "missing_fields": [], "reason": "转售确收在下游商机登记"})
    if rule == "manual":
        missing.append("manual_sales_input")
        trace["steps"].append({"step": "manual", "status": "not_calculated",
                               "explanation": "人工规则不从物料推断参考确收，等待销售人工输入"})
    elif rule == "delivery":
        if not row.get("expected_delivery_on"):
            missing.append("expected_delivery_on")
        else:
            delivery = _date(row["expected_delivery_on"])
            included = _month(delivery) in months
            inclusive = line_amount(row, trace=trace["pricing"]) if included else Decimal(0)
            if not included:
                trace["pricing"]["status"] = "not_used_outside_quarter"
            trace["steps"].append({"step": "delivery", "delivery_on": delivery.isoformat(), "included": included,
                                   "explanation": "预计交付所在季度计入全部成交含税金额，其他季度为零",
                                   "amount_inclusive": text(inclusive)})
    elif rule == "consumption":
        for key in ("expected_consumption_amount", "expected_consumption_year", "expected_consumption_quarter"):
            if row.get(key) is None:
                missing.append(key)
        if not missing:
            included = (
                row["expected_consumption_year"], row["expected_consumption_quarter"]
            ) == (year, quarter)
            inclusive = money(row["expected_consumption_amount"]) if included else Decimal(0)
            trace["pricing"]["status"] = "not_used_consumption_basis"
            trace["steps"].append({"step": "consumption", "included": included,
                                   "consumption_amount": _trace_value(row["expected_consumption_amount"]),
                                   "consumption_year": str(row["expected_consumption_year"]),
                                   "consumption_quarter": str(row["expected_consumption_quarter"]),
                                   "quantum": "0.01", "rounding": "ROUND_HALF_UP",
                                   "explanation": "只采用明确期间的预计消耗含税额，不使用充值额或成交总额替代",
                                   "amount_inclusive": text(inclusive)})
    elif rule == "monthly":
        count = row.get("service_months") or parameters.get("default_service_months")
        if not count:
            missing.append("service_months")
        elif not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 120:
            raise ValueError("service_months must be an integer between 1 and 120")
        if not row.get("expected_start_on"):
            missing.append("expected_start_on")
        if parameters.get("monthly_allocation") != "equal_calendar_months":
            missing.append("rule_parameters.monthly_allocation")
        if parameters.get("oa_required"):
            for key, value in (("expected_oa_on", row.get("expected_oa_on")),
                               ("rule_parameters.oa_cutoff_day", parameters.get("oa_cutoff_day")),
                               ("rule_parameters.oa_deferral_policy", parameters.get("oa_deferral_policy"))):
                if not value:
                    missing.append(key)
        if not missing:
            start = _month(_date(row["expected_start_on"]))
            end = start + count
            allocation = {"service_months": str(count),
                          "service_months_source": "line" if row.get("service_months") else "catalog_default",
                          "requested_start_on": _date(row["expected_start_on"]).isoformat(),
                          "requested_start_month": _month_label(start), "requested_end_month_exclusive": _month_label(end),
                          "oa": {"required": bool(parameters.get("oa_required")), "status": "not_required"},
                          "monthly_allocation": parameters["monthly_allocation"]}
            if parameters.get("oa_required"):
                oa = _date(row["expected_oa_on"])
                month_last_day = monthrange(oa.year, oa.month)[1]
                cutoff = min(parameters["oa_cutoff_day"], month_last_day)
                permitted_start = _month(oa) + (oa.day > cutoff)
                shifted = max(start, permitted_start)
                allocation["oa"] = {"required": True, "status": "applied", "expected_oa_on": oa.isoformat(),
                    "configured_cutoff_day": str(parameters["oa_cutoff_day"]),
                    "month_last_day": str(month_last_day), "effective_cutoff_day": str(cutoff),
                    "cutoff_on": oa.replace(day=cutoff).isoformat(), "clamped_to_month_end": cutoff != parameters["oa_cutoff_day"],
                    "after_cutoff": oa.day > cutoff, "permitted_start_month": _month_label(permitted_start),
                    "deferral_policy": parameters["oa_deferral_policy"], "shifted_months": str(shifted - start)}
                if parameters["oa_deferral_policy"] == "shift_full_term":
                    end += shifted - start
                start = shifted
            # Original-term deferral does not reallocate excluded months to the
            # remaining months; only an explicit shift_full_term shifts all fees.
            total = line_amount(row, trace=trace["pricing"])
            cents = int(total * 100)
            regular_cents, extra_cents = divmod(cents, count)
            original_start = end - count
            allocations, month_details = {}, []
            for month in range(original_start, end):
                extra = int(month >= end - extra_cents)
                value = Decimal(regular_cents + extra) / 100
                allocations[month] = value
                month_details.append({"month": _month_label(month), "base_cents": str(regular_cents),
                    "tail_extra_cents": str(extra), "allocated_inclusive": text(value),
                    "within_quarter": month in months, "excluded_by_oa": month < start,
                    "included": month >= start and month in months,
                    "exclusion_reasons": (["before_effective_start"] if month < start else [])
                        + (["outside_requested_quarter"] if month not in months else [])})
            inclusive = sum((value for month, value in allocations.items() if month >= start and month in months), Decimal(0))
            allocation.update(effective_start_month=_month_label(start), effective_end_month_exclusive=_month_label(end),
                empty_effective_window=start >= end, allocation_start_month=_month_label(original_start),
                allocation_end_month_exclusive=_month_label(end), total_inclusive=text(total), total_cents=str(cents),
                base_cents_per_month=str(regular_cents), tail_extra_cents=str(extra_cents), months=month_details,
                amount_inclusive=text(inclusive),
                explanation=("按完整服务月数以分均摊，余分依次补在最后月份；OA延后整个完整服务期"
                    if parameters.get("oa_required") and parameters.get("oa_deferral_policy") == "shift_full_term"
                    else "按完整服务月数以分均摊，余分依次补在最后月份；保留原结束月份，OA剔除月份不向余下月份重分配"
                    if parameters.get("oa_required") else "按完整服务月数以分均摊，余分依次补在最后月份"))
            trace["monthly_allocation"] = allocation
            trace["steps"].append({"step": "monthly", "detail": "monthly_allocation", "amount_inclusive": text(inclusive)})
    else:
        missing.append("recognition_rule")
    exclusive = None
    if inclusive is not None:
        divisor = 1 + Decimal(str(snapshot["tax_rate"]))
        before_rounding = inclusive / divisor
        exclusive = money(before_rounding)
        trace["tax"] = {"status": "calculated", "formula": "quarter_inclusive / (1 + tax_rate)",
                        "rate": _trace_value(snapshot["tax_rate"]), "divisor": format(divisor, "f"),
                        "amount_inclusive": text(inclusive), "unrounded_exclusive": format(before_rounding, "f"),
                        "quantum": "0.01", "rounding": "ROUND_HALF_UP", "amount_exclusive": text(exclusive),
                        "explanation": "先算本行当季含税额，再除以一加本行冻结税率；逐行四舍五入到分后才汇总"}
        trace["steps"].append({"step": "tax", "detail": "tax", "amount_exclusive": text(exclusive)})
    elif missing:
        trace["steps"].append({"step": "required_inputs", "status": "not_calculated", "missing_fields": list(missing)})
    return _finish_trace(trace, {"line_id": str(row["id"]), "status": "incomplete" if missing else "complete",
            "amount_inclusive": text(inclusive), "amount_exclusive": text(exclusive), "missing_fields": missing})


def material_forecast_preview(rows, year, quarter):
    rows = list(rows)
    lines = [preview_line(row, year, quarter) for row in rows if row.get("is_active", True)]
    missing = [f"{row['line_id']}:{key}" for row in lines for key in row["missing_fields"]]
    if not lines:
        missing.append("material_lines")
    relevant = [r for r in lines if r["status"] != "excluded"]
    complete = not missing and bool(relevant)
    result = {"status": "complete" if complete else "incomplete",
            "recognized_inclusive": text(sum((Decimal(r["amount_inclusive"]) for r in relevant), Decimal(0))) if complete else None,
            "recognized_exclusive": text(sum((Decimal(r["amount_exclusive"]) for r in relevant), Decimal(0))) if complete else None,
            "lines": lines, "missing_fields": missing}
    months = [year * 12 + (quarter - 1) * 3 + i for i in range(3)]
    result["calculation_trace"] = {
        "calculator_version": CALCULATOR_VERSION, "canonical_encoding": TRACE_ENCODING,
        "period": _period(year, quarter, months), "calculation_status": "calculated" if complete else "not_calculated",
        "not_calculated_reasons": (["material_lines"] if not lines else
                                   ["all_active_lines_excluded"] if not relevant else list(missing)),
        "inactive_line_ids": [str(row["id"]) for row in rows if not row.get("is_active", True)],
        "excluded_line_ids": [row["line_id"] for row in lines if row["status"] == "excluded"],
        "sum_line_ids": [row["line_id"] for row in relevant] if complete else [],
        "line_results": [{key: row[key] for key in ("line_id", "status", "amount_inclusive", "amount_exclusive")}
                         for row in lines],
        "aggregation": "sum_of_each_complete_line_inclusive_and_each_line_ROUND_HALF_UP_exclusive",
        "explanation": "仅在存在适用物料且全部条件齐备时汇总；税后金额累加逐行已舍入金额，不对含税总额统一除税",
        "result": {key: result[key] for key in ("status", "recognized_inclusive", "recognized_exclusive")},
    }
    return result
