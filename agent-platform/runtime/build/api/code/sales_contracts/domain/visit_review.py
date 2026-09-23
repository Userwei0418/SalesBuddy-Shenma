from sales_contracts.contracts.visit_schema import FIRST_VISIT_KEYS, TEXT_FIELDS

from sales_contracts.domain.visit_contract import COMPATIBLE_PROMPT_VERSIONS, CONTENT_KEYS, is_first_visit

def review_text(fields):
    lines = [
        "拜访审核 v2",
        f"拜访结果：{str(fields.get('follow_up_record', '')).strip()}",
        f"下一步行动计划：{str(fields.get('next_action', '')).strip()}",
    ]
    if is_first_visit(fields):
        lines += ["拜访类型：首次拜访"] + [
            f"{TEXT_FIELDS[k]}：{str(fields.get(k, '')).strip()}" for k in FIRST_VISIT_KEYS
        ]
    return "\n".join(lines)
