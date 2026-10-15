"""Deterministic report periods, canonical hashes and persistence guards."""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from sales_backend.contracts.work_report import (
    METRIC_TOKEN,
    GenerationInput,
    GenerationResult,
    PeriodSelection,
    ReportContent,
    ResolvedPeriod,
    SchemeConfig,
)

SHANGHAI = ZoneInfo('Asia/Shanghai')


class WorkReportError(Exception):
    def __init__(self, code: str, detail: str, *, status: int = 409,
                 current_version: int | None = None, retryable: bool = False):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.current_version = current_version
        self.retryable = retryable


def resolve_period(selection: PeriodSelection, *, now: datetime) -> ResolvedPeriod:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('周期解析必须提供带时区的当前时间')
    local_now = now.astimezone(SHANGHAI)
    anchor = selection.anchor_date or local_now.date()
    if anchor > local_now.date():
        raise WorkReportError('WORK_REPORT_FUTURE_PERIOD', '不能选择未来日期', status=422)
    kind = selection.kind
    if kind == 'day':
        start, end = anchor, anchor + timedelta(days=1)
    elif kind in ('week', 'two_weeks'):
        monday = anchor - timedelta(days=anchor.weekday())
        start = monday - timedelta(days=7 if kind == 'two_weeks' else 0)
        end = monday + timedelta(days=7)
    else:
        month = anchor.month if kind == 'month' else 1 + ((anchor.month - 1) // 3) * 3
        start = date(anchor.year, month, 1)
        after = month + (1 if kind == 'month' else 3)
        end = date(anchor.year + (after - 1) // 12, (after - 1) % 12 + 1, 1)
    start_at, end_at = (datetime.combine(d, time.min, SHANGHAI) for d in (start, end))
    cutoff = min(local_now, end_at)
    label = (start.isoformat() if kind == 'day'
             else f'{start.isoformat()}—{(end - timedelta(days=1)).isoformat()}')
    return ResolvedPeriod(kind=kind, start=start_at, end_exclusive=end_at,
                          cutoff=cutoff, label=label)


def canonical_hash(value) -> str:
    """UTF-8 JSON; object keys sorted, list order significant, no NaN."""
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return sha256(encoded).hexdigest()


def default_scheme(*, sales_week: bool = False) -> SchemeConfig:
    if sales_week:
        return SchemeConfig(facts_contract='work_report.sales_week_facts.v1', sections=[
            {'id': 'summary', 'title': '本周工作总结'},
            {'id': 'actual_collection', 'title': '本周实际回款'},
            {'id': 'next_week_plan', 'title': '下周工作计划'},
            {'id': 'next_week_collection', 'title': '下周预计回款'},
            {'id': 'ppl_first_30', 'title': '本周首次达到≥30% PPL：进展与下一步'},
            {'id': 'ppl_open_50', 'title': '当前全部未关闭≥50% PPL：进展与下一步'},
        ])
    return SchemeConfig(sections=[
        {'id': 'overview', 'title': '本期概况'},
        {'id': 'progress', 'title': '工作进展'},
        {'id': 'risks', 'title': '风险与待协调'},
        {'id': 'next_steps', 'title': '下一步计划'},
    ])


def validate_content(content: ReportContent, request: GenerationInput, *, generated: bool) -> None:
    """Structural/reference guard, not proof that arbitrary prose is truthful.

    Manual edits may rename or reorder sections; source and metric references
    remain bound to the same immutable snapshot. Generated section identity and
    order must match the trusted scheme (or selected existing sections).
    """
    sources = {s.source_id: s for s in request.sources}
    supplements = {s.source_id: s for s in request.supplements}
    metrics = {m.metric_id: m for m in request.metrics}
    overview_refs = set(request.overview_facts.references.values()) if request.overview_facts else set()
    planned_overview = ({request.overview_facts.references[s.source_id]
                        for s in request.overview_facts.facts.sources if s.kind == 'task'}
                       if request.overview_facts else set())
    if request.overview_facts:
        planned_overview.update(request.overview_facts.references[ref]
            for step in request.overview_facts.facts.next_steps for ref in step.source_refs)
    if generated:
        if request.rewrite:
            selected = set(request.rewrite.section_ids)
            expected = [(s.id, s.title) for s in request.rewrite.previous_content.sections if s.id in selected]
            if content.title != request.rewrite.previous_content.title:
                raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '局部重写不能修改整份报告标题', status=422)
        else:
            expected = [(s.id, s.title) for s in request.scheme.config.sections if s.enabled]
        if [(s.id, s.title) for s in content.sections] != expected:
            raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '生成章节与已冻结方案或重写选择不一致', status=422)
    required_missing = {reason for c in request.coverage for reason in c.missing_reasons}
    if request.sales_week_facts:
        required_missing.update(reason for section in request.sales_week_facts.sections for reason in section.missing_reasons)
    from sales_backend.contracts.work_report_overview import overview_missing_reasons
    required_missing.update(overview_missing_reasons(request.overview_facts))
    if request.review_facts:
        required_missing.update(request.review_facts.missing_data)
    if content.review_content is not None:
        from sales_backend.services.agent_platform.routing import InvalidAgentResult
        from sales_backend.services.work_report_generation.review import validate_review_content
        if request.review_facts is None or request.review_mode is None:
            raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', 'Review正文缺少同次授权事实', status=422)
        try:
            validate_review_content(content.review_content.model_dump(mode='python'), request.review_facts,
                                    mode=request.review_mode)
        except InvalidAgentResult:
            raise WorkReportError('WORK_REPORT_INVALID_OUTPUT',
                                  'Review正文或引用与冻结事实不一致', status=422) from None
    if not required_missing <= set(content.missing_data):
        raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '正文不能隐去已知材料缺口', status=422)
    for section in content.sections:
        note_refs = set()
        for item in section.items:
            for note in item.source_notes or []:
                weekly = request.sales_week_facts
                facts = {fact.source_ref: fact for fact in weekly.opportunity_facts} if weekly else {}
                allowed = next((set(s.source_refs) for s in weekly.sections if s.section_id == section.id), set()) if weekly else set()
                primary = note.source_refs[0]
                fact = facts.get(primary)
                if (section.id not in {'ppl_first_30', 'ppl_open_50'} or fact is None
                        or primary in note_refs or not set(note.source_refs) <= allowed & sources.keys()
                        or not set(note.source_refs) <= {primary, *fact.next_action_refs}):
                    raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '来源说明不属于本章冻结商机或引用重复', status=422)
                note_refs.add(primary)
            if generated and item.kind == 'manual':
                raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '模型不能冒充人工编辑正文', status=422)
            refs, metric_refs = set(item.source_refs), set(item.metric_refs)
            if not refs <= sources.keys() | supplements.keys() | overview_refs or not metric_refs <= metrics.keys():
                raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '正文包含未知来源或统计引用', status=422)
            if any(not metrics[key].complete or metrics[key].value is None for key in metric_refs):
                raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '不能将不完整或未知统计写成精确成果', status=422)
            if item.kind == 'user_supplement' and (not refs or not refs <= supplements.keys() or metric_refs):
                raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '用户补充只能引用本次手工材料', status=422)
            if item.kind == 'fact':
                if not (refs & (sources.keys() | overview_refs) or metric_refs) or refs & supplements.keys():
                    raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '系统事实必须有系统来源或可信统计', status=422)
                if any(sources[key].state_kind == 'planned' for key in refs & sources.keys()) or refs & planned_overview:
                    raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '计划材料不能写成已发生事实', status=422)
            if generated and not refs and not metric_refs:
                raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '生成条目必须保留事实或材料依据', status=422)


