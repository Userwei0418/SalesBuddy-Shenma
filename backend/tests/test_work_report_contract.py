from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from sales_backend.contracts.work_report import (
    DocumentQuery, DocumentSchemeReference,
    GenerationInput, GenerationResult, PeriodSelection, ReportContent, ReportItem,
    RunCreate, SchemeUpdate, ScopeSelection, ShareRequest, SnapshotSelection, SourceCoverage,
    TrustedMetric,
)
from sales_backend.domain.work_report import (
    WorkReportError, canonical_hash, default_scheme, merge_rewrite, render_content,
    resolve_period, validate_content, validate_generation_result,
)

U1, U2, U3 = (str(UUID(int=i)) for i in (1, 2, 3))
NOW = datetime.fromisoformat('2026-10-09T13:00:00+08:00')


def test_shared_document_scheme_cannot_include_private_writing_examples():
    data = dict(scheme_id=U3, version_no=1, name='方案', config_hash='a' * 64)
    assert set(DocumentSchemeReference(**data).model_dump()) == set(data)
    with pytest.raises(ValidationError):
        DocumentSchemeReference(**data, config={'reference_texts': [{'text': '私有范文'}]})
    # Full configuration stays available only in the private generation input.
    assert GenerationInput(**request_data()).scheme.config == default_scheme()


@pytest.mark.parametrize('data', [{'q': 'x' * 121}, {'status': 'all'}, {'limit': 101}, {'offset': -1}])
def test_document_filter_is_bounded_and_explicit(data):
    with pytest.raises(ValidationError):
        DocumentQuery(**data)


def request_data():
    config = default_scheme()
    return dict(snapshot_id=U1, snapshot_hash='a' * 64,
                period=resolve_period(PeriodSelection(), now=NOW).model_dump(mode='json'),
                scope=dict(kind='self', label='本人', member_ids=[U1]),
                permission_version='v1', data_as_of=NOW.isoformat(),
                coverage=[dict(source_type=k, selected=k == 'visit',
                               status='complete' if k == 'visit' else 'not_selected',
                               total=1 if k == 'visit' else None, included=1 if k == 'visit' else 0,
                               statistics_complete=k == 'visit')
                          for k in ('visit', 'opportunity', 'task', 'risk')],
                sources=[dict(source_id='visit:' + U2, source_type='visit', record_id=U2,
                              business_at=NOW.isoformat(), observed_at=NOW.isoformat(),
                              time_basis='interaction_at', state_kind='occurred', title='拜访', text='已确认需求')],
                metrics=[dict(metric_id='visits', label='拜访次数', kind='count', value='1', unit='次',
                              display_value='1', definition='完整授权范围正式拜访去重数',
                              period_basis='occurred_in_period', complete=True)],
                supplements=[dict(source_id='user:' + U3, author_id=U1, text='明天跟进', declared_kind='plan')],
                scheme=dict(scheme_id=U3, version_no=1, name='默认方案', config=config.model_dump(mode='json'),
                            config_hash=canonical_hash(config)), rules_version='v1')


def content_data():
    return dict(title='本周工作汇报', sections=[
        dict(id=s.id, title=s.title, items=([dict(kind='fact', text='本期拜访 {{metric:visits}} 次。',
                                               source_refs=['visit:' + U2], metric_refs=['visits'])]
                                         if s.id == 'overview' else []))
        for s in default_scheme().sections])


def result_data():
    return dict(status='generated', provider='direct', snapshot_id=U1, snapshot_hash='a' * 64,
                scheme_id=U3, scheme_version=1, coverage_status='complete', content=content_data(),
                trace=dict(prompt_version='work_report.v1', elapsed_ms=0, chunks_total=1, chunks_completed=1))


@pytest.mark.parametrize('kind,anchor,start,end', [
    ('day', '2024-02-29', '2024-02-29', '2024-03-01'),
    ('week', '2025-01-01', '2024-12-30', '2025-01-06'),
    ('two_weeks', '2025-01-01', '2024-12-23', '2025-01-06'),
    ('month', '2024-02-29', '2024-02-01', '2024-03-01'),
    ('month', '2025-12-31', '2025-12-01', '2026-01-01'),
    ('quarter', '2025-12-31', '2025-10-01', '2026-01-01'),
    ('quarter', '2024-02-29', '2024-01-01', '2024-04-01'),
    ('week', '2026-10-04', '2026-09-28', '2026-10-05'),
])
def test_past_periods_are_shanghai_half_open(kind, anchor, start, end):
    period = resolve_period(PeriodSelection(kind=kind, anchor_date=anchor), now=NOW)
    assert period.start.isoformat() == start + 'T00:00:00+08:00'
    assert period.end_exclusive.isoformat() == end + 'T00:00:00+08:00'
    assert period.cutoff == period.end_exclusive


