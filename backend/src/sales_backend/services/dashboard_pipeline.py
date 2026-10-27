"""Server-side stage amounts; no page sample or client-side money reconstruction."""

from decimal import Decimal, localcontext

from sales_backend.domain.opportunities import STAGES as STAGE_LABELS
from sales_backend.services import dashboard_metrics
from sales_backend.services.review_overview import METRICS, amount, contribution

STAGES = tuple((value, STAGE_LABELS[value]) for value in (30, 50, 70, 90, 100))


def stage_amount(rows, code, *, scope_complete):
    eligible = excluded = unknown = missing = 0
    known, reasons = [], set()
    for row in rows:
        included, value, reason = contribution(row, code)
        eligible += included is True
        excluded += included is False
        unknown += included is None
        missing += included is True and value is None
        if value is not None:
            known.append(value)
        if reason:
            reasons.add(reason)
    if not scope_complete:
        reasons.add("source_scope_not_fully_authorized")
    complete = scope_complete and not (missing or unknown)
    with localcontext() as ctx:
        ctx.prec = 80
        subtotal = sum(known, Decimal(0)) if known or complete else None
    return dict(eligible_count=eligible, excluded_count=excluded, unknown_eligibility_count=unknown,
                value=amount(subtotal) if complete else None, known_subtotal=amount(subtotal),
                known_amount_count=len(known), missing_amount_count=missing,
                coverage="complete" if complete else "partial" if known else "unavailable",
                missing_reasons=sorted(reasons), unit="元", currency="CNY", tax_basis=METRICS[code][2])


def stage_summary(rows, *, scope_complete):
    buckets = {str(probability): [] for probability, _ in STAGES}
    buckets["unknown"] = []
    unknown = excluded = 0
    for row in rows:
        included = contribution(row, "collection_ppl")[0]
        if included is None:
            unknown += 1
        elif not included:
            excluded += 1
        else:
            probability = Decimal(str(row["probability"]))
            key = str(int(probability)) if probability == int(probability) else "unknown"
            buckets[key if key in buckets else "unknown"].append(row)
    stages = []
    for probability, label in (*STAGES, (None, "阶段待核对")):
        key = str(probability) if probability is not None else "unknown"
        if key == "unknown" and not buckets[key]:
            continue
        stages.append(dict(code=key, label=label, probability=probability, count=len(buckets[key]),
                           metrics={code: stage_amount(buckets[key], code, scope_complete=scope_complete)
                                    for code in METRICS}))
    complete = scope_complete and not unknown and not buckets["unknown"]
    reasons = ([] if scope_complete else ["source_scope_not_fully_authorized"])
    if unknown:
        reasons.append("unknown_eligibility")
    if buckets["unknown"]:
        reasons.append("stage_unrecognized")
    count = sum(len(bucket) for bucket in buckets.values())
    return dict(coverage="complete" if complete else "partial" if count else "unavailable",
                missing_reasons=reasons, eligible_count=count, excluded_count=excluded,
                unknown_eligibility_count=unknown, stages=stages)


async def pipeline(connection, actor, **selection):
    _, envelope, rows, complete = await dashboard_metrics.material(connection, actor, **selection)
    return dict(**envelope, basis="latest_sales_forecast", **stage_summary(rows, scope_complete=complete))
