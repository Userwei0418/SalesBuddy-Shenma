"""Versioned server-owned schemes; never impersonate saved user schemes."""
from uuid import UUID, uuid5

from sales_backend.contracts.work_report import SchemeSnapshot
from sales_backend.domain.work_report import WorkReportError, canonical_hash, default_scheme

_NAMESPACE = UUID('c70b7696-b88c-43da-85e3-8f052ae46a77')
_NAMES = {'day': '日报', 'two_weeks': '双周报告', 'month': '月报', 'quarter': '季度报告', 'week': '周报'}


def builtin_schemes():
    result = []
    for period, name in _NAMES.items():
        config = default_scheme().model_copy(update={'default_period': period})
        result.append(SchemeSnapshot(scheme_id=uuid5(_NAMESPACE, f'general.{period}.v1'),
            version_no=1, name=f'默认{name}', config=config, config_hash=canonical_hash(config)))
    config = default_scheme(sales_week=True)
    result.append(SchemeSnapshot(scheme_id=uuid5(_NAMESPACE, 'sales_week_six.week.v1'),
        version_no=1, name='默认销售六节周报', config=config, config_hash=canonical_hash(config)))
    return result


def builtin_scheme(reference):
    value = next((s for s in builtin_schemes() if s.scheme_id == reference.scheme_id), None)
    if value is not None and reference.version_no != value.version_no:
        raise WorkReportError('WORK_REPORT_VERSION_CONFLICT', '默认方案版本已变化')
    return value


def check_builtin_period(reference, period, config, *, facts_contract=None):
    builtin = builtin_scheme(reference)
    if builtin is None:
        return
    if period.kind != builtin.config.default_period or config.default_period != period.kind:
        raise WorkReportError('WORK_REPORT_INVALID_INPUT', '默认方案与报告周期不一致', status=422)
    if config.facts_contract != builtin.config.facts_contract or facts_contract != builtin.config.facts_contract:
        raise WorkReportError('WORK_REPORT_INVALID_INPUT', '默认方案与报告事实版本不一致', status=422)