def test_current_period_cutoff_and_shanghai_today():
    now = datetime.fromisoformat('2026-10-08T17:00:00+00:00')
    period = resolve_period(PeriodSelection(kind='day'), now=now)
    assert period.start.isoformat() == '2026-10-09T00:00:00+08:00'
    assert period.cutoff == now
    both = resolve_period(PeriodSelection(kind='two_weeks'), now=NOW)
    assert both.start.isoformat().startswith('2026-09-28')
    assert both.end_exclusive.isoformat().startswith('2026-10-12')


def test_future_and_naive_now_rejected():
    with pytest.raises(WorkReportError, match='未来'):
        resolve_period(PeriodSelection(anchor_date='2026-10-10'), now=NOW)
    with pytest.raises(ValueError, match='时区'):
        resolve_period(PeriodSelection(), now=NOW.replace(tzinfo=None))


@pytest.mark.parametrize('change', [
    {'total': None}, {'included': 0}, {'truncated': True}, {'statistics_complete': False},
    {'missing_reasons': ['缺正文']}, {'selected': False},
])
def test_complete_cannot_hide_missing_material(change):
    coverage = request_data()['coverage'][0] | change
    with pytest.raises(ValidationError):
        SourceCoverage(**coverage)


def test_partial_unknown_is_not_zero_and_failed_is_not_provider_failure():
    data = request_data()
    data['coverage'][0].update(status='partial', total=None, statistics_complete=False, missing_reasons=['未知总量'])
    assert GenerationInput(**data).coverage[0].total is None
    data['coverage'][0]['status'] = 'failed'
    with pytest.raises(ValidationError, match='读取失败'):
        GenerationInput(**data)


@pytest.mark.parametrize('change', [
    {'source_id': 'task:' + U2}, {'truncated': True}, {'record_id': U3, 'source_id': 'visit:' + U3},
])
def test_snapshot_source_references_and_coverage_are_consistent(change):
    data = request_data()
    if 'record_id' in change:
        data['sources'].append(data['sources'][0] | change)
    else:
        data['sources'][0].update(change)
    with pytest.raises(ValidationError):
        GenerationInput(**data)


def test_user_only_snapshot_does_not_invent_statistics():
    data = request_data()
    data.update(sources=[], metrics=[])
    data['coverage'][0].update(selected=False, status='not_selected', total=None, included=0, statistics_complete=False)
    assert GenerationInput(**data).supplements[0].declared_kind == 'plan'
    assert SnapshotSelection(sources=[], supplements=[dict(id=U3, text='个人补充')]).sources == []


@pytest.mark.parametrize('text,refs', [
    ('{{metric:visits}}', []), ('没有统计', ['visits']), ('{{metric:Visits}}', []),
    ('{{metric:visits', []), ('{{metric:visits}}', ['visits', 'visits']),
])
def test_metric_token_is_explicit_and_exact(text, refs):
    with pytest.raises(ValidationError):
        ReportItem(kind='summary', text=text, metric_refs=refs)


@pytest.mark.parametrize('value', ['NaN', '1e2', '1.5', '-1', 2, True])
def test_counts_are_exact_nonnegative_integer_strings(value):
    metric = request_data()['metrics'][0] | {'value': value}
    with pytest.raises(ValidationError):
        TrustedMetric(**metric)


@pytest.mark.parametrize('change', [
    {'snapshot_hash': 'b' * 64}, {'scheme_version': 2}, {'coverage_status': 'partial'},
])
def test_result_cannot_substitute_trusted_context(change):
    with pytest.raises(WorkReportError):
        validate_generation_result(GenerationResult(**(result_data() | change)), GenerationInput(**request_data()))


def test_generated_requires_real_route_and_completed_nonempty_batches():
    data = result_data()
    data['trace'].update(chunks_total=0, chunks_completed=0)
    with pytest.raises(ValidationError):
        GenerationResult(**data)
    data.update(status='insufficient_data', provider='none', content=None)
    assert GenerationResult(**data).content is None


def test_generated_sections_follow_frozen_order_and_titles():
    content = content_data()
    content['sections'].reverse()
    with pytest.raises(WorkReportError, match='章节'):
        validate_content(ReportContent(**content), GenerationInput(**request_data()), generated=True)


