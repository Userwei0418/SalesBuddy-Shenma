"""Compare complete facts with yesterday's real scheduled capture, never a fabricated close."""
from datetime import datetime, timedelta
from decimal import Decimal, localcontext

from sales_backend.domain.work_report import SHANGHAI
from sales_backend.repositories import review_history as repository

BASIS = "previous_day_scheduled_snapshot"


def attach_baseline(cards, baseline, *, year, quarter, today):
    # Keep current coverage/reasons/target untouched; a known subtotal is not a total.
    from sales_backend.services.review_overview import metric_cards

    metadata = {key: baseline.get(key) for key in (
        "status", "reason_code", "reason", "captured_at", "snapshot_date", "snapshot_id", "definition_version")}
    metadata.update(basis=BASIS, timezone="Asia/Shanghai", value=None)
    valid = baseline.get("status") == "available"
    captured = None
    if valid:
        try:
            captured = datetime.fromisoformat(baseline.get("captured_at", ""))
        except (TypeError, ValueError):
            pass
    if valid and (baseline.get("year") != year or baseline.get("quarter") != quarter
                  or baseline.get("definition_version") != 1
                  or baseline.get("snapshot_date") != (today - timedelta(days=1)).isoformat()
                  or captured is None or captured.utcoffset() is None
                  or captured.astimezone(SHANGHAI).date() != today - timedelta(days=1)
                  or not baseline.get("snapshot_id")):
        valid = False
        metadata.update(status="incomparable", reason_code="baseline_identity_mismatch",
                        reason="昨日快照周期或指标定义不一致")
    prior = {item["metric_code"]: item for item in metric_cards(
        baseline.get("rows", []), {}, False, year=year, quarter=quarter)} if valid else {}
    for card in cards:
        card["day_change"] = None
        card["baseline"] = dict(metadata)
        if not valid:
            continue
        old = prior[card["metric_code"]]
        card["baseline"].update(value=old["value"], coverage=old["coverage"],
                                known_subtotal=old["known_subtotal"], missing_reasons=old["missing_reasons"],
                                source_refs=[{"kind": "review_metric_snapshot", "id": baseline["snapshot_id"]}])
        if old["value"] is None or old["coverage"] != "complete":
            card["baseline"].update(status="incomparable", reason_code="baseline_amount_incomplete",
                                    reason="昨日定时快照金额或资格信息不完整")
        elif card["value"] is None or card["coverage"] != "complete":
            card["baseline"].update(status="incomparable", reason_code="current_amount_incomplete",
                                    reason="当前金额或资格信息不完整，无法计算日变化")
        else:
            with localcontext() as context:
                context.prec = 80
                card["day_change"] = format(Decimal(card["value"]) - Decimal(old["value"]), ".2f")
            card["baseline"].update(reason_code=None, reason="较昨日定时快照，采集时间见标注")
    return cards


async def with_baseline(connection, cards, scopes, *, quarter, selection, now):
    today = now.astimezone(SHANGHAI).date()
    if selection.anchor_date != today:
        baseline = dict(status="no_baseline", reason_code="historical_anchor_current_values",
                        reason="当前指标截至取数时点，所选历史日期不计算日变化")
    else:
        baseline = await repository.metric_baseline(
            connection, scopes, snapshot_date=today - timedelta(days=1),
            year=quarter["year"], quarter=quarter["quarter"])
    return attach_baseline(cards, baseline, year=quarter["year"], quarter=quarter["quarter"], today=today)
