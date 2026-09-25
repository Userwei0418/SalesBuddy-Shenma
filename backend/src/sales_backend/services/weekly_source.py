"""Build one immutable, exact-money weekly.v2 snapshot under REPEATABLE READ."""
import hashlib
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sales_backend.domain.business_options import business_options
from sales_backend.services.authorization import require_permission
from sales_backend.weekly_contract.validate_response import validate_input

SHANGHAI = ZoneInfo('Asia/Shanghai')


def exact_json(value):
    """Emit Decimal as a JSON number without a binary float round trip."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('WEEKLY_NONFINITE_NUMBER')
        return format(value, 'f')
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k, ensure_ascii=False) + ':' + exact_json(v)
                              for k, v in sorted(value.items())) + '}'
    if isinstance(value, (list, tuple)):
        return '[' + ','.join(exact_json(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def decode_snapshot(text):
    return json.loads(text, parse_float=Decimal)


def report_dates(now, report_week=None):
    today = now.astimezone(SHANGHAI).date()
    current_week = today - timedelta(days=today.weekday())
    week = report_week or current_week
    if not isinstance(week, date) or week.weekday() != 0 or week > current_week:
        raise ValueError('WEEKLY_INVALID_REPORT_WEEK')
    end_day = min(today, week + timedelta(days=6))
    cutoff = min(now, datetime.combine(end_day + timedelta(days=1), time.min, SHANGHAI) - timedelta(microseconds=1))
    return week, cutoff


def window(now, report_week=None):
    _, cutoff = report_dates(now, report_week)
    end = cutoff.astimezone(SHANGHAI).date()
    start = end - timedelta(days=13)
    return ({'start_date': start.isoformat(), 'end_date': end.isoformat(),
             'timezone': 'Asia/Shanghai', 'date_basis': 'created_at'},
            datetime.combine(start, time.min, SHANGHAI),
            datetime.combine(end + timedelta(days=1), time.min, SHANGHAI))


def reference(kind, row, generation):
    return {'type': kind, 'id': str(row['id']), 'version_no': row['version_no'],
            'snapshot_ref': f'weekly:{generation}:{kind}:{row["id"]}:v{row["version_no"]}'}


def iso(value):
    return value.isoformat() if value is not None else None


async def assert_snapshot_access(connection, actor, source):
    """Recheck current access before dispatch and before returning historical content."""
    await require_permission(connection, 'visit.read')
    for table, ids in (
        ('activity.visit', [r['id'] for r in source['records']]),
        ('crm.customer', [r['id'] for r in source['context']['customers']]),
        ('crm.opportunity', [r['id'] for r in source['context']['opportunities']]),
    ):
        if not ids:
            continue
        count = await connection.fetchval(
            f'SELECT count(*) FROM {table} WHERE workspace_id=$1::uuid AND id=ANY($2::uuid[]) AND deleted_at IS NULL',
            actor.workspace_id, ids,
        )
        if count != len(ids):
            raise PermissionError('WEEKLY_SOURCE_ACCESS_CHANGED')


async def build_snapshot(connection, actor, generation, report_week=None):
    for code in ('weekly_report.generate', 'weekly_report.read', 'visit.read', 'customer.read', 'opportunity.read'):
        await require_permission(connection, code)
    now = await connection.fetchval('SELECT transaction_timestamp()')
    _, cutoff = report_dates(now, report_week)
    period, start, end = window(now, report_week)
    rows = await connection.fetch('''SELECT id,customer_id,opportunity_id,recorder_user_ref_id,
        created_at,interaction_at,recorded_on,follow_up_record,next_action,status,version_no
        FROM activity.visit WHERE workspace_id=$1::uuid AND recorder_user_ref_id=$2::uuid
        AND deleted_at IS NULL AND status IN ('confirmed','archived')
        AND created_at >= $3 AND created_at < $4 AND created_at <= $5 ORDER BY created_at,id''',
        actor.workspace_id, actor.user_id, start, end, cutoff)
    total = await connection.fetchval('SELECT security.weekly_source_count($1,$2,$3)', start, end, cutoff)
    if total != len(rows):
        raise PermissionError('WEEKLY_SOURCE_ACCESS_INCOMPLETE')
    # weekly.v2 requires a customer and one unambiguous opportunity per record.
    # Do not invent a customer or silently drop extra historical associations.
    if any(r['customer_id'] is None for r in rows):
        raise ValueError('WEEKLY_SOURCE_SUBJECT_UNSUPPORTED')
    if rows and await connection.fetchval("""SELECT EXISTS(
        SELECT 1 FROM activity.visit_opportunity vo JOIN activity.visit v ON v.id=vo.visit_id
        WHERE v.id=ANY($1::uuid[]) AND vo.opportunity_id IS DISTINCT FROM v.opportunity_id)""",
        [r['id'] for r in rows]):
        raise ValueError('WEEKLY_SOURCE_ASSOCIATION_UNSUPPORTED')
    cids = sorted({str(r['customer_id']) for r in rows})
    oids = sorted({str(r['opportunity_id']) for r in rows if r['opportunity_id']})
    customers = await connection.fetch('''SELECT id,name,industry_code,customer_type_code,demand_summary,
        updated_at,version_no FROM crm.customer WHERE workspace_id=$1::uuid
        AND id=ANY($2::uuid[]) AND deleted_at IS NULL''', actor.workspace_id, cids)
    opportunities = await connection.fetch('''SELECT o.id,o.customer_id,o.name,o.amount,o.currency,o.stage_code,
        o.status,o.probability,o.expected_close_date,o.product_line,o.sales_channel,o.follow_up_plan,
        o.updated_at,o.version_no,p.name AS partner_name FROM crm.opportunity o
        LEFT JOIN crm.partner p ON p.id=o.partner_id AND p.workspace_id=o.workspace_id
        WHERE o.workspace_id=$1::uuid AND o.id=ANY($2::uuid[]) AND o.deleted_at IS NULL''', actor.workspace_id, oids)
    # Do not confuse RLS denial/deletion with an empty context and report success.
    if len(customers) != len(cids) or len(opportunities) != len(oids):
        raise PermissionError('WEEKLY_ENTITY_ACCESS_INCOMPLETE')
    cs = {str(c['id']): c for c in customers}
    os = {str(o['id']): o for o in opportunities}
    name = await connection.fetchval('SELECT display_name FROM platform.user_ref WHERE id=$1::uuid', actor.user_id)
    records = []
    for row in rows:
        cid = str(row['customer_id'])
        oid = str(row['opportunity_id']) if row['opportunity_id'] else None
        if oid and str(os[oid]['customer_id']) != cid:
            raise ValueError('WEEKLY_OPPORTUNITY_CUSTOMER_MISMATCH')
        visit_date = row['interaction_at'].astimezone(SHANGHAI).date() if row['interaction_at'] else row['recorded_on']
        records.append(dict(id=str(row['id']), recorder_id=str(row['recorder_user_ref_id']), recorder_name=name,
            customer_id=cid, customer_name=cs[cid]['name'], opportunity_id=oid,
            opportunity_name=os[oid]['name'] if oid else None, created_at=iso(row['created_at']),
            visit_date=iso(visit_date), follow_up_record=row['follow_up_record'] or '', next_action=row['next_action'],
            status=row['status'], version_no=row['version_no'], source_ref=reference('visit', row, generation)))
    stages = {s['code']: s['label'] for s in business_options()['opportunity']['stages']}
    source = dict(contract_version='weekly.v2', generation_id=generation, current_time=iso(now), period=period,
        author=dict(id=actor.user_id, display_name=name), statistics=dict(record_count=len(records),
        customer_count=len(cids), opportunity_count=len(oids)), records=records,
        context=dict(as_of=iso(now), customers=[dict(id=str(c['id']), name=c['name'], industry=c['industry_code'],
            customer_type=c['customer_type_code'], demand_summary=c['demand_summary'], updated_at=iso(c['updated_at']),
            source_ref=reference('customer', c, generation)) for c in customers], opportunities=[]))
    for o in opportunities:
        if o['stage_code'] and o['stage_code'] not in stages:
            raise ValueError('WEEKLY_STAGE_MAPPING_REQUIRED')
        source['context']['opportunities'].append(dict(id=str(o['id']), customer_id=str(o['customer_id']),
            name=o['name'], amount=o['amount'], currency=o['currency'].strip() if o['currency'] else None,
            stage_label=stages.get(o['stage_code']), status=o['status'], probability=o['probability'],
            expected_close_date=iso(o['expected_close_date']), product_line=o['product_line'],
            sales_channel=o['sales_channel'], partner_name=o['partner_name'], follow_up_plan=o['follow_up_plan'],
            updated_at=iso(o['updated_at']), source_ref=reference('opportunity', o, generation)))
    if validate_input(source):
        raise ValueError('WEEKLY_INVALID_SOURCE')
    raw = exact_json(source)
    return source, raw, hashlib.sha256(raw.encode()).hexdigest()
