"""Pure opportunity stages, confirmation requirements, forecasts and change descriptions."""

from decimal import Decimal
from typing import Any

# 商机阶段不单独填写，由概率推出，前端只给这五档。
STAGE_BY_PROBABILITY = {
    10: "identified",
    30: "qualified",
    50: "solution",
    70: "proposal",
    90: "negotiation",
}


def stage_for_probability(probability: Any) -> str:
    stage_code = STAGE_BY_PROBABILITY.get(int(probability))
    if not stage_code:
        raise ValueError("商机概率只能是10%、30%、50%、70%或90%")
    return stage_code


STAGES = {
    10: "意向沟通－10%",
    30: "商机确认－30%",
    50: "方案沟通－50%",
    70: "商务谈判－70%",
    90: "客户签约－90%",
    100: "赢单 Won－100%",
}


def stage_label(row):
    return "丢单 Lost" if row.get("status") == "lost" else STAGES.get(row.get("probability"), "暂无商机")


def change_tone(before, after):
    if not before:
        return "blue", "商机创建成功"
    if after["status"] == "lost" or (
        before.get("probability") is not None
        and after.get("probability") is not None
        and after["probability"] < before["probability"]
    ):
        return "red", "商机推进回退" if after["status"] != "lost" else "商机已丢单"
    if (before.get("amount") is not None and after["amount"] < before["amount"]) or (
        before.get("expected_close_date") is not None and after["expected_close_date"] > before["expected_close_date"]
    ):
        return "orange", "商机需关注"
    if after["status"] == "won" or (after.get("probability") or 0) > (before.get("probability") or 0):
        return "green", "商机有进展"
    return "blue", "商机信息已更新"


EDIT_KEYS = (
    "name",
    "probability",
    "status",
    "amount",
    "expected_close_date",
    "partner_name",
    "sales_channel",
    "partner_id",
    "product_line",
    "follow_up_plan",
)


def validate_forecast_completeness(opportunity, rows):
    """A zero plan is explicit; an absent/null plan is not a zero forecast."""
    if opportunity["status"] == "lost" or (opportunity.get("probability") or 0) < 30:
        return
    entered = [r for r in rows if r.get("recognized_amount") is not None or r.get("collection_amount") is not None]
    if not entered:
        raise ValueError("30%及以上阶段须至少填写一个季度的回款和确收，金额为零请填 0")
    for row in entered:
        if row.get("recognized_amount") is None or row.get("collection_amount") is None:
            raise ValueError(f"{row['year']} Q{row['quarter']}：请补全季度回款和确收，金额为零请填 0")


def normalize_forecasts(existing, updates):
    merged = {(r["year"], r["quarter"]): dict(r) for r in existing}
    seen = set()
    for row in updates or []:
        key = row["year"], row["quarter"]
        if key in seen:
            raise ValueError("同一季度只能填写一条预测")
        seen.add(key)
        merged[key] = dict(row)
    return [merged[key] for key in sorted(merged)]


def differences(before, after, old_forecasts, new_forecasts):
    rows = []
    labels = {
        "name": "商机名称",
        "amount": "ACV（万元）",
        "expected_close_date": "预计关单日期",
        "partner_name": "所属伙伴",
        "product_line": "产品线",
        "follow_up_plan": "跟进计划",
    }
    if before and (
        str(before.get("partner_id")) != str(after.get("partner_id"))
        or before.get("sales_channel") != after.get("sales_channel")
    ):
        rows.append({"label": "销售渠道", "before": before.get("sales_channel"), "after": after.get("sales_channel")})
    if not before or stage_label(before) != stage_label(after):
        rows.append(
            {"label": "商机阶段", "before": stage_label(before) if before else "新建", "after": stage_label(after)}
        )
    for key, label in labels.items():
        old, new = (before or {}).get(key), after.get(key)
        if old == new or (not before and not new):
            continue
        if key == "amount":
            old = str(old / Decimal(10000)) if old is not None else "未填写"
            new = str(new / Decimal(10000))
        rows.append(
            {
                "label": label,
                "before": old if old not in (None, "") else "未填写",
                "after": new if new not in (None, "") else "未填写",
            }
        )
    old_map = {(r["year"], r["quarter"]): r for r in old_forecasts}
    for row in new_forecasts:
        prior = old_map.get((row["year"], row["quarter"]), {})
        for key, label in [("recognized_amount", "预测含税确收"), ("collection_amount", "预测回款")]:
            if row.get(key) != prior.get(key):
                rows.append(
                    {
                        "label": f"{row['year']} Q{row['quarter']} {label}（万元）",
                        "before": str(prior[key] / Decimal(10000)) if prior.get(key) is not None else "未填写",
                        "after": str(row[key] / Decimal(10000)) if row.get(key) is not None else "未填写",
                    }
                )
    return rows


def prepare_change(before, data):
    after = {k: data.get(k) if data.get(k) is not None else (before or {}).get(k) for k in EDIT_KEYS}
    after["name"] = str(data["name"]).strip()
    after["amount"] = Decimal(str(data["amount"]))
    after["status"] = data.get("status", "open")
    if after["status"] == "won":
        after["probability"] = 100
    elif after["status"] == "open" and after["probability"] not in (10, 30, 50, 70, 90):
        raise ValueError("请选择商机阶段")
    elif after["status"] == "lost":
        after["probability"] = before.get("probability") if before else data.get("probability")
    if after["status"] in ("won", "lost") and (before or {}).get("status") != after["status"]:
        if not data.get("closure_confirmed"):
            raise ValueError("请明确确认赢单或丢单")
    if before and before["status"] != "open" and after["status"] == "open" and not data.get("reopen_confirmed"):
        raise ValueError("商机已关闭，请明确确认重新打开")
    return after
