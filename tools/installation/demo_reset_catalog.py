"""Fail-closed business reset scope. Identity, settings, originals and usage remain."""
import hashlib
import json

from database_inventory import sql_name
from instance_files import InstanceError
from release_bundle import json_bytes

BUSINESS_SCHEMAS=('crm','activity','workflow','insight')
RETAINED=frozenset(('crm.product','crm.material','crm.opportunity_saved_view','crm.opportunity_view_preference',
                    'activity.visit_import','activity.visit_import_content',
                    'activity.visit_upload_intent','activity.visit_upload_attempt'))
CLEARED=frozenset('''crm.business_change crm.contact crm.customer crm.customer_actual
crm.opportunity_actual_basis crm.opportunity_actual_confirmation crm.opportunity_material crm.customer_creation_request
crm.customer_assignment crm.customer_claim_request crm.customer_duplicate_candidate
crm.customer_ownership crm.customer_ownership_event crm.customer_product crm.customer_sales_member
crm.opportunity crm.opportunity_demo_scenes crm.opportunity_forecast crm.opportunity_participant
crm.opportunity_project_participant
crm.opportunity_period_actual_snapshot crm.opportunity_quote_reference crm.opportunity_related_partner
crm.partner crm.sales_target crm.sales_target_batch_request crm.sales_target_change_request
activity.action_item activity.visit activity.visit_contact activity.visit_field_value
activity.visit_opportunity activity.visit_participant
workflow.notification workflow.notification_delivery workflow.task workflow.task_assignee workflow.task_candidate workflow.task_event
workflow.task_import_batch workflow.task_import_item
insight.business_advice insight.business_suggestion insight.customer_risk_assessment insight.quadrant_score
insight.recommendation insight.report insight.risk insight.risk_event insight.sales_competency_review'''.split())
# Only these explicitly listed BUSINESS history guards may be temporarily stopped.
# ops.audit_log/configuration/provider evidence guards never appear here.
HISTORY_GUARDS={
 'crm.customer_ownership_event':'ownership_history_append_only',
 'crm.sales_target_change_request':'target_request_no_delete',
 'crm.sales_target_batch_request':'target_batch_no_delete',
 'crm.opportunity_period_actual_snapshot':'period_actual_snapshot_immutable',
 'workflow.task_import_batch':'task_import_batch_guard',
 'workflow.task_import_item':'task_import_item_guard',
}


def quoted(table):
    if table not in CLEARED|RETAINED: raise InstanceError('Unknown demo-reset table')
    return '.'.join(sql_name(x) for x in table.split('.'))


def sha(value): return hashlib.sha256(json_bytes(value)).hexdigest()


async def structure(conn):
    tables=[r['name'] for r in await conn.fetch('''SELECT n.nspname||'.'||c.relname AS name FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname=ANY($1::text[]) ORDER BY 1''',list(BUSINESS_SCHEMAS))]
    constraints=[dict(r) for r in await conn.fetch('''SELECT c.conrelid::regclass::text AS source,
      CASE WHEN c.confrelid<>0 THEN c.confrelid::regclass::text END AS target,c.conname,
      pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c
      WHERE c.conrelid IN (SELECT oid FROM pg_class WHERE relnamespace IN
       (SELECT oid FROM pg_namespace WHERE nspname=ANY($1::text[])))
      OR c.confrelid IN (SELECT oid FROM pg_class WHERE relnamespace IN
       (SELECT oid FROM pg_namespace WHERE nspname=ANY($1::text[]))) ORDER BY 1,3''',list(BUSINESS_SCHEMAS))]
    triggers=[dict(r) for r in await conn.fetch('''SELECT t.tgrelid::regclass::text AS table_name,t.tgname,
      t.tgenabled::text AS enabled,pg_get_triggerdef(t.oid) AS definition,
      n.nspname||'.'||p.proname AS function_name,md5(pg_get_functiondef(p.oid)) AS function_hash
      FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE NOT t.tgisinternal AND t.tgrelid IN (SELECT c.oid FROM pg_class c JOIN pg_namespace s
       ON s.oid=c.relnamespace WHERE s.nspname=ANY($1::text[])) ORDER BY 1,2''',list(BUSINESS_SCHEMAS))]
    foreign_guards=[dict(r) for r in await conn.fetch('''SELECT t.tgrelid::regclass::text AS table_name,
      c.conname,t.tgtype,p.proname,t.tgenabled::text AS enabled FROM pg_trigger t JOIN pg_constraint c
      ON c.oid=t.tgconstraint JOIN pg_proc p ON p.oid=t.tgfoid WHERE c.contype='f' AND
      (c.conrelid IN (SELECT oid FROM pg_class WHERE relnamespace IN (SELECT oid FROM pg_namespace WHERE nspname=ANY($1::text[])))
       OR c.confrelid IN (SELECT oid FROM pg_class WHERE relnamespace IN (SELECT oid FROM pg_namespace WHERE nspname=ANY($1::text[]))))
      ORDER BY 1,2,3,4''',list(BUSINESS_SCHEMAS))]
    return {'tables':tables,'constraints':constraints,'triggers':triggers,'foreign_guards':foreign_guards}


def validate_structure(actual,expected):
    if set(actual['tables'])!=CLEARED|RETAINED: raise InstanceError('Unreviewed business tables; reset refused')
    # Enable modes are captured and restored, not normalized to enabled.
    def contract(value):
        return {**value,'triggers':[{k:v for k,v in row.items() if k!='enabled'} for row in value['triggers']]}
    if contract(actual)!=contract(expected): raise InstanceError('Unreviewed business dependency/trigger changes; reset refused')
    for fk in actual['constraints']:
        if fk['target'] in CLEARED and fk['source'] not in CLEARED:
            raise InstanceError('Retained data references reset business; reset refused')
    for row in actual['triggers']:
        if row['enabled'] not in ('O','D','R','A'): raise InstanceError('Unknown trigger enable mode')
    return actual


def load_catalog(root):
    value=json.loads((root/'release/tools/installation/demo_reset_catalog.json').read_text())
    if value.get('format')!='salesbuddy-demo-reset-catalog-v1': raise InstanceError('Unsupported reset catalog')
    return value['structure']


async def toggle_reset_triggers(conn,baseline,*,restore=False):
    modes={'O':'ENABLE','D':'DISABLE','R':'ENABLE REPLICA','A':'ENABLE ALWAYS'}
    for row in baseline['triggers']:
        table=row['table_name']; name=row['tgname']
        selected=(HISTORY_GUARDS.get(table)==name or (table in CLEARED and name in {'feishu_capture','feishu_finance_parent'}))
        if not selected: continue
        action=modes[row['enabled']] if restore else 'DISABLE'
        await conn.execute('ALTER TABLE '+quoted(table)+' '+action+' TRIGGER '+sql_name(name))


async def clear_business(conn,workspace):
    # A single statement lets PostgreSQL validate cyclic immediate FKs against
    # the complete post-statement state, without disabling any FK or RLS policy.
    parts=['d'+str(i)+' AS (DELETE FROM '+quoted(table)+' WHERE workspace_id=$1 RETURNING 1)'
           for i,table in enumerate(sorted(CLEARED))]
    await conn.execute('WITH '+','.join(parts)+' SELECT 1',workspace)
