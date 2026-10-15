"""Retain explicit frozen-source qualifiers after the last model call.

Literal excerpts are not semantic classification or a complete limitation list.
No source IDs, company names or synthetic-data keywords determine eligibility.
"""

import re

from pydantic import ValidationError

from sales_backend.contracts.work_report import ReportContent, ReportItem, ReportSourceNote
from sales_backend.domain.model_contract import ModelContractError
from sales_backend.domain.work_report import WorkReportError, validate_content
from sales_backend.services.agent_platform.routing import InvalidAgentResult

from .content import _literal, overview_references, overview_section, validate_section_references

_LEADING_LABEL = re.compile(r"\A\s*(【[^【】\r\n]+】(?:[ \t]*【[^【】\r\n]+】)*)")
_EXPLICIT_QUALIFIERS = ("尚需核实", "尚未确认", "待验证")
_EXCERPT_LIMIT = 1200
_PROGRESS = {
    "recorded_in_week": "本周有已登记的进展材料；目标是否完成以材料中的具体结果为准。",
    "no_recorded_progress_in_week": "本周未记录新进展。",
    "unavailable": "本周进展材料不可得，不能据此认定没有进展。",
}
_NEXT_ACTION = {
    "recorded": "已登记下一步，不代表已执行或已完成。",
    "not_recorded": "未登记下一步；不能据此推断没有后续工作。",
    "unavailable": "下一步材料不可得，不能据此认定未登记。",
}
# Exact prefixes emitted by domain.work_report_weekly, not natural-language date
# inference. Any matching source text is quoted as text, never a certified date.
_ACTION_PREFIX = re.compile(r"(?m)^(?:当前登记下一步|已登记下一步)（日期未结构化核定）：")


def _action_note(source):
    marker = _ACTION_PREFIX.search(source.text)
    if marker:
        excerpt = source.text[marker.start():]
        if len(excerpt) <= _EXCERPT_LIMIT:
            return "下一步来源片段（原文；不核定自由文本日期）：\n" + _literal(excerpt)
        return "下一步日期未结构化核定；来源片段过长，未完整并入正文，须结合来源核对。"
    if source.state_kind == "planned" and source.time_basis == "due_at" and source.business_at is not None:
        return "正式计划登记截止时间：" + source.business_at.isoformat() + "；该字段不证明任务已完成。"
    return None


def _opportunity_notes(fact, source, cited, sources):
    probability = f"{fact.probability}%" if fact.probability is not None else "未知（未核定，不按0处理）"
    contributors = "、".join(_literal(person.name) for person in source.contributors)
    notes = [
        "快照当前事实（不代表本期发生推进，也不替代原正文的语义核对）：",
        "当前阶段：" + (_literal(fact.stage_code) or "未登记") + "；当前状态："
        + (_literal(fact.status) or "未登记") + "；当前概率：" + probability + "。",
        "贡献者：" + (contributors or "快照未提供，不能据此认定无人参与") + "。",
        "快照进展状态：" + _PROGRESS[fact.progress_state],
        "下一步登记状态：" + _NEXT_ACTION[fact.next_action_state],
    ]
    refs = [fact.source_ref]
    if fact.next_action_state == "recorded":
        included = list(dict.fromkeys(ref for ref in fact.next_action_refs if ref in cited))
        for ref in included:
            action_source = sources[ref]
            action = _action_note(action_source)
            notes.append(_literal(action_source.title) + "：\n" + (action or
                         "本来源未提供可单独核对的下一步日期字段或片段；日期未核定，须结合来源核对。"))
            if action_source.truncated:
                notes.append("该下一步来源在本快照已截断，不能据此认定材料完整。")
            if ref not in refs:
                refs.append(ref)
        if not included or set(fact.next_action_refs) - set(included):
            notes.append("部分已登记下一步材料未在本章引用；不从这些材料补写正文或核定日期。")
    else:
        notes.append("未提供可核定的下一步日期；当前状态、跟进时间和正文中的日期不能替代计划日期。")
    return notes, refs


