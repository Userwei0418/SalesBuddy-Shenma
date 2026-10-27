"""Narrow V192 -> V194 upgrade after canonical runtime provisioning.

Called by verify_inference_audit_postgres.py on its disposable PG16 server. The
source policy is independently pinned; neither a historical reconciliation call
nor a canonical ACL reapplication may conceal the migration's actual net delta.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from uuid import uuid4

import asyncpg

from verify_runtime_grants_postgres import migrate_through
from verify_task_import_postgres import context, draft, fixture, rejected, value

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/installation'))
import runtime_roles
from migrate import ROOT as DATABASE_ROOT, apply, discover

FINISH = 'security.finish_inference_operation(uuid,text,jsonb,jsonb,text)'
REPARSE = 'security.reparse_task_import_batch(uuid,integer,jsonb,jsonb,jsonb)'
SOURCE_POLICY_SHA256 = 'cc5f7414f74032aa8788ee1c1b4356738fad67d891d387c7c78ccddfbda15475'
SOURCE_LEDGER_SHA256 = '7af583fc2d84c2e9a9f99894c3c7fb5bb70524e300f527450dd08645be58b940'
FROZEN_TARGET_POLICY = Path(__file__).with_name('fixtures') / 'runtime_permissions_v194.json'

# Each entry is an independently observed legacy-chain permission omitted by the
# reviewed V192 policy. Keep the full 42-entry negative regression explicit.
OMITTED_TABLES = {
    'api': {
        'activity.visit_participant': ('UPDATE',),
        'activity.v_visit_collaborators': ('SELECT',),
        'insight.customer_risk_assessment': ('UPDATE',),
        'config.model_api_current': ('UPDATE',),
        'ops.crm_import_batch': ('INSERT', 'SELECT', 'UPDATE'),
        'ops.crm_import_record': ('INSERT', 'SELECT', 'UPDATE'),
        'ops.crm_import_binding': ('INSERT', 'SELECT', 'UPDATE'),
        'crm.opportunity_period_actual_snapshot': ('INSERT',),
        'activity.visit_opportunity': ('DELETE', 'INSERT'),
        'config.permission_catalog': ('SELECT',),
        'config.permission_role_default': ('SELECT',),
    },
    'worker': {
        'activity.visit_participant': ('SELECT',),
        'activity.v_visit_collaborators': ('SELECT',),
        'crm.opportunity_demo_scenes': ('SELECT',),
        'config.model_api_test': ('INSERT', 'UPDATE'),
        'config.model_api_release': ('INSERT',),
        'config.model_api_current': ('INSERT', 'UPDATE'),
        'crm.opportunity_related_partner': ('SELECT',),
        'activity.visit_opportunity': ('SELECT',),
    },
}
OMITTED_FUNCTIONS = {
    'api': (
        'security.restore_historical_customer_ownership(uuid,uuid,uuid)',
        'security.visit_opportunities_readable(uuid,uuid)',
        'security.visit_opportunities_writable(uuid,uuid)',
        'security.has_partner_followup_access(uuid)',
        'security.has_linked_visit_access(uuid)',
        'security.crm_history_visit_form_valid(uuid)',
        'security.authorization_visit_created(uuid)',
        'security.authorization_customer_mutation(uuid)',
        'security.authorization_history_feature()',
        'security.check_crm_import_target(uuid,uuid)',
    ),
    'worker': (
        'security.visit_opportunities_readable(uuid,uuid)',
        'security.visit_opportunities_writable(uuid,uuid)',
        'security.has_partner_followup_access(uuid)',
        'security.has_linked_visit_access(uuid)',
    ),
}


def source_policy(target):
    # Reverse only the reviewed two-version contract. The immutable source hash
    # prevents future policy edits from silently redefining this source fixture.
    source = deepcopy(target)
    assert source['schema'] == 'V194', 'Review this frozen upgrade before extending its target'
    source['schema'] = 'V192'
    source['migration_fingerprint'] = SOURCE_LEDGER_SHA256
    for service in ('api', 'worker'):
        source['services'][service]['tables']['agent.inference_operation'].append('UPDATE')
        source['services'][service]['functions'].remove(FINISH)
    source['services']['api']['functions'].remove(REPARSE)
    assert runtime_roles.fingerprint(source) == SOURCE_POLICY_SHA256
    return runtime_roles.validate_policy(source)


async def acl_snapshot(conn):
    """Every non-owner ACL in application schemas, including PUBLIC/pg_ roles."""
    rows = await conn.fetch('''WITH objects AS (
      SELECT 'relation' AS kind,c.oid::regclass::text AS name,'' AS col,c.relowner AS owner,
        COALESCE(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 's'::"char" ELSE 'r'::"char" END,c.relowner)) AS acl
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=ANY($1::text[])
      UNION ALL SELECT 'column',c.oid::regclass::text,a.attname,c.relowner,a.attacl
      FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=ANY($1::text[]) AND a.attnum>0 AND NOT a.attisdropped
      UNION ALL SELECT 'function',p.oid::regprocedure::text,'',p.proowner,COALESCE(p.proacl,acldefault('f',p.proowner))
      FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=ANY($1::text[])
      UNION ALL SELECT 'schema',n.nspname,'',n.nspowner,COALESCE(n.nspacl,acldefault('n',n.nspowner))
      FROM pg_namespace n WHERE n.nspname=ANY($1::text[])
      UNION ALL SELECT 'database',datname,'',datdba,COALESCE(datacl,acldefault('d',datdba))
      FROM pg_database WHERE datname=current_database()
      UNION ALL SELECT 'default',d.defaclobjtype::text||':'||COALESCE(n.nspname,''),'',d.defaclrole,d.defaclacl
      FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
      WHERE d.defaclnamespace=0 OR n.nspname=ANY($1::text[])
    ) SELECT kind,name,col,COALESCE(r.rolname,'PUBLIC') AS grantee,a.privilege_type,a.is_grantable,
      pg_get_userbyid(a.grantor) AS grantor FROM objects o CROSS JOIN LATERAL aclexplode(o.acl) a
      LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE a.grantee<>o.owner''', [*runtime_roles.SCHEMAS, 'public'])
    return {tuple(row) for row in rows}


async def omitted_permissions(conn, roles, *, allowed=False):
    count = 0
    for service, tables in OMITTED_TABLES.items():
        for table, privileges in tables.items():
            for privilege in privileges:
                assert await conn.fetchval('SELECT has_table_privilege($1,$2,$3)', roles[service], table, privilege) == allowed
                assert await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_class c,
                    LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
                    WHERE c.oid=$2::regclass AND r.rolname=$1 AND a.privilege_type=$3)''', roles[service], table, privilege) == allowed
                count += 1
    for service, functions in OMITTED_FUNCTIONS.items():
        for signature in functions:
            assert await conn.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", roles[service], signature) == allowed
            assert await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM pg_proc p,
                LATERAL aclexplode(p.proacl) a JOIN pg_roles r ON r.oid=a.grantee
                WHERE p.oid=$2::regprocedure AND r.rolname=$1 AND a.privilege_type='EXECUTE')''', roles[service], signature) == allowed
            count += 1
    assert count == 42


async def verify(config):
    suffix = uuid4().hex[:12]
    database = 'sb_canonical_' + suffix
    roles = {service: 'sb_canon_' + service + '_' + suffix for service in sorted(runtime_roles.SERVICES)}
    legacy = {key: 'sb_canon_' + key + '_' + suffix for key in ('app', 'runtime', 'readonly')}
    admin = await asyncpg.connect(database='postgres', **config)
    owner = None
    created, checks = [], []
    try:
        await admin.execute(f'CREATE DATABASE "{database}" TEMPLATE template0 ENCODING \'UTF8\'')
        owner = await asyncpg.connect(database=database, **config)
        await migrate_through(owner, 192)
        # This is deliberately the historical V192 -> V194 contract. A current
        # policy may contain later reviewed grants, but must never redefine the
        # 42 historical negative cases or this migration's exact ACL delta.
        # Fixture copied verbatim from reviewed commit 16ac529, independently
        # checked below by reversing only V193/V194 into the pinned V192 hash.
        target = runtime_roles.load_policy(FROZEN_TARGET_POLICY)
        source = source_policy(target)
        await runtime_roles.provision_new(owner, database=database, roles=roles, policy=source)
        created.extend(roles.values())
        # Mirror inherited legacy compatibility eligibility without running an
        # old maintenance chain after canonical service provisioning.
        for role in legacy.values():
            await owner.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            created.append(role)
        await owner.execute(f'GRANT "{legacy["app"]}" TO "{legacy["runtime"]}"')
        await owner.execute(f'GRANT USAGE ON SCHEMA agent,security,workflow TO "{legacy["app"]}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE ON agent.inference_operation,workflow.task_import_batch TO "{legacy["app"]}"')
        await owner.execute(f'GRANT USAGE ON SCHEMA security,workflow TO "{legacy["readonly"]}"')
        await owner.execute(f'GRANT SELECT,INSERT,UPDATE ON workflow.task_import_batch TO "{legacy["readonly"]}"')
        await runtime_roles.verify(owner, roles, source)
        await omitted_permissions(owner, roles)
        data = await fixture(owner)
        await context(owner, data)
        batch, items = await draft(owner, data)
        prior = await value(owner, 'SELECT to_jsonb(b) FROM workflow.task_import_batch b WHERE id=$1', batch)
        # Built-in all-data writer satisfies the historical table/schema test,
        # but must never receive either application SECURITY DEFINER capability.
        for schema in ('agent', 'security'):
            assert await owner.fetchval("SELECT has_schema_privilege('pg_write_all_data',$1,'USAGE')", schema)
        assert await owner.fetchval("SELECT has_table_privilege('pg_write_all_data','agent.inference_operation','INSERT')")
        assert await owner.fetchval("SELECT has_table_privilege('pg_write_all_data','workflow.task_import_batch','INSERT,UPDATE')")
        before = await acl_snapshot(owner)
        checks.append('canonical_v192_source_pinned_and_all_42_legacy_permissions_denied')

        # Positive control: this source really reproduces the historical-chain
        # expansion, so the negative assertions cannot pass due to an ineligible
        # fixture. Roll it back before testing either new migration.
        legacy_probe = owner.transaction()
        await legacy_probe.start()
        try:
            await owner.fetchval('SELECT security.reconcile_runtime_grants()')
            await omitted_permissions(owner, roles, allowed=True)
        finally:
            await legacy_probe.rollback()
        assert await acl_snapshot(owner) == before
        await runtime_roles.verify(owner, roles, source)
        checks.append('source_legacy_chain_reproduces_all_42_excess_grants_only_inside_rolled_back_probe')

        increments = [m for m in discover() if 'V192' < m.key <= 'V194']
        assert [m.key for m in increments] == ['V193', 'V194']

        async def upgrade():
            # The production scoped adapter applies reviewed files in one outer
            # transaction, never the generic migrate() maintenance wrapper.
            for migration in increments:
                assert await apply(owner, migration, DATABASE_ROOT) == 'applied'
            await runtime_roles.verify(owner, roles, target)
            await omitted_permissions(owner, roles)
            finish_roles = (roles['api'], roles['worker'], legacy['app'], legacy['runtime'])
            reparse_roles = (roles['api'], legacy['app'], legacy['runtime'], legacy['readonly'])
            grantor = await owner.fetchval('SELECT current_user')
            additions = {('function', FINISH, '', role, 'EXECUTE', False, grantor) for role in finish_roles}
            additions |= {('function', REPARSE, '', role, 'EXECUTE', False, grantor) for role in reparse_roles}
            removals = {('relation', 'agent.inference_operation', '', role, 'UPDATE', False, grantor)
                        for role in (roles['api'], roles['worker'], legacy['app'])}
            after = await acl_snapshot(owner)
            assert after - before == additions, after - before
            assert before - after == removals, before - after
            for signature in (FINISH, REPARSE):
                assert not await owner.fetchval("SELECT has_function_privilege('pg_write_all_data',$1,'EXECUTE')", signature)
            assert await value(owner, 'SELECT to_jsonb(b) FROM workflow.task_import_batch b WHERE id=$1', batch) == prior

        # A failed outer release transaction must leave both ACLs and ledger at
        # the previously verified source, including the original draft.
        probe = owner.transaction()
        await probe.start()
        try:
            await upgrade()
        finally:
            await probe.rollback()
        assert await acl_snapshot(owner) == before
        await runtime_roles.verify(owner, roles, source)
        checks.append('rolled_back_upgrade_restores_canonical_acl_ledger_and_source')
        async with owner.transaction():
            await upgrade()
        checks.append('committed_direct_v193_v194_upgrade_has_only_reviewed_eight_grants_three_revocations')
        checks.append('all_42_legacy_permissions_and_builtin_pg_write_execute_stay_denied_without_reconciliation')
        checks.append('canonical_four_service_contract_rls_and_preexisting_draft_preserved')
        committed_acl = await acl_snapshot(owner)
        for migration in increments:
            assert await apply(owner, migration, DATABASE_ROOT) == 'unchanged'
        assert await acl_snapshot(owner) == committed_acl
        checks.append('direct_migration_replay_does_not_reconcile_historical_grants')

        # Exercise the two new calls as the canonical API without adding any
        # fixture permissions. Business source remains immutable; no dispatch.
        operation = uuid4()
        async with owner.transaction():
            await owner.execute('SET LOCAL row_security=on')
            await owner.execute(f'SET LOCAL ROLE "{roles["api"]}"')
            await context(owner, data)
            await owner.execute('''INSERT INTO agent.inference_operation
              (id,workspace_id,actor_user_ref_id,actor_role_code,capability)
              VALUES($1,$2,$3,'sales','task_import')''', operation, data['ws'], data['creator'])
            await rejected(owner, 'SELECT * FROM agent.inference_operation')
            assert await owner.fetchval("SELECT security.finish_inference_operation($1,'failed','{}','[]','SyntheticFailure')", operation)
            assert not await owner.fetchval("SELECT security.finish_inference_operation($1,'accepted','{}','[]',NULL)", operation)
            assert await owner.fetchval('SELECT security.reparse_task_import_batch($1,1,$2::jsonb,\'[]\',$2::jsonb)', batch, json.dumps(items)) == batch
        assert await owner.fetchval('SELECT count(*) FROM workflow.task') == 0
        assert await owner.fetchval('SELECT count(*) FROM workflow.task_import_item') == 0
        assert await owner.fetchval('SELECT version_no FROM workflow.task_import_batch WHERE id=$1', batch) == 2
        checks.append('canonical_api_finishes_and_reparses_without_private_read_or_task_dispatch')
        return checks
    finally:
        if owner:
            await owner.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        # Inherited child roles must disappear before their parent group.
        for role in reversed(created):
            await admin.execute(f'DROP ROLE IF EXISTS "{role}"')
        await admin.close()
