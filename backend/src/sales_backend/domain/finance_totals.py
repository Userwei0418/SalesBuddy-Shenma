"""Shared null/partial and date-precision semantics for already scoped facts."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sales_backend.domain.opportunity_finance import decimal_text


def actual_totals(rows):
    result = {}
    for kind in ('recognized', 'collection'):
        selected = [r for r in rows if r['kind'] == kind]
        periods = defaultdict(list)
        for row in selected:
            periods[(str(row.get('opportunity_id') or row['id']), row['year'], row['quarter'])].append(row)
        values = [Decimal(str(r['amount'])) for r in selected if r.get('amount') is not None]
        subtotal = decimal_text(sum(values, Decimal(0))) if values else None
        complete = bool(selected) and all(r['complete'] for r in selected)
        result.update({
            f'{kind}_amount': subtotal if complete else None,
            f'{kind}_confirmed_subtotal': subtotal,
            f'{kind}_complete': complete,
            f'{kind}_count': len(values),
            f'{kind}_period_count': len(periods),
            f'{kind}_incomplete_period_count': sum(not all(r['complete'] for r in facts) for facts in periods.values()),
            f'{kind}_missing_material_count': sum(max(r.get('missing_material_count', 0) for r in facts) for facts in periods.values()),
            f'{kind}_tax_unknown_period_count': sum(any(r.get('tax_unresolved', False) for r in facts) for facts in periods.values()),
            f'{kind}_conflict_period_count': sum(any(r.get('status') in {'needs_confirmation','stale_confirmation'} for r in facts) for facts in periods.values()),
        })
    result['complete'] = result['recognized_complete'] and result['collection_complete']
    result['conflict_period_count'] = result['recognized_conflict_period_count'] + result['collection_conflict_period_count']
    return result


def date_scoped_actuals(rows, start=None, end=None):
    """Only entire quarter intervals can enter an exact date-range total."""
    def day(value): return date.fromisoformat(str(value)[:10]) if value is not None else None
    start, end = day(start), day(end)
    included, unallocated = [], []
    for row in rows:
        exact = day(row.get('occurred_on'))
        if exact:
            if (start is None or exact >= start) and (end is None or exact <= end): included.append(row)
            continue
        first, last = day(row['period_start']), day(row['period_end'])
        if (start is not None and last < start) or (end is not None and first > end): continue
        if (start is None or first >= start) and (end is None or last <= end): included.append(row)
        else: unallocated.append(row)
    totals = actual_totals(unallocated)
    return included, dict(
        unallocated_period_count=len({(str(r.get('opportunity_id') or r['id']),r['year'],r['quarter'],r['kind']) for r in unallocated}),
        period_only_recognized_amount=totals['recognized_confirmed_subtotal'],
        period_only_collection_amount=totals['collection_confirmed_subtotal'],
        period_precision_warning='not_allocated_to_days' if unallocated else None,
    )
