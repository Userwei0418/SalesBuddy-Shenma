"""Targeted V239 exact service ACL validation in the separately authorized PG04 window."""
from __future__ import annotations

import json
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import asyncpg

import verify_customer_identity_postgres as harness

sys.path.insert(0, str(harness.ROOT / 'tools/installation'))
from runtime_roles import SERVICES, RolePolicyError, load_policy, provision_new, verify
from upgrade_permissions import reconcile

SIGNATURES = (
    'security.set_customer_business_identity(uuid,text[],uuid)',
    'security.partner_enterprise_reference(uuid)',
    'security.company_customer_directory_search(text,integer,integer,text,text,text[],text)',
)


async def verify_denials(roles, calls):
    evidence = []
    for service in ('worker','feishu','platform'):
        for signature,(sql,args) in zip(SIGNATURES,calls,strict=True):
            c = await harness.connect()
            try:
                async with c.transaction():
                    await c.execute('SET LOCAL ROLE '+roles[service])
                    assert await c.fetchval('SELECT current_user') == roles[service]
                    # No authorization_refresh prerequisite and no synthetic
                    # grant. Only the target SELECT can satisfy this negative.
                    try:
                        async with c.transaction():
                            await c.fetchval(sql,*args)
                    except asyncpg.InsufficientPrivilegeError as exc:
                        evidence.append({'service':service,'role':roles[service],
                            'signature':signature,'reached_target_select':True,'sqlstate':exc.sqlstate})
                    else:
                        raise AssertionError((service,'new helper unexpectedly callable'))
            finally:
                await c.close()
    return evidence


