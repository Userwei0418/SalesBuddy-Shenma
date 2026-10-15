"""Deterministic six-section material projection; no inference or money planning."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sales_backend.contracts.work_report import (
    ActualPaymentLineage, ActualPeriodDependency, CollectionCoverage, FactSource, FirstThresholdFact,
    OpportunityFact, SalesWeekFacts, SectionFacts, SourceCoverage, TransitionCoverage,
    TrustedMetric, WeekBounds, WEEK_SECTIONS,
)
from sales_backend.domain.finance_totals import date_scoped_actuals
from sales_backend.domain.work_report import SHANGHAI, WorkReportError, canonical_hash
from sales_backend.domain.work_report_facts import business_time, in_period

# Formal workflow statuses (V038/V047 lifecycle, repositories/tasks.py).
PLANNED_TASK_STATUSES = ('pending_confirm', 'pending_execution', 'in_progress', 'deferred', 'pending_review')
GAPS = {
    'collection_quarter_precision': '部分有效回款只有季度精度，未分配为本周回款。',
    'collection_source_incomplete': '部分回款来源未完整确认，本周回款总额未核定。',
    'collection_day_plan_unavailable': '尚无正式日级回款计划，下周预计回款未核定；用户补充不属于系统核定事实。',
    'actual_source_not_selected': '未选择或未获授权读取实际回款来源，本周实际回款未知。',
    'threshold_history_incomplete': '商机历史尚不足以证明首次达到30%，本周首次达到商机数量未核定。',
    'opportunity_source_not_selected': '未选择商机来源，当前商机范围和首次达到记录未核定。',
    'progress_source_not_selected': '正式跟进来源未选择或材料不完整，不能据此认定本周没有跟进。',
    'plan_source_not_selected': '未选择正式待办来源，下周正式计划覆盖未知。',
    'plan_date_unknown': '部分下一步只有文本描述，没有明确日期，未认定为下周计划。',
    'plan_as_of_unknown': '读取的是当前登记计划，无法还原过去周报时点已知的计划。',
    'material_excerpt_truncated': '部分正文为明确截断的摘录，统计仍来自完整授权集合。',
}


def money(value):
    return format(Decimal(str(value)), 'f') if value is not None else None


def projection_fingerprint(row):
    keys = ('id', 'workspace_id', 'customer_id', 'opportunity_id', 'year', 'quarter', 'kind',
            'amount', 'source', 'status', 'occurred_on', 'period_start', 'period_end', 'period_precision',
            'complete', 'reference_changed', 'source_fingerprint', 'version_no', 'owner_user_ref_id', 'owner_team_id')
    return canonical_hash({key: (None if row.get(key) is None else
        money(row[key]) if key == 'amount' else str(row[key])) for key in keys})


def actual_source_id(row):
    return 'actual:' + canonical_hash({k: str(row.get(k)) for k in
        ('workspace_id', 'customer_id', 'opportunity_id', 'year', 'quarter', 'kind', 'id', 'source', 'version_no')})


def end_day(period):
    # A past period is half-open; current day facts only promise day precision.
    return (period.end_exclusive - timedelta(days=1)).date() if period.cutoff == period.end_exclusive else period.cutoff.date()


def collection_facts(rows, *, period, data_as_of):
    def fact_key(row):
        return (str(row['customer_id']), str(row.get('opportunity_id')), row['year'], row['quarter'], str(row['id']))

    seen = {}
    for raw in rows:
        row = dict(raw)
        if row['kind'] != 'collection':
            raise WorkReportError('WORK_REPORT_SOURCE_FAILED', '回款来源种类不一致', status=503)
        key = fact_key(row)
        if key in seen and seen[key] != row:
            raise WorkReportError('WORK_REPORT_SOURCE_FAILED', '回款来源出现冲突版本', status=503)
        seen[key] = row
    rows = list(seen.values())
    included, warning = date_scoped_actuals(rows, period.start.date(), end_day(period))
    included_keys = {fact_key(r) for r in included}
    # SQL fetched only overlap rows; retain quarter-only/incomplete placeholders.
    relevant = [r for r in rows if fact_key(r) in included_keys or (r.get('occurred_on') is None
        and r['period_start'] <= end_day(period) and r['period_end'] >= period.start.date())]
    exact = [r for r in included if r['period_precision'] == 'day' and r['complete'] and r.get('amount') is not None]
    exact_keys = {fact_key(r) for r in exact}
    subtotal = money(sum((Decimal(str(r['amount'])) for r in exact), Decimal(0)))
    incomplete = {(str(r.get('opportunity_id') or r['customer_id']), r['year'], r['quarter'])
                  for r in relevant if not r['complete'] or r.get('amount') is None}
    conflicts = {(str(r.get('opportunity_id') or r['customer_id']), r['year'], r['quarter'])
                 for r in relevant if r['status'] in ('needs_confirmation', 'stale_confirmation')}
    complete = not incomplete and not warning['unallocated_period_count']
    sources, lines, dependencies = [], [], []
    for row in relevant:
        try:
            ident = UUID(str(row['id']))
        except ValueError:
            # Null/conflict period rows do not become fake physical record IDs.
            dependencies.append(ActualPeriodDependency(effective_fact_id=str(row['id']), customer_id=row['customer_id'],
                opportunity_id=row.get('opportunity_id'), year=row['year'], quarter=row['quarter'],
                source_fingerprint=row.get('source_fingerprint'), resolution_version=row['version_no'],
                status=row['status'], projection_fingerprint=projection_fingerprint(row)))
            continue
        if row.get('source') not in ('operations', 'entries', 'historical'):
            raise WorkReportError('WORK_REPORT_SOURCE_FAILED', '回款来源类型未确认', status=503)
        source_id = actual_source_id(row)
        line = ActualPaymentLineage(source_ref=source_id, record_id=ident, effective_fact_id=str(row['id']),
            customer_id=row['customer_id'], opportunity_id=row.get('opportunity_id'), year=row['year'], quarter=row['quarter'],
            source=row['source'], status=row['status'], amount=money(row.get('amount')), occurred_on=row.get('occurred_on'),
            period_precision=row['period_precision'], period_start=row['period_start'], period_end=row['period_end'],
            complete=row['complete'], reference_changed=row['reference_changed'], source_fingerprint=row.get('source_fingerprint'),
            resolution_version=row['version_no'], projection_fingerprint=projection_fingerprint(row), included_in_week=fact_key(row) in exact_keys)
        lines.append(line)
        at = datetime.combine(line.occurred_on, time.min, SHANGHAI) if line.occurred_on else None
        text = (f'按发生日可核实回款：{line.amount}元；日期{line.occurred_on}。' if line.included_in_week else
                f'本条回款未计入本周核定总额；精度{line.period_precision}，状态{line.status}。')
        sources.append(FactSource(source_id=source_id, source_type='actual', record_id=ident,
            record_version=line.projection_fingerprint, actual_lineage=line, business_at=at, observed_at=data_as_of,
            time_basis='occurred_on' if at else 'observed_at', state_kind='occurred' if at else 'current',
            title='有效回款来源', text=text))
    codes = (['collection_quarter_precision'] if warning['unallocated_period_count'] else [])
    if incomplete:
        codes.append('collection_source_incomplete')
    coverage = SourceCoverage(source_type='actual', selected=True, status='complete' if complete else 'partial',
        total=len(relevant), included=len({s.record_id for s in sources}), statistics_complete=complete,
        missing_reasons=[GAPS[c] for c in codes])
    finance = CollectionCoverage(lines=lines, dependencies=dependencies, day_precision_confirmed_subtotal=subtotal,
        weekly_total=subtotal if complete else None, weekly_total_complete=complete,
        unallocated_period_count=warning['unallocated_period_count'],
        period_only_collection_amount=warning['period_only_collection_amount'],
        incomplete_period_count=len(incomplete), conflict_period_count=len(conflicts),
        precision_warning=warning['period_precision_warning'])
    metrics = [TrustedMetric(metric_id='week_collection_cny', label='本周核定回款', kind='amount',
        value=finance.weekly_total, unit='元', currency='CNY', display_value=(finance.weekly_total + '元') if complete else '未核定',
        definition='本周完整授权有效回款集合；仅有确定发生日且确认完整的金额，无季度未分配或来源冲突。',
        period_basis='occurred_in_period', complete=complete),
        TrustedMetric(metric_id='week_collection_day_subtotal_cny', label='可按日核实回款小计', kind='amount',
        value=subtotal, unit='元', currency='CNY', display_value=subtotal + '元',
        definition='仅本周具有确定发生日且确认完整的有效回款；不是含未知季度回款的完整周总额。',
        period_basis='occurred_in_period', complete=True)]
    return sources, coverage, finance, metrics, codes


def assemble_weekly(prepared, *, rows_by_type, selection, period, histories=None):
    """Extend the already assembled legacy materials without another calculation."""
    now = prepared.data_as_of
    next_start, next_end = period.end_exclusive, period.end_exclusive + timedelta(days=7)
    cov = {c.source_type: c for c in prepared.coverage}
    planned = []
    for row in rows_by_type.get('task', []):
        due = business_time(row.get('due_at'))
        if row['status'] not in PLANNED_TASK_STATUSES or due is None or not next_start <= due < next_end:
            continue
        text = '当前登记的正式计划，截止时间' + due.isoformat() + '。\n' + (row.get('description') or '')
        source = FactSource(source_id='task:' + str(row['id']) + '-planned', source_type='task', record_id=row['id'],
            record_version=str(row['version_no']), business_at=due, observed_at=now, time_basis='due_at', state_kind='planned',
            title=(row.get('title') or '正式计划')[:240], text=text[:16000], truncated=len(text) > 16000 or len(row.get('title') or '') > 240)
        planned.append(source)
    prepared.sources.extend(planned)
    if cov['task'].selected:
        task_sources = [s for s in prepared.sources if s.source_type == 'task']
        cov['task'].included = len({s.record_id for s in task_sources})
        cov['task'].total = cov['task'].included
        if any(s.truncated for s in planned):
            cov['task'].status, cov['task'].truncated = 'partial', True
            cov['task'].missing_reasons.append(GAPS['material_excerpt_truncated'])
    # Current plans remain text with unknown dates, never next-week date facts.
    source_map = {s.source_id: s for s in prepared.sources}
    visit_rows = {str(r['id']): r for r in rows_by_type.get('visit', [])}
    for source in prepared.sources:
        if source.source_type == 'visit':
            action = visit_rows.get(str(source.record_id), {}).get('next_action')
            if action:
                text = source.text + '\n已登记下一步（日期未结构化核定）：' + action
                source.text, source.truncated = text[:16000], source.truncated or len(text) > 16000
    opportunities = []
    histories = histories or {}
    progress_complete = cov['visit'].selected and cov['visit'].status == 'complete'
    progress_by_opportunity = {}
    for source in prepared.sources:
        if source.source_type != 'visit' or not in_period(source.business_at, period):
            continue
        visit = visit_rows.get(str(source.record_id), {})
        for ident in set(visit.get('linked_opportunity_ids', [visit.get('opportunity_id')])):
            progress_by_opportunity.setdefault(str(ident), []).append(source.source_id)
    for row in rows_by_type.get('opportunity', []):
        history = histories.get(str(row['id']))
        first = FirstThresholdFact.model_validate(history['first_ge30']) if history else FirstThresholdFact()
        qualified = []
        if first.status == 'proven' and period.start <= first.occurred_at < period.cutoff:
            qualified.append('first_ge30_in_week')
        if row['status'] == 'open' and row['probability'] is not None and row['probability'] >= 50:
            qualified.append('current_open_ge50')
        if not qualified:
            continue
        ref = 'opportunity:' + str(row['id']) + '-current'
        source = source_map[ref]
        if row.get('follow_up_plan'):
            text = source.text + '\n当前登记下一步（日期未结构化核定）：' + row['follow_up_plan']
            source.text, source.truncated = text[:16000], source.truncated or len(text) > 16000
        progress = progress_by_opportunity.get(str(row['id']), [])
        actions = ([ref] if row.get('follow_up_plan') else []) + [r for r in progress
            if visit_rows[str(source_map[r].record_id)].get('next_action')]
        opportunities.append(OpportunityFact(source_ref=ref, opportunity_id=row['id'], opportunity_version=row['version_no'],
            probability=row['probability'], stage_code=row['stage_code'], status=row['status'],
            qualified_as=qualified, progress_refs=progress, next_action_refs=actions,
            progress_state='recorded_in_week' if progress else 'no_recorded_progress_in_week' if progress_complete else 'unavailable',
            next_action_state='recorded' if actions else 'not_recorded' if progress_complete else 'unavailable',
            first_ge30=first))
    for kind in ('visit', 'opportunity'):
        if any(s.truncated for s in prepared.sources if s.source_type == kind):
            cov[kind].status, cov[kind].truncated = 'partial', True
            if GAPS['material_excerpt_truncated'] not in cov[kind].missing_reasons:
                cov[kind].missing_reasons.append(GAPS['material_excerpt_truncated'])
    finance, actual_codes = None, ['actual_source_not_selected']
    if 'actual' in selection.sources:
        sources, actual_coverage, finance, metrics, actual_codes = collection_facts(
            rows_by_type['actual'], period=period, data_as_of=now)
        prepared.sources.extend(sources)
        prepared.metrics.extend(metrics)
    else:
        actual_coverage = SourceCoverage(source_type='actual', selected=False, status='not_selected', included=0)
    prepared.coverage.append(actual_coverage)
    sections = {}
    for ident in WEEK_SECTIONS:
        sections[ident] = SectionFacts(section_id=ident, availability='unknown', observed_at=now,
            period_basis='planned_in_period' if ident.startswith('next_week') else
            'current_at_snapshot' if ident == 'ppl_open_50' else 'occurred_in_period')
    def fill(ident, refs, *, codes=(), metrics=(), total=None, population=False, content=False):
        section = sections[ident]
        section.source_refs = list(dict.fromkeys(refs))
        section.metric_refs = list(metrics)
        section.missing_codes = list(codes)
        section.missing_reasons = [GAPS[c] for c in codes]
        section.population_total, section.included = total, total if total is not None else len(section.source_refs)
        section.population_complete, section.content_complete = population, content
        section.availability = 'partial' if codes and refs else 'unknown' if codes else 'available'
    fill('actual_collection', [s.source_id for s in prepared.sources if s.source_type == 'actual'], codes=actual_codes,
        metrics=[m.metric_id for m in prepared.metrics if m.metric_id.startswith('week_collection')],
        total=actual_coverage.total, population=actual_coverage.selected,
        content=actual_coverage.status == 'complete')
    plan_codes = [] if cov['task'].selected else ['plan_source_not_selected']
    if next_start < now:
        plan_codes.append('plan_as_of_unknown')
    if any(o.next_action_refs for o in opportunities):
        plan_codes.append('plan_date_unknown')
    fill('next_week_plan', [s.source_id for s in planned], codes=plan_codes, total=len(planned) if cov['task'].selected else None,
        population=cov['task'].selected, content=not plan_codes and not any(s.truncated for s in planned))
    fill('next_week_collection', [], codes=['collection_day_plan_unavailable'])
    candidates = rows_by_type.get('opportunity', [])
    history_complete = sum(bool(histories.get(str(r['id']), {}).get('first_ge30', {}).get('complete_from_origin'))
                           for r in candidates)
    unknown_count = len(candidates) - history_complete
    cohort_complete = cov['opportunity'].selected and not unknown_count
    first_cohort = [o for o in opportunities if 'first_ge30_in_week' in o.qualified_as]
    open_cohort = [o for o in opportunities if 'current_open_ge50' in o.qualified_as]
    first_codes = (['opportunity_source_not_selected'] if not cov['opportunity'].selected else
                   ['threshold_history_incomplete'] if unknown_count else [])
    if first_cohort and not progress_complete:
        first_codes.append('progress_source_not_selected')
    if any(source_map[o.source_ref].truncated for o in first_cohort):
        first_codes.append('material_excerpt_truncated')
    fill('ppl_first_30', [r for o in first_cohort for r in [o.source_ref, *o.progress_refs, *o.next_action_refs]],
        codes=first_codes, total=len(first_cohort) if cohort_complete else None,
        population=cohort_complete, content=not first_codes)
    sections['ppl_first_30'].included = len(first_cohort)
    open_codes = ([] if cov['opportunity'].selected else ['opportunity_source_not_selected']) + ([] if progress_complete else ['progress_source_not_selected'])
    if any(source_map[o.source_ref].truncated for o in open_cohort):
        open_codes.append('material_excerpt_truncated')
    fill('ppl_open_50', [r for o in open_cohort for r in [o.source_ref, *o.progress_refs, *o.next_action_refs]],
        codes=open_codes, total=len(open_cohort) if cov['opportunity'].selected else None,
        population=cov['opportunity'].selected, content=not open_codes)
    fill('summary', [s.source_id for s in prepared.sources if s.state_kind != 'planned'],
        metrics=[m.metric_id for m in prepared.metrics if m.complete],
        total=len(prepared.sources), population=all(c.statistics_complete for c in prepared.coverage if c.selected),
        content=all(c.status == 'complete' for c in prepared.coverage if c.selected))
    for supplement in selection.supplements:
        ident = supplement.section_id or 'summary'
        sections[ident].source_refs.append('user:' + str(supplement.id))
        if sections[ident].availability == 'unknown' and ident != 'next_week_collection':
            sections[ident].availability = 'partial'
    prepared.sales_week_facts = SalesWeekFacts(data_as_of=now,
        next_period=WeekBounds(start=next_start, end_exclusive=next_end), sections=list(sections.values()),
        finance=finance, opportunity_facts=opportunities,
        history_guards=[histories[str(r['id'])]['history_guard'] for r in candidates if str(r['id']) in histories],
        transition_coverage=TransitionCoverage(candidate_count=len(candidates), history_complete_count=history_complete,
            history_unknown_count=unknown_count, proven_first_count=len(first_cohort),
            cohort_complete=cohort_complete,
            trustworthy_from=min((business_time(h['first_ge30']['trustworthy_from']) for h in histories.values()
                if h['first_ge30'].get('trustworthy_from')), default=None),
            missing_codes=['threshold_history_incomplete'] if unknown_count else
                [] if cov['opportunity'].selected else ['opportunity_source_not_selected']))
    return prepared