def _source_notes(source):
    text = source.text
    notes = []
    label = _LEADING_LABEL.match(text)
    if label:
        if len(label.group(1)) <= _EXCERPT_LIMIT:
            notes.append("来源开头标注（原文）：" + _literal(label.group(1)))
        else:
            notes.append("来源开头标注过长，未并入正文；需结合来源核对其限定。")

    # Never split a sentence at punctuation: negation and contrast remain in the
    # same paragraph. Include adjacent paragraphs as context, without interpreting
    # whether any mentioned condition is true, resolved or hypothetical.
    paragraphs = list(re.finditer(r"[^\r\n]+", text))
    selected = set()
    for index, paragraph in enumerate(paragraphs):
        if any(term in paragraph.group() for term in _EXPLICIT_QUALIFIERS):
            selected.update(range(max(0, index - 1), min(len(paragraphs), index + 2)))
    groups = []
    for index in sorted(selected):
        if groups and index == groups[-1][-1] + 1:
            groups[-1].append(index)
        else:
            groups.append([index])
    used = 0
    omitted = False
    for group in groups:
        excerpt = text[paragraphs[group[0]].start():paragraphs[group[-1]].end()]
        if used + len(excerpt) > _EXCERPT_LIMIT:
            omitted = True
            continue  # Do not cut off a negation, sentence or the following context.
        notes.append("来源原文片段（保留上下文，不代表完整限制清单）：\n" + _literal(excerpt))
        used += len(excerpt)
    if omitted:
        notes.append("来源中包含显式限定表述，但上下文过长，未完整并入正文；须结合来源核对，不能以摘要替代原文限定。")
    if notes and source.truncated:
        notes.append("本快照来源已截断；上述标注与片段不代表完整原文限定。")
    return notes


def _opportunity_notice(fact, source, cited, sources):
    """Always-visible qualifications, not another rendition of the summary."""
    contributors = "、".join(_literal(person.name) for person in source.contributors)
    lines = [
        "贡献者：" + (contributors or "快照未提供，不能据此认定无人参与") + "。",
        "来源说明仅用于核对冻结材料，不核定摘要或人工正文。当前快照不代表本期发生推进。",
    ]
    lines.append({
        "recorded_in_week": "登记进展不代表目标已经完成。",
        "no_recorded_progress_in_week": "未记录新进展不等于没有发生进展。",
        "unavailable": "进展材料不可得，不能据此认定没有进展。",
    }[fact.progress_state])
    if fact.next_action_state == "recorded":
        lines.append("下一步登记不代表已执行或已完成。")
        included = [sources[ref] for ref in dict.fromkeys(fact.next_action_refs) if ref in cited]
        if any(_ACTION_PREFIX.search(item.text) for item in included):
            lines.append("下一步日期未结构化核定；不能从自由文本推断具体计划日期或下周归属。")
        if any(item.state_kind == "planned" and item.time_basis == "due_at" and item.business_at is not None
               and not _ACTION_PREFIX.search(item.text) for item in included):
            lines.append("结构化截止时间属于计划登记，不代表任务已完成。")
        if not included or any(_action_note(item) is None for item in included):
            lines.append("下一步日期缺少可核定字段或片段，须结合来源核对。")
        if set(fact.next_action_refs) - cited:
            lines.append("部分下一步材料未在本章引用，不从这些材料补写正文或核定日期。")
        if any(item.truncated for item in included):
            lines.append("下一步来源在本快照已截断，不能据此认定材料完整。")
    else:
        lines.append("未登记下一步不能推断没有后续工作。" if fact.next_action_state == "not_recorded"
                     else "下一步材料不可得，不能据此认定未登记。")
        lines.append("未提供可核定的下一步日期；当前状态、跟进时间和正文日期不能替代计划日期。")
    lines.extend(_source_notes(source))
    if source.truncated and not any("已截断" in line for line in lines):
        lines.append("本快照来源已截断，不能据此认定材料完整。")
    return "\n".join(lines)