def validate_generation_result(result: GenerationResult, request: GenerationInput) -> None:
    expected = (request.snapshot_id, request.snapshot_hash, request.scheme.scheme_id, request.scheme.version_no)
    if (result.snapshot_id, result.snapshot_hash, result.scheme_id, result.scheme_version) != expected:
        raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '结果与作业快照或方案版本不一致', status=422)
    coverage = 'partial' if (any(c.status == 'partial' for c in request.coverage)
                             or (request.review_facts and request.review_facts.missing_data)) else 'complete'
    if result.coverage_status != coverage:
        raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '结果不能更改事实快照的覆盖声明', status=422)
    if result.content is not None:
        if result.status == 'generated' and request.review_mode and result.content.review_content is None:
            raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', 'Review未完成，不能标记生成成功', status=422)
        validate_content(result.content, request, generated=True)
    if request.review_mode == 'daily' and result.status == 'basic_summary':
        raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', 'AI日总结不可用时不能以基础汇总代替', status=422)


def merge_rewrite(result: GenerationResult, request: GenerationInput) -> ReportContent:
    validate_generation_result(result, request)
    if request.rewrite is None or result.content is None:
        raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '没有可合并的局部重写结果', status=422)
    replacements = {s.id: s for s in result.content.sections}
    original = request.rewrite.previous_content
    return ReportContent(title=original.title,
                         sections=[replacements.get(s.id, s) for s in original.sections],
                         missing_data=list(dict.fromkeys([*original.missing_data, *result.content.missing_data])),
                         review_content=original.review_content)