async def exercise(baseline):
    server = await harness.connect('postgres')
    await server.execute(f'CREATE DATABASE {harness.DB} TEMPLATE template0')
    await server.close()
    admin = await harness.connect()
    try:
        await harness.migrate(admin, baseline/'database')
        old = json.loads(subprocess.check_output([
            'git', 'show', harness.BASE+':tools/installation/runtime_permissions.json'], cwd=harness.ROOT))
        policy = load_policy(harness.ROOT/'tools/installation/runtime_permissions.json')
        roles = {service: 'identity04_'+service for service in SERVICES}
        # Real reviewed installer, no schema-wide/function-wide fixture grants.
        await provision_new(admin, database=harness.DB, roles=roles, policy=old)
        harness.check('v238_exact_four_service_policy_passes', (await verify(admin,roles,old))['status']=='passed')
        ledger_before = [dict(r) for r in await admin.fetch('SELECT * FROM ops.migration_checksum ORDER BY migration_key')]
        await harness.migrate(admin,harness.ROOT/'database')
        ledger_after = [dict(r) for r in await admin.fetch('SELECT * FROM ops.migration_checksum ORDER BY migration_key')]
        harness.save('LEDGER_BEFORE.json',ledger_before)
        harness.save('LEDGER_AFTER.json',ledger_after)
        harness.check('all_202_original_rows_unchanged_after_v239',len(ledger_before)==202
            and len(ledger_after)==203 and [r for r in ledger_after if r['migration_key']!='V239']==ledger_before)
        try:
            await verify(admin,roles,old)
        except RolePolicyError: harness.check('old_policy_refuses_new_migration_fingerprint')
        else: raise AssertionError('old policy accepted V239')
        async with admin.transaction():
            upgraded = await reconcile(admin,roles,policy)
        harness.check('v239_upgrade_exact_four_service_policy_passes',upgraded['status']=='passed')
        matrix = {}
        for service,role in roles.items():
            matrix[service] = {}
            for signature in SIGNATURES:
                allowed = await admin.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')",role,signature)
                matrix[service][signature] = allowed
                assert allowed == (service=='api'), (service,signature,allowed)
        harness.check('only_api_has_the_three_new_function_grants')
        for signature in SIGNATURES:
            assert not await admin.fetchval("SELECT has_function_privilege('salegent_feishu_worker',$1,'EXECUTE')",signature)
            assert not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM pg_proc p CROSS JOIN LATERAL "
                "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE p.oid=$1::regprocedure AND a.grantee=0 AND a.privilege_type='EXECUTE')",signature)
        harness.check('public_and_legacy_feishu_have_no_new_helper_execute')
        assert await admin.fetchval("SELECT has_function_privilege($1,"
            "'security.company_customer_directory_search(text,integer,integer,text,text,text[])','EXECUTE')",roles['api'])
        harness.check('api_old_six_argument_directory_remains_granted')
        ws,team,user = uuid4(),uuid4(),uuid4()
        await admin.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,'identity04','Identity04')",ws)
        await admin.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1",ws)
        await admin.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'identity','Identity team')",team,ws)
        await admin.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,'O','O','Operator')",user,ws)
        await admin.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,'operations','workspace',$3)",ws,user,team)
        await admin.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,'operations')",ws,user,team)
        actor = harness.IdentityRepository._actor(await admin.fetchrow(
            "SELECT * FROM security.resolve_account_actor('identity04','O',NULL)")).context

        @asynccontextmanager
        async def tx(service):
            c = await harness.connect()
            try:
                async with c.transaction():
                    await c.execute('SET LOCAL ROLE '+roles[service])
                    await harness.set_request_context(c,actor)
                    yield c
            finally:
                await c.close()

        async with tx('api') as c:
            customer = await harness.CustomerMutationRepository().create(c,actor,data=harness.body('ACL pure',['partner']))
            partners = await harness.PartnerRepository().list(c,q='ACL pure')
            directory = await harness.CustomerMemberRepository().claim_pool_page(c,business_identity='partner')
            harness.check('api_minimal_grants_create_bind_partner_and_directory',customer['partner_profile_id']
                and partners['items'][0]['linked_customer_id']==customer['id']
                and directory['items'][0]['id']==customer['id'])
        async with tx('worker') as c:
            row = await c.fetchrow('SELECT business_identities FROM crm.customer WHERE id=$1::uuid',customer['id'])
            harness.check('worker_can_read_identity_for_skip_without_new_helper',row['business_identities']==['partner'])
        calls = [
            ('SELECT security.set_customer_business_identity($1::uuid,$2::text[],$3::uuid)',
                (customer['id'],['partner'],customer['partner_profile_id'])),
            ('SELECT security.partner_enterprise_reference($1::uuid)',(customer['partner_profile_id'],)),
            ("SELECT security.company_customer_directory_search(NULL,10,0,NULL,NULL,ARRAY[]::text[],'all')",()),
        ]
        denial_evidence = await verify_denials(roles,calls)
        harness.check('worker_feishu_platform_real_calls_denied')
        final = await verify(admin,roles,policy)
        harness.save('RESULT.json',{'status':'passed','checks':harness.checks,'matrix':matrix,
            'target_select_denials':denial_evidence,
            'exact_role_verification':final,'policy_sha256':harness.sha(harness.ROOT/'tools/installation/runtime_permissions.json'),
            'v239_sha256':harness.sha(harness.ROOT/'database/migrations/V239__enterprise_business_identities.sql'),
            'grant_path':'V238 provision_new -> migrate V239 -> original upgrade_permissions.reconcile -> exact verify',
            'no_all_functions_fixture_grants':True,'no_model_calls':True,'no_shared_runtime_access':True})
    finally:
        await admin.close()


if __name__ == '__main__':
    harness.CLUSTER = Path('/tmp/sb-identity-pg16-20261013-04')
    harness.SOCKET = harness.CLUSTER/'socket'
    harness.DELIVERY = harness.DELIVERY.parent/'pg16-runtime04'
    harness.AUTH = harness.AUTH.with_name('ISOLATED_PG16_WINDOW_04.json')
    harness.check_window()
    harness.DELIVERY.mkdir(parents=True,exist_ok=True)
    harness.save('RUNTIME_INPUTS.json',{'git_head':subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=harness.ROOT,text=True).strip(),'files':{
            name:harness.sha(harness.ROOT/name) for name in (
                'tools/installation/runtime_roles.py','tools/installation/upgrade_permissions.py',
                'tools/installation/runtime_permissions.json','database/scripts/migrate.py',
                'database/migrations/V239__enterprise_business_identities.sql',
                'backend/tests/system/verify_customer_identity_runtime_acl.py')}})
    harness.verify = exercise
    harness.main()
