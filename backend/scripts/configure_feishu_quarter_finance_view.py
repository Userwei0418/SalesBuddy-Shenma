"""Secret-free, fixed-year V4 quarterly column plan. Never applies remote changes.

Forecasts are formulas over uniquely linked valid source rows. Actuals are
writable projections from the shared evidence resolver; no duplicate ledger.
Run again with another year to add history-preserving annual column groups.
"""
import argparse
import json
from pathlib import Path


def build_plan(year):
    if not 2000 <= year <= 2100:
        raise ValueError("Year must be between 2000 and 2100")
    fields, mapping, visible = [], {}, ["商机名称", "商机状态", "产品线", "商机金额（元）", "预计关单日期"]
    table = "商机季度预测（原始录入）"
    for quarter in range(1, 5):
        prefix = f"{year} Q{quarter} "
        selector = (f'[{table}].FILTER(CurrentValue.[关联商机系统ID]=[系统记录ID] && '
                    f'CurrentValue.[记录状态]="有效" && CurrentValue.[年份]={year} && '
                    f'CurrentValue.[季度]={quarter})')
        for source, title in (("预测确收（含税元）", "预计确收金额（万元，含税）"),
                              ("预测回款（含税元）", "预计回款金额（万元）"),
                              ("回款信心度", "回款信心度")):
            value = f'{selector}.[{source}]'
            content = (f'SWITCH(FIRST({value}),"high","高（≥70%）","low","低（<70%）","")'
                       if source == "回款信心度" else f'FIRST({value})/10000')
            expression = f'IF([记录状态]="有效" && COUNTA({selector}.[系统记录ID])=1 && COUNTA({value})=1,{content},"")'
            name = prefix + title
            field = {"name": name, "type": "formula", "expression": expression,
                     "description": f"固定{year}年Q{quarter}；唯一系统商机ID匹配有效季度预测，按原值展示，不乘阶段或信心度。重复来源、未填留空，明确0保留。"}
            fields.append(field); visible.append(name)
        for metric, title in (("recognized_exclusive", "实际确收金额（万元，不含税）"),
                              ("collection", "实际回款金额（万元）"), ("quality", "实绩口径说明")):
            name, key = prefix + title, f"quarter_{year}_q{quarter}_{metric}"
            field = {"name": name, "type": "text" if metric == "quality" else "number",
                     "description": "系统按发生日/原季度从已核对来源同步；冲突或未登记留空，明确0保留。实际确收仅展示已明确不含税金额，未知/含税/混合不换算。禁止手填总额。"}
            if metric != "quality":
                field["style"] = {"type": "plain", "precision": 4, "thousands_separator": True}
                visible.append(name)
            fields.append(field); mapping[key] = name
    visible += [f"{year} Q{q} 实绩口径说明" for q in range(1, 5)]
    visible += ["财务核对入口", "季度预测明细", "逐笔实绩明细", "历史实绩明细", "客户名称", "负责人", "商机阶段",
                "累计确收（元）", "累计回款（元）", "实绩数据说明", "待核对实绩期数", "记录状态", "系统记录ID", "最近同步时间"]
    return {"format": "salesbuddy-v4-quarter-finance-v1", "year": year, "fields": fields,
            "writable_mapping_by_name": mapping, "visible_fields": visible}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x") as handle:
        json.dump(build_plan(args.year), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