def validate_source_note_revision(content: ReportContent, previous: ReportContent) -> None:
    """Editing narrative cannot forge, alter, move across sections or drop evidence."""
    def notes(value):
        return Counter((section.id, canonical_hash(note)) for section in value.sections
                       for item in section.items for note in item.source_notes or [])
    if notes(content) != notes(previous):
        raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '来源说明须与原修订一致，不能新增、改写或移除', status=422)
    if canonical_hash(content.review_content) != canonical_hash(previous.review_content):
        raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', 'Review结构化结果须保留原授权引用，请重新生成', status=422)


def render_report_text(content: ReportContent, metrics: dict[str, str], *, markdown: bool = True,
                       review_facts=None) -> str:
    """Keep prose once; evidence is explicitly labelled in a separate appendix."""
    rows = [('## ' if markdown else '') + content.title]
    appendix = []
    for section in content.sections:
        rows.extend(['', ('### ' if markdown else '') + section.title])
        for item in section.items:
            text = METRIC_TOKEN.sub(lambda m: metrics[m.group(1)], item.text)
            prefix = {'user_supplement': '用户补充：', 'manual': '人工编辑：'}.get(item.kind, '')
            rows.append(('- ' if markdown else '') + prefix + text)
            for note in item.source_notes or []:
                rows.append(('> ' if markdown else '') + note.title + ' · 来源限定：\n'
                            + note.notice_text.replace('\n', '\n> ' if markdown else '\n'))
                appendix.append((section.title, note))
    if content.review_content is not None:
        if review_facts is None:
            raise WorkReportError('WORK_REPORT_INVALID_OUTPUT', '导出Review须加载原授权事实', status=422)
        from sales_backend.contracts.work_report_review import validate_review_facts
        from sales_backend.services.work_report_generation.review import render_review_content
        rows.extend(['', render_review_content(content.review_content, validate_review_facts(review_facts),
                                               markdown=markdown).rstrip('\n')])
    if content.missing_data:
        rows.extend(['', '材料缺口：', *[('- ' if markdown else '') + reason for reason in content.missing_data]])
    if appendix:
        rows.extend(['', ('### ' if markdown else '') + '来源核对附录'])
        for section_title, note in appendix:
            rows.extend(['', ('#### ' if markdown else '') + section_title + ' · ' + note.title,
                         note.text, '来源引用：' + '、'.join(note.source_refs)])
    return '\n'.join(rows) + '\n'


def render_content(content: ReportContent, request: GenerationInput, *, markdown: bool = True) -> str:
    validate_content(content, request, generated=False)
    return render_report_text(content, {m.metric_id: m.display_value for m in request.metrics}, markdown=markdown,
                              review_facts=request.review_facts)
