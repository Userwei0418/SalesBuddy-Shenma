"""Public sales growth projections; frozen inputs and evidence never leave here.

This module does not calculate a historical score with today's rules. A group is
a current projection of comparable completed reviews, never a fabricated review.
"""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sales_backend.db import json_value
from sales_backend.domain.profile_scores import CompetencyWeights, number

CONTRACT_VERSION = 'sales.growth.v2'
CALCULATION = 'sales_competency_frozen_complete_v2'
CODES = tuple(k for k in CompetencyWeights.model_fields if k != 'schema_version')
STATES = ('assessed', 'partially_assessed', 'insufficient_data', 'legacy_unknown')
SELECTION = 'interaction_at_desc_created_at_desc_id_desc'


def _object(value):
    value = json_value(value)
    return value if isinstance(value, dict) else {}


def _scalar(value):
    # Nested containers in a public scalar field must not smuggle raw input.
    if isinstance(value, UUID):
        return str(value)
    return value if value is None or isinstance(value, (str, int, float, Decimal, date, datetime)) else None


def _pick(value, keys):
    value = _object(value)
    return {key: _scalar(value.get(key)) for key in keys}


def _texts(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _policy(value):
    value = _object(value)
    if not value:
        return None
    return {**_pick(value, ('id', 'code', 'version', 'source')),
            'definition': _pick(value.get('definition'), ('schema_version', *CODES))}


def _framework(value):
    value = _object(value)
    if not value:
        return None
    rules = _object(value.get('scoring_rules'))
    return {**_pick(value, ('version_no', 'display_name')),
            'dimensions': [_pick(d, ('code', 'name', 'short_name', 'weight'))
                           for d in _objects(value.get('dimensions')) if d.get('code') in CODES],
            'scoring_rules': {**{key: _scalar(rules[key]) for key in
                                ('minimum_visit_count', 'evidence_required', 'review_window_days') if key in rules},
                              **({'score_range': [_scalar(v) for v in rules['score_range']]}
                                 if isinstance(rules.get('score_range'), list) else {})}}


def _basis(value, *, legacy_version=None):
    value = _object(value)
    if value.get('snapshot_status') != 'frozen':
        return dict(basis_id=None, framework_version=_scalar(legacy_version), framework_hash=None,
                    framework_snapshot=None, score_policy=None, calculation_version=None,
                    snapshot_status='legacy_unknown')
    return {**_pick(value, ('basis_id', 'framework_version', 'framework_hash', 'calculation_version')),
            'framework_snapshot': _framework(value.get('framework_snapshot')),
            'score_policy': _policy(value.get('score_policy')), 'snapshot_status': 'frozen'}


def _summary(value):
    value = _object(value)
    if not value:
        return None
    return {**_pick(value, ('value', 'text', 'count', 'total', 'coverage_percent', 'explanation', 'calculation')),
            'rule': _policy(value.get('rule')),
            'inputs': [_pick(item, ('code', 'name', 'raw_value', 'value', 'weight'))
                       for item in _objects(value.get('inputs')) if item.get('code') in CODES]}


def public_review(row):
    """One whitelist for latest/history/detail; never return input_snapshot itself."""
    row = dict(row)
    snapshot = _object(row.get('input_snapshot'))
    basis = _basis(snapshot.get('score_basis'),
                   legacy_version=row.get('framework_version', snapshot.get('framework_version')))
    state = row.get('evaluation_state')
    frozen = (snapshot.get('contract_version') == 'sales.competency.v2'
              and basis['snapshot_status'] == 'frozen' and basis['calculation_version'] == CALCULATION
              and isinstance(basis['basis_id'], str) and len(basis['basis_id']) == 64
              and basis['framework_snapshot'] is not None and basis['score_policy'] is not None
              and state in STATES[:-1])
    if not frozen:
        basis = _basis({}, legacy_version=basis.get('framework_version'))
        state = 'legacy_unknown'
    dimensions = {}
    for code, item in _object(row.get('dimension_scores')).items():
        if code not in CODES or not isinstance(item, dict):
            continue
        dimensions[code] = _pick(item, ('name', 'short_name', 'score', 'weight', 'assessment', 'coaching_action'))
        dimension_state = item.get('evaluation_state')
        dimensions[code]['evaluation_state'] = (dimension_state if frozen and dimension_state in
                                                ('assessed', 'insufficient_data') else 'legacy_unknown')
    coverage = _object(snapshot.get('evidence_coverage'))
    if frozen and coverage.get('status') == 'known':
        coverage = _pick(coverage, ('status', 'window_start', 'window_end_exclusive', 'business_timezone',
                                   'total_eligible_visits', 'used_visits', 'limit', 'truncated', 'selection'))
    else:
        coverage = dict(status='legacy_unknown', window_start=_scalar(snapshot.get('window_start')),
                        window_end_exclusive=_scalar(snapshot.get('window_end_exclusive')),
                        business_timezone=_scalar(snapshot.get('business_timezone')),
                        total_eligible_visits=None, used_visits=_scalar(snapshot.get('visit_count')),
                        limit=80, truncated=None, selection=SELECTION)
    return {**_pick(row, ('id', 'review_date', 'reviewed_at', 'generation_no', 'overall_score', 'summary')),
            'evaluation_state': state, 'dimension_scores': dimensions,
            'strengths': _texts(json_value(row.get('strengths'))),
            'improvements': _texts(json_value(row.get('improvements'))), 'score_basis': basis,
            'score_summary': _summary(snapshot.get('score_summary')) if frozen else None,
            'evidence_coverage': coverage,
            'input_fingerprint': _scalar(row.get('input_fingerprint') or snapshot.get('input_fingerprint')) if frozen else None,
            'data_as_of': _scalar(snapshot.get('data_as_of'))}


def current_review(row):
    return _pick(row, ('id', 'status', 'generation_no', 'trigger_type', 'requested_at')) if row else None


def member_coverage(reviews, eligible_count):
    counts = {state: sum(r['evaluation_state'] == state for r in reviews) for state in STATES}
    return dict(reviewed_members=len(reviews), eligible_members=eligible_count,
                assessed_members=counts['assessed'], partially_assessed_members=counts['partially_assessed'],
                unassessed_members=eligible_count - len(reviews) + counts['insufficient_data'],
                legacy_unknown_members=counts['legacy_unknown'])


def group_projection(rows, eligible_count):
    """Only same-basis, explicitly assessed new dimensions enter a group mean."""
    reviews = [public_review(row) for row in rows]
    coverage = member_coverage(reviews, eligible_count)
    bases = {r['score_basis']['basis_id'] for r in reviews}
    comparison = ('no_data' if not reviews else 'legacy_unknown' if coverage['legacy_unknown_members']
                  or None in bases else 'same_basis' if len(bases) == 1 else 'mixed_basis')
    result = dict(contract_version=CONTRACT_VERSION, assessment_kind='sales_competency', data_source='database',
                  projection='current_group', history=[], current_review=None, can_review=False,
                  today_status='succeeded' if reviews else 'missing', coverage=coverage,
                  comparison_status=comparison, comparison_basis_id=next(iter(bases)) if comparison == 'same_basis' else None,
                  latest=None, framework=None, score_policy=None)
    if comparison != 'same_basis':
        result['reason'] = {'no_data': '当前范围尚无已完成评估', 'legacy_unknown': '包含规则依据无法还原的历史评估，仅展示人数',
                            'mixed_basis': '成员评估采用不同规则，仅展示人数'}[comparison]
        return result
    basis = reviews[0]['score_basis']
    dimensions = {}
    for definition in (basis['framework_snapshot'] or {}).get('dimensions', []):
        code = definition['code']
        values = [number(r['dimension_scores'].get(code, {}).get('score')) for r in reviews
                  if r['dimension_scores'].get(code, {}).get('evaluation_state') == 'assessed']
        values = [v for v in values if v is not None and 0 <= v <= 100]
        dimensions[code] = {**deepcopy(definition), 'score': sum(values) / len(values) if values else None,
                            'sample_count': len(values), 'evaluation_state': 'assessed' if values else 'insufficient_data',
                            'assessment': '相同规则下已评估成员的最新评分均值',
                            'coaching_action': '切换到个人查看具体证据和训练建议'}
    result.update(framework=deepcopy(basis['framework_snapshot']), score_policy=deepcopy(basis['score_policy']))
    result['latest'] = dict(dimension_scores=dimensions, score_basis=deepcopy(basis), overall_score=None,
                            score_summary=None, review_date=max(r['review_date'] for r in reviews),
                            summary=f'{eligible_count}位适用成员中，{len(reviews)}位有已完成评估；仅汇总同规则已评估维度。',
                            strengths=[], improvements=[])
    return result
