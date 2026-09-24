#!/usr/bin/env python3
"""Validate weekly.v2 input and observed output. Does not call the model.

Structural/reference checks are deterministic; factual prose still requires
review against records and entity snapshots. Never use this as authorization.
"""
import argparse
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from .decode_response import decode_response
from .schema_check import schema_errors

ROOT = Path(__file__).resolve().parent
SHANGHAI = ZoneInfo('Asia/Shanghai')


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('Timezone required')
    return parsed


def valid_period(period):
    if not isinstance(period, dict) or set(period) != {'start_date', 'end_date', 'timezone', 'date_basis'}:
        return False
    if period['timezone'] != 'Asia/Shanghai' or period['date_basis'] != 'created_at':
        return False
    try:
        for key in ('start_date', 'end_date'):
            if not isinstance(period[key], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', period[key]):
                return False
        return (date.fromisoformat(period['end_date']) - date.fromisoformat(period['start_date'])).days == 13
    except (ValueError, TypeError):
        return False


def validate_input(source):
    schema = json.loads((ROOT / 'input.schema.json').read_text())
    errors = schema_errors(source, schema)
    if errors:
        return errors
    period = source['period']
    if not valid_period(period):
        errors.append('period: expected exactly 14 inclusive Shanghai dates by created_at')
    current = timestamp(source['current_time'])
    if date.fromisoformat(period['end_date']) > current.astimezone(SHANGHAI).date():
        errors.append('period: end_date is in the future')
    as_of = timestamp(source['context']['as_of'])
    if as_of > current:
        errors.append('context.as_of: in the future')
    records = source['records']
    record_ids, customer_ids, opportunity_customers = set(), set(), {}
    for row in records:
        rid = row['id']
        if rid in record_ids:
            errors.append('records: duplicate ID ' + rid)
        record_ids.add(rid)
        customer_ids.add(row['customer_id'])
        if row['opportunity_id']:
            old = opportunity_customers.setdefault(row['opportunity_id'], row['customer_id'])
            if old != row['customer_id']:
                errors.append('records: opportunity crosses customer')
        if row['recorder_id'] != source['author']['id']:
            errors.append('records.' + rid + ': recorder does not match author')
        uploaded = timestamp(row['created_at'])
        local_date = uploaded.astimezone(SHANGHAI).date().isoformat()
        if not period['start_date'] <= local_date <= period['end_date'] or uploaded > current:
            errors.append('records.' + rid + ': upload time outside period/current_time')
        if row.get('is_summary') or row.get('content_truncated'):
            errors.append('records.' + rid + ': incomplete source')
        reference = row['source_ref']
        if reference['id'] != rid or reference['version_no'] != row['version_no']:
            errors.append('records.' + rid + ': source identity/version mismatch')
    for table, kind in [('customers', 'customer'), ('opportunities', 'opportunity')]:
        seen = set()
        for row in source['context'][table]:
            ident = row['id']
            if ident in seen:
                errors.append(table + ': duplicate ID ' + ident)
            seen.add(ident)
            if row['source_ref']['id'] != ident:
                errors.append(table + '.' + ident + ': source identity mismatch')
            if row['updated_at'] and timestamp(row['updated_at']) > as_of:
                errors.append(table + '.' + ident + ': updated after snapshot')
            if kind == 'customer' and ident not in customer_ids:
                errors.append(table + '.' + ident + ': unrelated entity')
            if kind == 'opportunity':
                if ident not in opportunity_customers or opportunity_customers.get(ident) != row['customer_id']:
                    errors.append(table + '.' + ident + ': unrelated/mismatched customer')
                if row['amount'] is not None and not (row['currency'] or '').strip():
                    errors.append(table + '.' + ident + ': amount lacks currency')
    return errors


def expected_statistics(source):
    raw = source.get('statistics')
    raw = raw if isinstance(raw, dict) else {}
    return {key: raw.get(key) if type(raw.get(key)) is int and raw[key] >= 0 else None
            for key in ('record_count', 'customer_count', 'opportunity_count')}


def validate(source, reply, expected_status=None):
    errors = schema_errors(reply, json.loads((ROOT / 'output.schema.json').read_text()))
    if errors:
        return errors
    def need(condition, message):
        if not condition:
            errors.append(message)
    input_errors = validate_input(source)
    if expected_status:
        need(reply['status'] == expected_status, 'Unexpected result status')
    if input_errors:
        need(reply['status'] == 'invalid_input', 'Invalid input accepted: ' + '; '.join(input_errors))
    else:
        need(reply['status'] != 'invalid_input', 'Valid input rejected')
        if not source['records']:
            need(reply['status'] == 'insufficient_data', 'Empty input treated as a report')
    expected_period = source.get('period') if valid_period(source.get('period')) else None
    need(reply['period'] == expected_period, 'Period changed or invalid period preserved')
    need(reply['statistics'] == expected_statistics(source), 'Statistics changed/invented')
    rows = source.get('records', []) if isinstance(source, dict) else []
    rows = rows if isinstance(rows, list) else []
    records = {r['id']: r for r in rows if isinstance(r, dict) and isinstance(r.get('id'), str)}
    context = source.get('context', {})
    context = context if isinstance(context, dict) else {}
    entities = {}
    for table, kind in [('customers', 'customer'), ('opportunities', 'opportunity')]:
        for row in context.get(table, []) if isinstance(context.get(table), list) else []:
            if isinstance(row, dict) and isinstance(row.get('id'), str):
                entities[(kind, row['id'])] = row
    def verify_acv(text, entity, required=False):
        matches = re.findall(r'ACV[：: ]+([0-9,]+(?:\.[0-9]+)?)\s*(万)?', text, re.I)
        if required:
            need(bool(matches), 'Nonempty opportunity amount missing from report')
        for numeric, ten_thousand in matches:
            amount = entity.get('amount')
            need(amount is not None, 'Null ACV displayed as a number')
            if amount is None:
                continue
            currency = (entity.get('currency') or '').upper()
            value = Decimal(numeric.replace(',', ''))
            if currency in ('CNY', 'RMB'):
                value *= 10000 if ten_thousand else 1
            else:
                need(not ten_thousand, 'Foreign currency must retain base-unit number')
            need(value == Decimal(str(amount)), 'ACV amount changed or rounded')
    if reply['status'] == 'ready':
        opps = context.get('opportunities', [])
        for opportunity in opps:
            name = opportunity.get('name')
            if not name or sum(o.get('name') == name for o in opps) != 1:
                continue
            block = re.search(r'^###\s+' + re.escape(name) + r'\s*\n(.*?)(?=^#{2,3}\s|\Z)',
                              reply['body_markdown'], re.M | re.S)
            if block:
                verify_acv(block.group(1), opportunity, opportunity.get('amount') is not None)
                displayed = opportunity.get('amount') is not None or any(
                    opportunity.get(key) and str(opportunity[key]) in block.group(1)
                    for key in ('stage_label', 'expected_close_date', 'product_line', 'partner_name'))
                if displayed:
                    need(any(section['key'] == 'context' and any(
                        item['opportunity_id'] == opportunity['id'] and
                        opportunity['source_ref'] in item['entity_refs'] for item in section['items'])
                        for section in reply['sections']), 'Displayed opportunity information lacks opportunity context evidence')
    seen = set()
    for section in reply['sections']:
        kind = section['key']
        need(kind not in seen, 'Duplicate section key')
        seen.add(kind)
        for item in section['items']:
            refs, erefs = item['source_ids'], item['entity_refs']
            need(bool(refs or erefs), 'Fact lacks any evidence')
            need(all(rid in records for rid in refs), 'Unknown visit source')
            cid, oid = item['customer_id'], item['opportunity_id']
            for rid in refs:
                if rid not in records:
                    continue
                row = records[rid]
                need(row.get('customer_id') == cid and row.get('opportunity_id') == oid,
                     'Visit reference crosses customer/opportunity')
            for ref in erefs:
                entity = entities.get((ref['type'], ref['id']))
                need(entity is not None, 'Unknown entity source')
                if entity is None:
                    continue
                need(entity.get('source_ref') == ref, 'Entity snapshot/version was altered')
                expected_cid = entity['id'] if ref['type'] == 'customer' else entity.get('customer_id')
                need(cid == expected_cid, 'Entity reference crosses customer')
                if ref['type'] == 'opportunity':
                    need(oid == entity['id'], 'Entity reference crosses opportunity')
            if kind == 'context':
                need(bool(erefs) and not refs, 'Context must use entity evidence only')
                need(not re.search(r'(?:ACV|阶段|预计关单)[：: ]*(?:未提供|待填写|未知|暂无)', item['text']),
                     'Context contains missing-field placeholders')
                if erefs and all(ref['type'] == 'customer' for ref in erefs):
                    need(oid is None, 'Customer-only context must not name an opportunity')
                for ref in erefs:
                    if ref['type'] == 'opportunity' and (ref['type'], ref['id']) in entities:
                        verify_acv(item['text'], entities[(ref['type'], ref['id'])])
            if kind == 'progress':
                need(bool(refs), 'Progress lacks visit evidence')
            if kind == 'next_actions' and not refs:
                need(any(ref['type'] == 'opportunity' and
                         (entities.get((ref['type'], ref['id']), {}).get('follow_up_plan') or '').strip()
                         for ref in erefs), 'Entity-only action lacks a stored opportunity plan')
            need(not re.search(r'\[\d+\]', item['text']), 'Visible reference in item text')
    if reply['status'] == 'ready':
        for customer in context.get('customers', []):
            name = customer.get('name')
            if not name:
                continue
            block = re.search(r'^##\s+' + re.escape(name) + r'\s*\n(.*?)(?=^##\s|\Z)',
                              reply['body_markdown'], re.M | re.S)
            if not block:
                continue
            displayed = any(customer.get(key) and customer[key] in block.group(1)
                            for key in ('industry', 'customer_type', 'demand_summary'))
            if displayed:
                need(any(section['key'] == 'context' and any(
                    item['customer_id'] == customer['id'] and item['opportunity_id'] is None and
                    customer['source_ref'] in item['entity_refs'] for item in section['items'])
                    for section in reply['sections']), 'Displayed customer information lacks customer context evidence')
    for warning in reply['warnings']:
        need(all(rid in records for rid in warning['source_ids']), 'Unknown warning visit source')
    if reply['status'] != 'ready':
        need(reply['sections'] == [], 'Non-ready output contains sections')
    if reply['status'] == 'invalid_input':
        need(reply['body_markdown'] == '' and bool(reply['warnings']), 'Invalid input requires empty body and a warning')
    if reply['status'] == 'ready':
        body = reply['body_markdown']
        need(bool(body.strip()) and bool(reply['sections']), 'Ready report is empty')
        need(bool(re.search(r'^##\s+\S', body, re.M)), 'Missing customer grouping')
        need(not re.search(r'\[\d+\]|本周期上传记录|覆盖\s*\d+\s*个?客户', body), 'Visible citation/statistics overview')
        need(not re.search(r'^#{1,6}\s*(来源|参考资料|进展|后续行动|概览)\s*$', body, re.M),
             'Global action/source/overview heading')
        for ident in records:
            need(not re.search(r'(?<![\w-])' + re.escape(ident) + r'(?![\w-])', body), 'Visit ID displayed in report')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('output', nargs='?')
    parser.add_argument('--expected-status', choices=['ready', 'insufficient_data', 'invalid_input'])
    args = parser.parse_args()
    try:
        source = decode_response(Path(args.input).read_text())
        errors = validate(source, decode_response(Path(args.output).read_text()), args.expected_status) if args.output else validate_input(source)
    except (ValueError, OSError) as exc:
        errors = [str(exc)]
    print(json.dumps({'passed': not errors, 'errors': errors,
                      'boundary': 'structure, date window and reference integrity; semantic prose and real authorization require separate verification'},
                     ensure_ascii=False, indent=2))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
