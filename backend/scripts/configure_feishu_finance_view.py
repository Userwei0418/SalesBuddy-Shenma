"""Build a secret-free, reviewable V4 financial schema plan.

This command does not mutate Feishu. Apply the dependency-ordered field groups
with lark-cli after reviewing the plan and its live table metadata. Formula
columns are deliberately absent from the backend writable mapping.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse, urlencode


SYNC_FIELDS = {
    "actual_recognized_total": "累计确收（元）",
    "actual_collection_total": "累计回款（元）",
    "actual_recognized_confirmed_subtotal": "可计入确收小计（元）",
    "actual_collection_confirmed_subtotal": "可计入回款小计（元）",
    "finance_conflict_period_count": "待核对实绩期数",
    "finance_quality": "实绩数据说明",
}


def formula(name, expression, description):
    return {"name": name, "type": "formula", "expression": expression,
            "description": description}


def build_plan(config):
    base_url = config["base_url"].rstrip("/")
    admin_url = config["admin_url"]
    for url in (base_url, admin_url):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Use exact HTTPS base/admin URLs without query or fragment")
    tables = config["tables"]
    if set(tables) != {"opportunity", "forecast", "actual", "history"}:
        raise ValueError("Exactly four table coordinates are required")
    if len({table["id"] for table in tables.values()}) != 4:
        raise ValueError("Table IDs must be distinct")
    for table in tables.values():
        if not table["id"].startswith("tbl") or any(c in table["name"] for c in '[]"'):
            raise ValueError("Invalid table coordinate")
    groups = []
    for kind in ("forecast", "actual", "history"):
        table = tables[kind]
        record_url = base_url + "?" + urlencode({"table": table["id"]}) + "&record="
        label = {'forecast': '[年份] & " Q" & [季度] & " 预测  "',
                 'actual': '"实绩 " & [类型] & "  "',
                 'history': '[年份] & " Q" & [季度] & " " & IF([实绩类型]="recognized","确收","回款") & " 原值  "'}[kind]
        groups.append({"table_id": table["id"], "fields": [
            formula("关联商机系统ID", 'IF(COUNTA([关联商机].[系统记录ID])=1,FIRST([关联商机].[系统记录ID]),"")',
                    "只匹配唯一关联商机的系统ID，不按同名客户或商机匹配。"),
            formula("财务明细入口", f'HYPERLINK("{record_url}" & RECORD_ID(),{label})',
                    "查看本条来源记录；登记和更正以销售系统为准。"),
        ]})
    fields = []
    for source, name in SYNC_FIELDS.items():
        field = {"name": name, "type": "text" if source == "finance_quality" else "number"}
        if field["type"] == "number":
            field["style"] = {"type": "plain", "precision": 0 if source.endswith("count") else 2,
                              "thousands_separator": True}
        field["description"] = ("由系统按每个商机、年度、季度和实绩类型选择有效来源。未知或来源冲突留空；0表示明确登记的零值。"
                                if source.endswith("total") else
                                "由系统同步；小计只含当前可计入的期间，不可代替存在待核对期间的累计值。")
        fields.append(field)
    fields.append(formula("预测年度", "YEAR(TODAY())", "本年预测对应的年份，随自然年自动更新。"))
    forecast = tables["forecast"]["name"]
    for source, name in (("预测确收（含税元）", "本年预计确收（含税元）"),
                         ("预测回款（含税元）", "本年预计回款（含税元）")):
        values = (f'[{forecast}].FILTER(CurrentValue.[关联商机系统ID]=[系统记录ID] && '
                  f'CurrentValue.[记录状态]="有效" && CurrentValue.[年份]=YEAR(TODAY()) && '
                  f'NOT(ISBLANK(CurrentValue.[{source}]))).[{source}]')
        fields.append(formula(name,
            f'IF([记录状态]="有效",IF(COUNTA({values})=0,"",{values}.SUM()),"")',
            "仅本年度有效季度预测的合计；预测不是实际确收或回款。空值不冒充0，原始明确0保留。"))
    for kind, name in (("forecast", "季度预测明细"), ("actual", "逐笔实绩明细"), ("history", "历史实绩明细")):
        # Keep voided/history rows reachable for audit; only forecasts above filter valid rows.
        table = tables[kind]["name"]
        fields.append(formula(name,
            f'[{table}].FILTER(CurrentValue.[关联商机系统ID]=[系统记录ID]).[财务明细入口].LISTCOMBINE()',
            "按唯一系统商机ID反查明细，保留作废记录供追溯；作废记录不计入汇总。"))
    review_prefix = admin_url + "?" + urlencode({"company": config["company_code"]}) + "&finance="
    fields.append(formula("财务核对入口",
        f'HYPERLINK("{review_prefix}" & ENCODEURL([系统记录ID]) & "#opportunities","查看 / 核对财务")',
        "进入系统查看同口径明细；管理员或运营且有实绩登记权限时可选择冲突来源，操作留审计。"))
    groups.append({"table_id": tables["opportunity"]["id"], "fields": fields})
    visible = ["商机名称", "累计确收（元）", "累计回款（元）", "本年预计确收（含税元）", "本年预计回款（含税元）",
               "预测年度", "实绩数据说明", "待核对实绩期数", "财务核对入口", "季度预测明细", "逐笔实绩明细", "历史实绩明细",
               "可计入确收小计（元）", "可计入回款小计（元）", "客户名称", "负责人", "商机阶段", "记录状态", "系统记录ID", "最近同步时间"]
    return {"format": "salesbuddy-v4-finance-plan-v1", "base_url": base_url,
            "field_groups": groups, "writable_mapping_by_name": SYNC_FIELDS,
            "view": {"table_id": tables["opportunity"]["id"], "name": "财务概览", "type": "grid",
                     "visible_fields": visible,
                     "filter": {"logic": "and", "conditions": [["记录状态", "intersects", ["有效"]]]}},
            "history_folder": {"name": "历史资料", "table_id": tables["history"]["id"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = build_plan(json.loads(args.config.read_text()))
    with args.output.open("x") as handle:
        json.dump(plan, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    main()
