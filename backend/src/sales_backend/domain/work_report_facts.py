"""Build report material only from complete, already authorized repository rows.

No inference of dates, stage movements or task completion. Aggregate facts use
the full unique record set; excerpt limits are explicit material coverage gaps.
"""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from uuid import UUID

from pydantic import AwareDatetime, Field

from sales_backend.contracts.work_report import (
    FactSource,
    OverviewGenerationFacts,
    PersonReference,
    ReportModel,
    ResolvedPeriod,
    SalesWeekFacts,
    SourceCoverage,
    SourceType,
    TrustedMetric,
)
from sales_backend.contracts.work_report_review import ReviewFacts, ReviewFactsV2
from sales_backend.db import json_value
from sales_backend.domain.task_outcome import task_end_reason
from sales_backend.domain.opportunities import STAGES, STAGE_BY_PROBABILITY
from sales_backend.domain.work_report import SHANGHAI, WorkReportError
from sales_backend.repositories.opportunity_business_date import business_created_on

OPEN_RISKS = frozenset(('new', 'pending', 'in_progress', 'escalated', 'accepted'))
OPEN_TASK_LABELS = {
    'pending_confirm': '待确认', 'pending_execution': '待执行', 'in_progress': '进行中',
    'deferred': '已延期', 'pending_review': '待验收', 'needs_adjustment': '需调整',
}
TASK_PROCESSING_LABELS = {'adjustment_request': '申请调整', 'paused': '暂停', 'awaiting_review': '待验收'}


def task_detail(value, *keys):
    if isinstance(value, dict):
        return next((value.get(key) for key in keys if isinstance(value.get(key), str) and value[key].strip()), '')
    return value if isinstance(value, str) else ''


def current_task_text(row, execution):
    reason = execution.get('processing_reason')
    if row['status'] == 'pending_review':
        reason = 'awaiting_review'
    elif execution.get('pause'):
        reason = 'paused'
    lines = [f"取数时点当前状态：{OPEN_TASK_LABELS[row['status']]}。未完成，不计入本期完成数量；不能据此认定本期发生推进。"]
    if reason == 'awaiting_review':
        lines.append('执行人已提交完成反馈，仍待下发人验收，不能认定已验收通过或目标达成。')
    for caption, value in (
        ('任务说明', row.get('description')),
        ('最新反馈', task_detail(execution.get('latest_feedback'), 'text')),
        ('最近记录原文', row.get('latest_record_note') if not execution.get('latest_feedback') else None),
        ('已登记下一步', execution.get('next_step')),
        ('处理原因', TASK_PROCESSING_LABELS.get(reason)),
        ('处理说明', task_detail(execution.get('processing_detail') or execution.get('pause'), 'reason', 'note')
         or (row.get('completion_note') if row['status'] == 'pending_review' else None)),
        ('延期原因', task_detail(execution.get('defer'), 'reason')),
    ):
        if value:
            lines.append(f'{caption}：{value}')
    if row.get('due_at'):
        lines.append('当前截止时间：' + business_time(row['due_at']).isoformat())
    return '\n'.join(lines)


class PreparedFacts(ReportModel):
    data_as_of: AwareDatetime
    coverage: list[SourceCoverage] = Field(default_factory=list)
    sources: list[FactSource] = Field(default_factory=list)
    metrics: list[TrustedMetric] = Field(default_factory=list)
    sales_week_facts: SalesWeekFacts | None = None
    overview_facts: OverviewGenerationFacts | None = None
    overview_dependencies: list[dict] | None = None
    review_facts: ReviewFacts | ReviewFactsV2 | None = None
    review_dependencies: list[dict] | None = None