@pytest.mark.parametrize('item', [
    dict(kind='fact', text='虚构', source_refs=['visit:unknown']),
    dict(kind='fact', text='完成', source_refs=['user:' + U3]),
    dict(kind='user_supplement', text='冒充手工', source_refs=['visit:' + U2]),
    dict(kind='summary', text='无依据'),
])
def test_generator_cannot_erase_or_invent_references(item):
    content = content_data()
    content['sections'][0]['items'] = [item]
    with pytest.raises(WorkReportError):
        validate_content(ReportContent(**content), GenerationInput(**request_data()), generated=True)


def test_missing_data_is_preserved_and_unknown_metric_not_rendered_as_zero():
    data = request_data()
    data['coverage'][0].update(status='partial', total=None, missing_reasons=['缺失材料'])
    with pytest.raises(WorkReportError, match='缺口'):
        validate_content(ReportContent(**content_data()), GenerationInput(**data), generated=True)
    data['metrics'][0].update(value=None, complete=False, display_value='未登记')
    content = content_data() | {'missing_data': ['缺失材料']}
    with pytest.raises(WorkReportError, match='统计'):
        validate_content(ReportContent(**content), GenerationInput(**data), generated=True)


def test_rewrite_preserves_unselected_manual_sections_and_title():
    data = request_data()
    previous = content_data()
    previous['sections'][1]['items'] = [dict(kind='manual', text='人工编辑段落')]
    data['rewrite'] = dict(document_id=U2, base_revision_id=U3, base_document_version=4,
                           section_ids=['overview'], previous_content=previous)
    result = result_data()
    result['content']['sections'] = result['content']['sections'][:1]
    request = GenerationInput(**data)
    merged = merge_rewrite(GenerationResult(**result), request)
    assert merged.sections[1].items[0].text == '人工编辑段落'
    assert merged.title == previous['title']
    result['content']['title'] = '不允许修改标题'
    with pytest.raises(WorkReportError):
        merge_rewrite(GenerationResult(**result), request)


def test_rewrite_requires_all_optimistic_selectors():
    base = dict(snapshot_id=U1, snapshot_hash='a' * 64, scheme=dict(scheme_id=U3, version_no=1))
    with pytest.raises(ValidationError):
        RunCreate(**base, document_id=U2)
    assert RunCreate(**base, document_id=U2, version_no=4, base_revision_id=U3, section_ids=['overview'])


@pytest.mark.parametrize('confirm', [False, 1, 'true', None])
def test_share_requires_boolean_true(confirm):
    with pytest.raises(ValidationError):
        ShareRequest(version_no=1, confirm=confirm, audience={'kind': 'company'})


def test_reference_versions_and_optimistic_scheme_version_in_schema():
    scheme = SchemeUpdate(name='方案', config=default_scheme(), version_no=3)
    assert scheme.version_no == 3
    assert 'version_no' in SchemeUpdate.model_json_schema()['required']


def test_workspace_selector_is_explicit_without_fake_team():
    assert ScopeSelection(kind='workspace').team_id is None
    with pytest.raises(ValidationError):
        ScopeSelection(kind='workspace', team_id=U1)


def test_manual_plain_text_never_claims_machine_verified_numbers():
    item = ReportItem(kind='manual', text='人工修改为拜访2次')
    assert item.metric_refs == []
    with pytest.raises(ValidationError):
        ReportItem(kind='manual', text='{{metric:visits}}', metric_refs=['visits'])
    content = content_data()
    content['sections'][0]['items'] = [item.model_dump()]
    request = GenerationInput(**request_data())
    validate_content(ReportContent(**content), request, generated=False)
    with pytest.raises(WorkReportError, match='人工'):
        validate_content(ReportContent(**content), request, generated=True)


def test_hash_stable_for_objects_sensitive_to_array_order_and_render_trusted_metrics():
    assert canonical_hash({'a': 1, 'b': 2}) == canonical_hash({'b': 2, 'a': 1})
    assert canonical_hash(['a', 'b']) != canonical_hash(['b', 'a'])
    data = request_data()
    data['metrics'][0]['display_value'] = '1（已核定）'
    rendered = render_content(ReportContent(**content_data()), GenerationInput(**data))
    assert '1（已核定）' in rendered and '{{metric:' not in rendered


@pytest.mark.parametrize('bad', ['bad\x00text', '\ud800'])
def test_unsafe_unicode_rejected_before_db(bad):
    with pytest.raises(ValidationError):
        ReportItem(kind='summary', text=bad)