def _retain_opportunity_note(section, source, fact, cited, sources):
    projection, refs = _opportunity_notes(fact, source, cited, sources)
    note = ReportSourceNote(title=_literal(source.title), source_refs=refs,
                            notice_text=_opportunity_notice(fact, source, cited, sources),
                            text="\n".join([*projection, *_source_notes(source)]))
    # Only a unique single-source summary gets an adjacent note. Ambiguous or
    # multi-source prose stays intact, with separately labelled source evidence.
    anchors = [item for item in section.items if item.kind == "summary" and item.source_refs == [fact.source_ref]]
    if len(anchors) == 1:
        anchors[0].source_notes = [note]
    else:
        section.items.append(ReportItem(kind="summary", text=_literal(source.title) + "：来源说明。",
                                        source_refs=[fact.source_ref], source_notes=[note]))


_COUNT_LABELS = {
    "new_claimed_customers": "新领取客户", "formal_visits": "正式拜访",
    "new_opportunities": "新增商机", "lost_opportunities": "丢单商机",
}
_AMOUNT_LABELS = {"acv": "ACV", "quarter_revenue": "季度预测收入", "quarter_collection": "季度预测回款"}
_COVERAGE_LABELS = {"complete": "完整", "partial": "部分", "unavailable": "不可得"}


def _count_lines(counts):
    for field, label in _COUNT_LABELS.items():
        count = getattr(counts, field)
        value = str(count.value) if count.value is not None else "未知（不按0处理）"
        yield (f"{label}：{value}；已知计数：{count.known_count}；"
               f"覆盖：{_COVERAGE_LABELS[count.coverage]}。")
        if count.missing_reasons:
            yield "计数缺口：" + "；".join(count.missing_reasons)


def _amount_lines(amounts):
    for field, label in _AMOUNT_LABELS.items():
        amount = getattr(amounts, field)
        values = []
        for point, point_label in (("opening", "期初"), ("change", "净变化"), ("closing", "期末")):
            total = getattr(amount, point)
            subtotal = getattr(amount.known_subtotals, point)
            status = getattr(amount.coverage, point)
            value = f"{total} 元" if total is not None else "未知（不按0处理）"
            known = f"{subtotal} 元" if subtotal is not None else "不可得"
            values.append(f"{point_label}：{value}，覆盖：{_COVERAGE_LABELS[status]}，已知小计：{known}")
        yield label + "（CNY）：" + "；".join(values) + "。"
        if amount.missing_reasons:
            yield "金额缺口：" + "；".join(amount.missing_reasons)