def business_time(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise WorkReportError('WORK_REPORT_SOURCE_FAILED', '业务记录时间缺少明确时区', status=503)
    return value


def in_period(value, period):
    value = business_time(value)
    return value is not None and period.start <= value < period.cutoff


def unique_rows(rows):
    seen = {}
    for row in rows:
        key = str(UUID(str(row['id'])))
        if key in seen and seen[key] != row:
            raise WorkReportError('WORK_REPORT_SOURCE_FAILED', '同一业务记录出现冲突版本', status=503)
        seen[key] = row
    return [seen[k] for k in sorted(seen)]


def count_metric(key, label, value, *, definition, complete=True, basis='occurred_in_period'):
    return TrustedMetric(metric_id=key, label=label, kind='count', value=str(value) if complete else None,
                         unit={'tasks_completed': '项', 'tasks_progressing': '项',
                               'opportunities_visible': '个', 'opportunities_created': '个'}.get(key, '条'), display_value=str(value) if complete else '未核定',
                         definition=definition, period_basis=basis, complete=complete)


class FactAssembler:
    def __init__(self, *, period: ResolvedPeriod, data_as_of: datetime):
        self.period, self.data_as_of = period, business_time(data_as_of)
        self.result = PreparedFacts(data_as_of=data_as_of)
        self.completed = set()

    def _source(self, kind, row, *, title, text, at, basis, state='occurred', suffix=''):
        source_id = kind + ':' + str(row['id']) + suffix
        truncated = len(title) > 240 or len(text) > 16000
        contributors = [PersonReference(**value) for value in json_value(row.get('contributors') or [])]
        source = FactSource(source_id=source_id, source_type=kind, record_id=row['id'],
                            record_version=str(row['version_no']), business_at=business_time(at),
                            observed_at=self.data_as_of, time_basis=basis, state_kind=state,
                            title=title[:240], text=text[:16000], contributors=contributors, truncated=truncated)
        self.result.sources.append(source)
        return source

    def add(self, kind: SourceType, rows):
        if kind in self.completed:
            raise ValueError('同类来源只能完整装配一次')
        self.completed.add(kind)
        rows = unique_rows(rows)
        before = len(self.result.sources)
        gaps = []
        stats_complete = True
        metrics = self.result.metrics
        if kind == 'visit':
            selected = []
            unknown = 0
            for row in rows:
                at = business_time(row['interaction_at'])
                if at is None:
                    unknown += 1
                if at is not None and not in_period(at, self.period):
                    continue
                selected.append(row)
                self._source(kind, row, title=row.get('title') or '跟进记录', at=at,
                             basis='interaction_at' if at else 'observed_at', state='occurred' if at else 'current',
                             text=('业务时间未登记，不能认定发生于本期。\n' if at is None else '')
                                  + (row.get('follow_up_record') or '未登记跟进正文。'))
            if unknown:
                gaps.append(f'{unknown}条可见正式跟进缺少业务时间，本期跟进数量未核定')
            metrics.append(count_metric('visits_completed', '本期正式跟进', len(selected) - unknown,
                                         complete=not unknown, definition='确认/归档跟进按interaction_at落入本期去重计数'))
            stats_complete = not unknown
        elif kind == 'task':
            selected = []
            unknown = completed = progressing = 0
            for row in rows:
                execution = json_value(row.get('execution')) or {}
                if row['status'] in OPEN_TASK_LABELS:
                    selected.append(row)
                    progressing += 1
                    self._source(kind, row, title=row.get('title') or '当前未完成待办', at=None,
                                 basis='observed_at', state='current', suffix='-current',
                                 text=current_task_text(row, execution))
                    continue
                if row['status'] != 'completed':
                    continue
                at = business_time(row['completed_at'])
                if at is not None and not in_period(at, self.period):
                    continue
                selected.append(row)
                outcome = task_end_reason(row)
                if outcome == "completed":
                    completed += at is not None
                    unknown += at is None
                elif outcome == "unknown":
                    unknown += 1
                self._source(kind, row, title=row.get('title') or ('已完成待办' if outcome == 'completed' else '已结束待办'), at=at,
                             basis='completed_at' if at else 'observed_at', state='occurred' if at else 'current',
                             text=('已结束但结果依据未知，不计入正常完成数量。\n' if outcome == 'unknown' else
                                   '已停止，不计入正常完成数量；不能写成验收通过或目标达成。\n' if outcome == 'stopped' else
                                   '已结束但没有完成时间，不能认定本期完成。\n' if at is None else
                                   '本期由下发人决定不再继续，并非验收通过或目标达成。\n' if row.get('completion_action') == 'close' else
                                   '本期已完成；完成数量不等于目标达成率或业绩贡献。\n')
                                  + (task_detail(execution.get('completion_result'), 'note')
                                     or row.get('completion_note') or row.get('description') or '未登记完成说明。'))
            if unknown:
                gaps.append(f'{unknown}条已结束待办缺少正常完成依据或completed_at，本期完成数量未核定')
            metrics.extend([
                count_metric('tasks_completed', '本期完成待办', completed, complete=not unknown,
                             definition='正常完成待办按completed_at落入本期去重计数；排除停止、取消、拒绝及结果未知，不以updated_at代替，不代表目标达成'),
                count_metric('tasks_progressing', '当前未完成待办', progressing, basis='current_at_snapshot',
                             definition='取数时点进行中、已延期、需调整及兼容旧流程的未完成待办去重计数，不代表本期新增或历史期末存量'),
            ])
            stats_complete = not unknown
        elif kind == 'opportunity':
            selected = rows
            created_count, unknown = 0, 0
            for row in rows:
                created_on = business_created_on(json_value(row['creation_date']))
                created_at = datetime.combine(created_on, time.min, SHANGHAI) if created_on else None
                if created_at is None:
                    unknown += 1
                stage_labels = {code: STAGES[probability] for probability, code in STAGE_BY_PROBABILITY.items()}
                stage = stage_labels.get(row.get('stage_code'), row.get('stage_code') or '未登记阶段')
                status = {'open': '进行中', 'won': '已赢单', 'lost': '已丢单'}.get(row.get('status'), row.get('status') or '未登记状态')
                state_text = f"快照时点的当前状态：{stage}；{status}。不能据此认定本期阶段推进。"
                self._source(kind, row, title=row.get('name') or '商机', at=None, basis='observed_at', state='current',
                             text=state_text + '\n' + (row.get('summary') or ''), suffix='-current')
                if in_period(created_at, self.period):
                    created_count += 1
                    self._source(kind, row, title=row.get('name') or '新增商机', at=created_at,
                                 basis='business_created_on', text='按登记的业务创建日期，本期新增。', suffix='-created')
            if unknown:
                gaps.append(f'{unknown}条可见商机缺少可信业务创建日期，本期新增数量未核定')
            metrics.extend([
                count_metric('opportunities_visible', '当前可见商机', len(rows), basis='current_at_snapshot',
                             definition='取数时点的完整授权商机集合去重计数，不代表历史期末存量'),
                count_metric('opportunities_created', '本期新增商机', created_count, complete=not unknown,
                             definition='按可信业务创建日期落入本期去重计数，历史导入时间不作创建时间'),
            ])
            stats_complete = not unknown
            currencies = sorted({str(r['currency']) for r in rows if r.get('currency')})
            for currency in currencies:
                subset = [r for r in rows if r.get('currency') == currency]
                amount_known = all(r.get('amount') is not None for r in subset)
                total = sum((Decimal(str(r['amount'])) for r in subset if r.get('amount') is not None), Decimal(0))
                metrics.append(TrustedMetric(metric_id='opportunity_amount_' + currency.lower(),
                    label=f'当前商机登记金额（{currency}）', kind='amount', value=format(total, 'f') if amount_known else None,
                    unit='原币', currency=currency, display_value=format(total, 'f') if amount_known else '未登记完整',
                    definition='快照时点授权商机按记录币种分别去重求和；不是签约、确收或回款',
                    period_basis='current_at_snapshot', complete=amount_known))
                if not amount_known:
                    gaps.append(f'{currency}商机金额存在未登记值，不将其视作0')
                    stats_complete = False
            if any(not r.get('currency') for r in rows):
                gaps.append('部分商机未登记币种，未将其金额计入其他币种')
                stats_complete = False
        else:  # risk
            selected, opened, resolved, current, unknown_opened, unknown_resolved = [], 0, 0, 0, 0, 0
            for row in rows:
                opened_at, resolved_at = business_time(row['opened_at']), business_time(row['resolved_at'])
                is_open = row['status'] in OPEN_RISKS
                opened_here = in_period(opened_at, self.period)
                resolved_here = row['status'] == 'resolved' and in_period(resolved_at, self.period)
                unknown_opened += opened_at is None
                unknown_resolved += row['status'] == 'resolved' and resolved_at is None
                current += is_open
                if not (is_open or opened_here or resolved_here or opened_at is None
                        or (row['status'] == 'resolved' and resolved_at is None)):
                    continue
                selected.append(row)
                if is_open or not (opened_here or resolved_here):
                    self._source(kind, row, title=row.get('title') or '风险', at=None, basis='observed_at', state='current',
                                 text='快照时点的当前风险状态：' + row['status'] + '。\n' + (row.get('description') or ''), suffix='-current')
                if opened_here:
                    opened += 1
                    self._source(kind, row, title=row.get('title') or '本期新增风险', at=opened_at, basis='opened_at',
                                 text='本期登记风险。\n' + (row.get('description') or ''), suffix='-opened')
                if resolved_here:
                    resolved += 1
                    self._source(kind, row, title=row.get('title') or '本期处理风险', at=resolved_at, basis='resolved_at',
                                 text='本期处理风险。\n' + (row.get('resolution_note') or '未登记处理说明。'), suffix='-resolved')
            if unknown_opened:
                gaps.append(f'{unknown_opened}条风险缺少opened_at，本期新增数量未核定')
            if unknown_resolved:
                gaps.append(f'{unknown_resolved}条已处理风险缺少resolved_at，本期处理数量未核定')
            metrics.extend([
                count_metric('risks_opened', '本期新增风险', opened, complete=not unknown_opened, definition='按opened_at落入本期去重计数'),
                count_metric('risks_resolved', '本期处理风险', resolved, complete=not unknown_resolved, definition='按resolved_at落入本期去重计数'),
                count_metric('risks_current_open', '当前未处理风险', current, basis='current_at_snapshot', definition='快照时点未关闭风险，不代表历史期末存量'),
            ])
            stats_complete = not (unknown_opened or unknown_resolved)
        added = self.result.sources[before:]
        truncated = any(s.truncated for s in added)
        if truncated:
            gaps.append(f'{kind}来源有正文超出单条展示长度；数量统计使用完整记录，材料正文仅提供明确标记的摘录')
        self.result.coverage.append(SourceCoverage(source_type=kind, selected=True,
            status='partial' if gaps else 'complete', total=len(selected), included=len({s.record_id for s in added}),
            statistics_complete=stats_complete, truncated=truncated, missing_reasons=gaps))

    def finish(self):
        for kind in ('visit', 'opportunity', 'task', 'risk'):
            if kind not in self.completed:
                self.result.coverage.append(SourceCoverage(source_type=kind, selected=False,
                    status='not_selected', included=0))
        self.result.coverage.sort(key=lambda c: ('visit', 'opportunity', 'task', 'risk').index(c.source_type))
        return self.result
