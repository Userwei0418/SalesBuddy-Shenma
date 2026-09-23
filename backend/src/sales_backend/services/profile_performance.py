"""Quarterly targets and independent structure/follow-up windows for one authorized subject."""

from datetime import date, timedelta
from decimal import Decimal

from sales_backend.repositories.customer_assets import today
from sales_backend.repositories.profile_customers import subject_customers
from sales_backend.repositories.profile_performance import ProfilePerformanceRepository
from sales_backend.services.profile_scores import performance_scores


def ratio_summary(numerator, denominator, evaluated):
    return {
        'numerator': numerator, 'denominator': denominator, 'evaluated_count': evaluated,
        'pending_count': denominator-evaluated,
        'coverage': Decimal(evaluated)*100/denominator if denominator else None,
        'rate': Decimal(numerator)*100/denominator if denominator and evaluated else None,
        'status': 'empty' if not denominator else 'pending' if not evaluated else
                  'partial' if evaluated < denominator else 'ready',
    }


def quarter_period(year, quarter):
    start = date(year, (quarter-1)*3+1, 1)
    end = date(year+1, 1, 1) if quarter == 4 else date(year, quarter*3+1, 1)
    return {'type': 'quarter', 'year': year, 'quarter': quarter, 'start': start, 'end': end-timedelta(days=1)}


async def performance(connection, actor, *, scope='self', account_code=None, team=None, member_id=None,
                      team_id=None, year=None, quarter=None, period='week', structure_period='current'):
    as_of = today()
    year = year or as_of.year
    quarter = quarter or (as_of.month-1)//3+1
    target_period = quarter_period(year, quarter)
    repo = ProfilePerformanceRepository()
    selected = await repo.scope(connection, actor, scope=scope, account_code=account_code, team=team,
                                member_id=member_id, team_id=team_id)
    selected['editable'] = selected['editable'] and target_period['start'] <= as_of <= target_period['end']
    members = await repo.members(connection, actor, selected)
    # Efficiency windows stay independent of the maturity quarter/year selector.
    visit_start = (as_of-timedelta(days=as_of.weekday()) if period == 'week' else
                   date(as_of.year, (as_of.month-1)//3*3+1, 1) if period == 'quarter' else
                   date(1900, 1, 1) if period == 'all' else date(as_of.year, 1, 1))
    structure_start = date(as_of.year, 1, 1) if structure_period == 'year' else None
    # Actual/retention dates use the selected year; follow-up always uses its own window.
    values = await repo.facts(connection, members, year, as_of, visit_start, selected,
                              target_start=target_period['start'], target_end=target_period['end'],
                              structure_start=structure_start, structure_end=as_of)
    customers = await subject_customers(connection, member_ids=members, scope=selected['scope'],
                                        team_ids=selected['team_ids'], structure_start=structure_start,
                                        structure_end=as_of)
    evaluated = [row for row in customers if row['quadrant_code'] in
                 {'main_attack', 'customer_asset', 'customer_resource', 'order_driven'}]
    customer_ratio = ratio_summary(sum(row['quadrant_code'] in {'main_attack', 'customer_asset'}
                                      for row in evaluated), len(customers), len(evaluated))
    opportunity_ratio = ratio_summary(values.pop('opportunity_ab_count'), values.pop('opportunity_count'),
                                     values.pop('opportunity_evaluated_count'))
    previous, current, count = (values.pop(key) for key in
                                ('retention_previous', 'retention_current', 'retention_customers'))
    followup = {'count': values.pop('followup_count'),
                'customer_count': values.pop('followup_customer_count'),
                'average_score': values['followup_score'],
                'evaluated_count': values.pop('followup_evaluated_count')}
    result = dict(
        data_source='database', year=year, quarter=quarter, as_of=as_of,
        scope=selected['scope'], editable=selected['editable'],
        selected_subject={'scope': selected['scope'], 'member_id': selected['user_id'],
                          'team_id': selected['team_id'], 'label': selected['label'], 'editable': selected['editable']},
        target_period=target_period,
        targets=await repo.targets(connection, actor, selected, target_period['start']),
        actuals={k: values.pop(k) for k in ('collection', 'recognized')},
        retention=dict(rate=current/previous*100 if previous else None,
                       current=current, previous=previous, customer_count=count),
        supplementals=dict(followup=values.pop('followup_score'), customers=customer_ratio['rate'],
                           opportunities=opportunity_ratio['rate']),
        efficiency={'customers': customer_ratio, 'opportunities': opportunity_ratio, 'followup': followup},
        windows={'structure': {'period': structure_period, 'start': structure_start, 'end': as_of},
                 'followup': {'period': period, 'start': visit_start, 'end': as_of}},
        **values,
    )
    result['provenance'] = {
        'scope': selected, 'year': year, 'as_of': as_of, 'period': period, 'followup_start': visit_start,
        'target_period': target_period, 'windows': result['windows'],
        'efficiency_formula': 'subject_quadrant_1_2_and_opportunity_ab_share_v2',
    }
    return await performance_scores(connection, result)


async def save_target(connection, actor, body):
    raise ValueError('目标已改为季度表单，请更新页面后提交本人季度目标；已有目标变更需填写原因并经运营审批')