def _overview_items(request):
    """Project frozen typed values verbatim; never infer dates or sum members.

    Aggregate rows use the entire frozen provenance set. Detail rows use their
    own mapped sources. All appended text passes the original report bounds;
    long source text is rejected here rather than silently excerpted.
    """
    overview = request.overview_facts
    facts = overview.facts
    all_refs = sorted(overview_references(request))
    if not all_refs:
        return  # A source-free overview cannot become a sourced generated fact.

    def item(lines, refs=all_refs, kind="summary"):
        return ReportItem(kind=kind, text=_literal("\n".join(lines)), source_refs=list(dict.fromkeys(refs)))

    def mapped(refs):
        return [overview.references[ref] for ref in refs]

    def change_item(change, label):
        lines = [label + change.name, "客户：" + (change.customer_name or "名称未提供"),
                 *_amount_lines(change.amounts)]
        for reason in change.reasons:
            lines.append("已登记原因（原文）：" + reason.text)
        refs = [*change.source_refs, *(ref for reason in change.reasons for ref in reason.source_refs)]
        return item(lines, mapped(refs))

    yield item([
        "概览冻结统计（范围去重口径；成员明细不可相加替代范围总数）：",
        "周期：" + facts.period.label + "；范围：" + facts.scope.label + "；取材时点：" + facts.data_as_of.isoformat(),
        *_count_lines(facts.counts),
    ])
    yield item([
        f"概览冻结金额变化（{facts.quarter.year}年第{facts.quarter.quarter}季度；预测不代表已确认实收）：",
        "期初、期末及变化沿用原快照；部分覆盖的已知小计不能充当完整总额。",
        *_amount_lines(facts.amounts),
    ])
    for change in facts.opportunity_changes:
        yield change_item(change, "概览商机变化：")
    for field, label in _COUNT_LABELS.items():
        for row in getattr(facts, field):
            lines = ["概览明细 · " + label + "：" + row.name]
            if row.customer_name:
                lines.append("客户：" + row.customer_name)
            if row.opportunity_name:
                lines.append("商机：" + row.opportunity_name)
            lines.append("已登记发生时间：" + (row.occurred_at.isoformat() if row.occurred_at else "未提供"))
            lines.append("记录原文：" + row.summary)
            yield item(lines, mapped(row.source_refs))
    for step in facts.next_steps:
        lines = ["概览已登记下一步（不代表已执行或已完成）：", step.text]
        lines.append("结构化登记截止时间：" + step.due_at.isoformat() if step.due_at is not None
                     else "日期未结构化核定；不从自由文本推断具体日期或下周归属。")
        if step.original_date_text is not None:
            lines.append("原日期文字（原文，不替代结构化截止时间）：" + step.original_date_text)
        yield item(lines, mapped(step.source_refs), "plan")
    for member in facts.members:
        yield item([
            "概览成员明细：" + (member.name if member.name is not None else "姓名未知（不猜测）"),
            "成员状态：" + ("当前成员" if member.membership_state == "current" else "仅周期内成员"),
            "成员关联口径，未作团队去重；不能相加替代范围总数或总额。",
            *_count_lines(member.counts), *_amount_lines(member.amounts),
            *("成员缺口：" + reason for reason in member.missing_reasons),
        ])
        for change in member.opportunity_changes:
            yield change_item(change, "成员关联商机变化（不可累加为团队总额）· "
                              + (member.name if member.name is not None else "姓名未知") + "：")


def retain_frozen_facts(content, request):
    """Add bounded, cited backend notes only to selected output sections.

    This is deliberately after compose/summarize/combine, so another model call
    cannot erase the retained text. Any final schema/length failure rejects the
    candidate; this function never retries a provider or truncates the report.
    """
    try:
        result = content.model_copy(deep=True)
        if any(item.source_notes for section in result.sections for item in section.items):
            raise ValueError("retention accepts model prose, never prepopulated backend notes")
        sources = {source.source_id: source for source in request.sources}
        opportunities = ({fact.source_ref: fact for fact in request.sales_week_facts.opportunity_facts}
                         if request.sales_week_facts else {})
        allowed = set(sources) | {source.source_id for source in request.supplements} | overview_references(request)
        validate_content(result, request, generated=True)
        validate_section_references(result, request, allowed)
        for section in result.sections:
            cited = {ref for item in section.items for ref in item.source_refs}
            for ref, source in sources.items():
                if ref not in cited:
                    continue
                if section.id in {"ppl_first_30", "ppl_open_50"} and ref in opportunities:
                    _retain_opportunity_note(section, source, opportunities[ref], cited, sources)
                    continue
                notes = _source_notes(source)
                note_refs = [ref]
                action = _action_note(source)
                if action:
                    notes.append(action)
                    if source.truncated and not any("本快照来源已截断" in note for note in notes):
                        notes.append("本快照来源已截断，不能据此认定下一步材料完整。")
                if notes:
                    # A literal source note is not a newly asserted accomplishment.
                    kind = "plan" if section.id in {"next_week_plan", "next_week_collection"} else "summary"
                    section.items.append(ReportItem(kind=kind, text=_literal(source.title) + "\n" + "\n".join(notes),
                                                    source_refs=note_refs, metric_refs=[]))
            if getattr(request, "overview_facts", None) and section.id == overview_section(request):
                section.items.extend(_overview_items(request))
        result = ReportContent.model_validate(result.model_dump(mode="python"))
        validate_content(result, request, generated=True)
        validate_section_references(result, request, allowed)
        return result
    except (ValidationError, WorkReportError, ValueError):
        raise InvalidAgentResult("work_report.retained_facts_invalid") from ModelContractError(
            "work_report.retained_facts_invalid"
        )
