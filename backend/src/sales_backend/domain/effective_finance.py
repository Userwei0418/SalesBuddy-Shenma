"""Format the DB's current projection without making another source choice."""
from collections import defaultdict
from datetime import date
from decimal import Decimal


def effective_financial_overview(raw):
    from sales_backend.domain.opportunity_finance import KINDS, _history_amount, decimal_text, source_fingerprint

    groups, confirmations, entries, history = (defaultdict(list) for _ in range(4))
    def key(row): return row['year'], row['quarter'], row['kind']
    for row in raw['finance_effective']: groups[key(row)].append(row)
    for row in raw.get('finance_confirmations', []): confirmations[key(row)].append(row)
    for row in raw.get('finance_history', []): history[key(row)].append(row)
    for row in raw.get('finance_actuals', []):
        day = date.fromisoformat(str(row['occurred_on'])[:10])
        entries[(day.year, (day.month-1)//3+1, row['kind'])].append(row)
    decisions = {key(row): row for row in raw.get('finance_decisions', [])}
    keys = set(groups) | set(decisions) | set(entries) | set(history)
    if raw.get('requested_period'):
        year, quarter = raw['requested_period']
        keys |= {(year, quarter, kind) for kind in KINDS}
    def subtotal(rows, field):
        values = [Decimal(str(r[field])) for r in rows if r.get(field) is not None]
        return decimal_text(sum(values, Decimal(0))) if values else None
    periods = []
    for bucket in sorted(keys):
        year, quarter, kind = bucket
        facts, decision = groups[bucket], decisions.get(bucket)
        first = facts[0] if facts else {}
        known = subtotal(facts, 'amount')
        complete = bool(facts) and all(r['complete'] for r in facts)
        bases = {r['tax_basis'] for r in facts if r.get('raw_amount') is not None}
        basis = next(iter(bases)) if len(bases)==1 else 'mixed' if bases else None
        old_entries, old_history = entries[bucket], history[bucket]
        periods.append(dict(
            year=year, quarter=quarter, kind=kind, amount=known if complete else None,
            confirmed_subtotal=known, known_exclusive_subtotal=known if kind=='recognized' else None,
            source=first.get('source'), status=first.get('status', 'missing'),
            source_fingerprint=first.get('source_fingerprint') or source_fingerprint(
                raw['workspace_id'],raw['id'],year,quarter,kind,old_entries,old_history),
            entry_amount=subtotal([r for r in old_entries if not r.get('voided_at')], 'amount'), entries=old_entries,
            history=[{**r,'amount_yuan':decimal_text(_history_amount(r))} for r in old_history], decision=decision,
            tax_basis=basis, tax_unknown=any(r.get('tax_unresolved',False) for r in facts),
            confirmations=confirmations[bucket], expected_material_line_ids=(decision or {}).get('expected_material_line_ids') or [],
            expected_material_count=first.get('expected_material_count',0), confirmed_material_count=first.get('confirmed_material_count',0),
            missing_material_count=first.get('missing_material_count',0), empty_confirmation=bool((decision or {}).get('empty_confirmation')),
            complete=complete,reference_changed=False if first.get('reference_changed') is None else first['reference_changed'],
            version_no=first.get('version_no',(decision or {}).get('version_no',0)),period_precision='quarter',occurred_on=None,
        ))
    totals = {}
    for kind in KINDS:
        selected = [r for r in periods if r['kind']==kind]
        known = subtotal(selected,'confirmed_subtotal')
        complete = bool(selected) and all(r['complete'] for r in selected)
        totals[kind] = dict(amount=known if complete else None,confirmed_subtotal=known,
            conflict_period_count=sum(r['status'] in {'needs_confirmation','stale_confirmation'} for r in selected),
            resolved_period_count=sum(r['complete'] for r in selected),tax_unknown_period_count=sum(r['tax_unknown'] for r in selected),
            partial_period_count=sum(r['status']=='partial' for r in selected),
            missing_period_count=sum(r['status'] in {'missing','withdrawn'} for r in selected),complete=complete)
    notes = []
    for kind,label in (('recognized','确收'),('collection','回款')):
        value = totals[kind]
        state = ('登记完整' if value['complete'] else '部分登记' if value['confirmed_subtotal'] is not None
                 else '待核对' if value['conflict_period_count'] else '未登记')
        if value['tax_unknown_period_count']: state += '，采用来源税口径待核实'
        notes.append(label+state)
    return dict(schema_version=2,currency='CNY',opportunity_id=str(raw['id']),opportunity_version=raw.get('version_no',0),
        totals=totals,periods=periods,conflict_period_count=sum(r['conflict_period_count'] for r in totals.values()),
        quality='；'.join(notes),coverage_note='实际按唯一采用来源汇总；确收为不含税金额。季度记录不分配到日，部分登记仅展示小计；预测独立。')
