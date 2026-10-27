"""Quarterly adopted facts, never a second source-selection or tax calculator."""

from collections import defaultdict
from decimal import Decimal, localcontext

ACTUAL_METRICS = {
    "actual_recognized_exclusive": ("recognized", "tax_exclusive"),
    "actual_collection": ("collection", "cash_collection_not_applicable"),
}
COVERAGE_NOTE = (
    "按本季度当前采用的已登记实绩汇总；确收不含税，回款独立。"
    "保留财务记录冻结的负责人和团队；未登记不补零，部分登记只显示已确认小计。"
    "登记完整仅指返回的已登记事实，不代表所有客户或商机均已登记；各缺项计数可能重叠，不相加。"
)


def distinct_actuals(rows):
    distinct = {}
    for row in rows:
        key = (row["id"], row["customer_id"], row.get("opportunity_id"), row["year"], row["quarter"], row["kind"])
        if key in distinct and distinct[key] != row:
            raise RuntimeError("Conflicting dashboard actual facts")
        distinct[key] = row
    return list(distinct.values())


def money(value):
    return None if value is None else format(value, ".2f")


def actual_summary(rows, metric_code, *, scope_complete):
    kind, _ = ACTUAL_METRICS[metric_code]
    relevant = [row for row in rows if row["kind"] == kind]
    unresolved = {(r.get("opportunity_id") or r["id"], r["year"], r["quarter"])
                  for r in relevant if r.get("attribution_status") == "unresolved"}
    excluded = any(r.get("_attribution_excluded") for r in relevant)
    selected = [row for row in relevant if not row.get("_attribution_excluded")]
    periods = defaultdict(list)
    values, reasons = [], set()
    for row in selected:
        # Customer-only entries have no shared opportunity-period source to
        # reconcile; keep each original adopted entry as its own object.
        key = (row.get("opportunity_id") or row["id"], row["year"], row["quarter"])
        periods[key].append(row)
        if row["amount"] is not None:
            value = Decimal(str(row["amount"]))
            if not value.is_finite():
                raise RuntimeError("Invalid dashboard actual amount")
            values.append(value)
        if row["tax_unresolved"]:
            reasons.add("actual_tax_basis_unresolved")
        if row["status"] in {"needs_confirmation", "stale_confirmation"}:
            reasons.add("actual_source_conflict")
        if row["status"] == "withdrawn":
            reasons.add("actual_withdrawn")
        if row["status"] == "missing":
            reasons.add("actual_not_registered")
    incomplete = sum(not all(r["complete"] and r["amount"] is not None for r in group)
                     for group in periods.values())
    if incomplete:
        reasons.add("actual_incomplete")
    if not relevant:
        reasons.add("actual_not_registered")
    if unresolved:
        reasons.add("actual_attribution_unresolved")
    if not scope_complete:
        reasons.add("source_scope_not_fully_authorized")
    complete = bool(selected) and not incomplete and scope_complete and not excluded
    with localcontext() as ctx:
        ctx.prec = 80
        subtotal = sum(values, Decimal(0)) if values else None
    return dict(
        value=money(subtotal) if complete else None, known_subtotal=money(subtotal),
        coverage="complete" if complete else "partial" if values else "unavailable",
        missing_amount_count=incomplete, unknown_eligibility_count=0,
        missing_reasons=sorted(reasons), coverage_note=COVERAGE_NOTE,
        attribution_unknown_period_count=len(unresolved),
        registered_fact_count=sum(r["raw_amount"] is not None for r in selected),
        registered_period_count=len(periods), incomplete_period_count=incomplete,
        missing_material_count=sum(max(r["missing_material_count"] for r in group) for group in periods.values()),
        tax_unknown_period_count=sum(any(r["tax_unresolved"] for r in group) for group in periods.values()),
        conflict_period_count=sum(any(r["status"] in {"needs_confirmation", "stale_confirmation"} for r in group)
                                  for group in periods.values()),
        source_refs=[dict(actual_id=r["id"], customer_id=r["customer_id"], opportunity_id=r.get("opportunity_id"),
                          material_line_id=r.get("material_line_id"), year=r["year"], quarter=r["quarter"],
                          kind=kind, version_no=r["version_no"], definition_version=1) for r in selected],
    )


def actual_card(rows, code, target, *, scope_complete, time_progress, baseline):
    kind, tax_basis = ACTUAL_METRICS[code]
    summary = actual_summary(rows, code, scope_complete=scope_complete)
    # These are existing net-recognition / collection goals. Inclusive forecast
    # goals are never converted or reused for adopted actuals.
    denominator_text = target["value"]
    target_partial = (target["status"] != "permission_denied" and denominator_text is None
                      and target["coverage"] == "partial" and target["known_subtotal"] is not None)
    if target_partial:
        denominator_text = target["known_subtotal"]
    numerator_text = summary["value"] if summary["value"] is not None else summary["known_subtotal"]
    value_partial = summary["value"] is None and summary["known_subtotal"] is not None
    rate_basis = ("known_subtotals_and_targets" if target_partial and value_partial else
                  "known_effective_targets" if target_partial else "known_subtotals" if value_partial else
                  "effective_target")
    with localcontext() as ctx:
        ctx.prec = 80
        denominator = Decimal(denominator_text) if denominator_text is not None else None
        numerator = Decimal(numerator_text) if numerator_text is not None else None
        comparable = numerator is not None and denominator is not None and denominator > 0
        ratio = numerator / denominator if comparable else None
        return dict(
            **summary, metric_code=code, definition_version=1, unit="元", currency="CNY",
            basis="adopted_actuals", tax_basis=tax_basis, target=target, target_basis=kind,
            gap=money(denominator - numerator) if comparable else None,
            rate=format(ratio, ".8f") if comparable else None, rate_basis=rate_basis if comparable else None,
            status="incomplete" if summary["coverage"] != "complete" else
                   "target_unavailable" if target["status"] == "permission_denied" else
                   "no_target" if not comparable else "expected_achieved" if ratio >= 1 else
                   "on_track" if ratio >= Decimal(time_progress) else "behind",
            day_change=None, baseline=dict(baseline),
        )
