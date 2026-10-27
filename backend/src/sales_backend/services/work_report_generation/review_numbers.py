"""Protect multi-subject Review numbers without changing frozen/public models.

Namespace ownership is computed exactly as in the snapshot producer. Never
infer a selection total by adding subject values: their populations may overlap.
"""

import json
import re

from sales_backend.domain.work_report import canonical_hash

METRIC_LABELS = {
    "forecast_collection": "回款预测", "collection_ppl": "回款PPL",
    "forecast_recognized": "确收预测", "recognized": "实际确收",
}
ROLE_LABELS = {
    "current": "当前值", "forecast": "预测值", "effective_target": "生效目标",
    "gap": "差额", "rate": "达成率", "day_change": "日变化",
    "week_change": "周变化", "kr_progress": "KR进度",
}
# Dates are permitted as dates, never as count/value fragments. Quantitative
# prose must use the protected metric sentences; there is no free count channel.
DATE = re.compile(r"\d{4}-\d{2}-\d{2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日")
NUMBER = re.compile(r"\d|[零〇一二两三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億]")
GRAMMAR = ("下一步", "进一步", "一致", "统一", "一并", "逐一", "一旦", "一般", "一方面", "另一方面")


def measure_subjects(facts):
    if facts.contract_version != "work_report.review_facts.v2":
        return {}
    prefixes = {
        "scope_" + canonical_hash({"scope_type": row.scope_type, "record_id": str(row.id)})[:16] + "_": row
        for row in facts.scope.subjects
    }
    return {
        measure.measure_id: subject
        for measure in facts.measures
        for prefix, subject in prefixes.items()
        if measure.measure_id.startswith(prefix)
    }


def protected_metric_sentences(facts):
    subjects = measure_subjects(facts)
    return {
        row.measure_id: (
            f"{subjects[row.measure_id].label}"
            f"（仅此{'团队' if subjects[row.measure_id].scope_type == 'team' else '成员'}）："
            f"{METRIC_LABELS.get(row.metric_code, row.metric_code)}{ROLE_LABELS[row.role]}为 "
            "{{metric:" + row.measure_id + "}} " + row.unit + "；该统计覆盖完整。"
        )
        for row in facts.measures
        if row.measure_id in subjects and row.coverage == "complete" and row.value is not None
    }


def _without_trusted_labels(text, row, facts):
    """Names/period labels are not counts; allow only frozen complete literals."""
    labels = {
        f"{'团队' if subject.scope_type == 'team' else '成员'}名称“{subject.label}”"
        for subject in getattr(facts.scope, "subjects", ()) if subject.label
    }
    for date in (facts.period.start, facts.period.cutoff):
        quarter = (date.month - 1) // 3 + 1
        # The input permits frozen calendar labels. Q4 and 第四季度 denote
        # the same trusted period, not a count of opportunities or references.
        labels.update((f"{date.year}年度", f"{date.year}年", f"Q{quarter}",
                       f"第{quarter}季度", f"第{'一二三四'[quarter - 1]}季度"))
    for source in facts.evidence:
        if source.source_ref not in row.source_refs:
            continue
        try:
            data = json.loads(source.text)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            # Never whitelist arbitrary values, numeric fields, or whole source
            # prose. Only these explicit names from a cited frozen record.
            for key, label in (("name", "来源"), ("customer_name", "客户"),
                               ("opportunity_name", "商机"), ("product_name", "产品")):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    labels.add(f"{label}名称“{value}”")
    for label in sorted(labels, key=len, reverse=True):
        text = text.replace(label, "")
    text = DATE.sub("", text)
    for phrase in GRAMMAR:
        text = text.replace(phrase, "")
    return text


def validate_scoped_numbers(row, facts):
    """Exact sentence equality, not a label substring or a prose truth judge."""
    if facts.contract_version != "work_report.review_facts.v2":
        return
    if row.measure_refs:
        sentences = protected_metric_sentences(facts)
        if any(ref not in sentences for ref in row.measure_refs):
            raise ValueError("Review metric has no verified subject")
        if row.text != "\n".join(sentences[ref] for ref in row.measure_refs):
            raise ValueError("Review numeric statement must preserve its exact subject sentence")
        measures = {item.measure_id: item for item in facts.measures}
        allowed = {ref for key in row.measure_refs for ref in measures[key].source_refs}
        if not set(row.source_refs) <= allowed:
            raise ValueError("Review numeric statement cannot cite another population")
    else:
        # Do not parse model numbers into new trusted measures or silently
        # remove/rewrite a bad sentence. Reject it through the existing router.
        text = _without_trusted_labels(row.text, row, facts)
        if NUMBER.search(text):
            raise ValueError("Review unbound numeric or count claim")


def numeric_projection(facts):
    subjects = measure_subjects(facts)
    return {
        "measure_subjects": {
            key: {"scope_type": row.scope_type, "id": str(row.id), "label": row.label}
            for key, row in subjects.items()
        },
        "numeric_sentences": protected_metric_sentences(facts),
        "selection_total": "not_provided; subject statistics are not a deduplicated selection aggregate",
        "count_policy": "no_free_counts; do not count source_refs, batch rows or metric_query as opportunities",
    }
