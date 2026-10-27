"""V297 creation-only claim scope with a real restricted API role in private PG16.

PG_BIN=/path/to/pg16/bin python database/tests/verify_opportunity_creation_claim_scope_postgres.py
No DSN, model, shared database or production access; the synthetic cluster is removed.
"""
import asyncio
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / p) for p in ("database/scripts", "tools/installation", "backend/src")]
from migrate import migrate
from runtime_roles import load_policy, verify_service_roles
from upgrade_permissions import reconcile_service_roles
from sales_backend.db import set_request_context
from sales_backend.domain.agent import ActorContext, RoleCode
from sales_backend.domain.opportunity_creation import OpportunityCreationError
from sales_backend.repositories.opportunity_creation import creation_access, creation_customers, require_creation_customer
from sales_backend.repositories.opportunity_history import native_creation

PORT = 56427
ROLE = "claim_scope_api"


async def seed(c):
    workspace = uuid4()
    await c.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'Claim scope synthetic')",workspace,str(workspace))
    await c.execute("UPDATE control.tenant_module SET enabled=true WHERE workspace_id=$1",workspace)
    teams = {name:uuid4() for name in ('north','south','north_child')}
    for name,team in teams.items():
        await c.execute("INSERT INTO platform.team(id,workspace_id,code,name,parent_team_id) VALUES($1,$2,$3,$3,$4)",team,workspace,name,teams['north'] if name=='north_child' else None)
    actors={}
    for name,role,team in [('s1','sales','north'),('s2','sales','south'),('sc','sales','north_child'),('sup1','supervisor','north'),('sup2','supervisor','north'),('manager','manager','south'),('admin','administrator','north'),('fde','fde','north')]:
        user=uuid4()
        await c.execute("INSERT INTO platform.user_ref(id,workspace_id,external_user_id,account_code,display_name) VALUES($1,$2,$3,$3,$3)",user,workspace,name)
        await c.execute("INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)",workspace,user,role,'self' if role=='fde' else 'workspace',teams[team])
        await c.execute("INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)",workspace,user,teams[team],role)
        actors[name]=ActorContext(workspace_id=str(workspace),user_id=str(user),role=role,data_scope='self' if role=='fde' else 'workspace',team_ids=(str(teams[team]),))
    # Wide feature grants deliberately prove that the new role/claim gate is
    # independent of an opportunity owner choice or a broad customer directory.
    for role in ('sales','supervisor','manager','administrator','fde'):
        permission_role=await c.fetchval("SELECT id FROM config.permission_role WHERE workspace_id=$1 AND builtin_role_code=$2",workspace,role)
        for permission in ('opportunity.create','opportunity.read','opportunity.update','opportunity.create_for_others','customer.reference'):
            await c.execute("""INSERT INTO config.permission_role_grant(workspace_id,role_id,permission_code,scope_code)
             VALUES($1,$2,$3,'workspace') ON CONFLICT(role_id,permission_code) DO UPDATE SET scope_code='workspace'""",workspace,permission_role,permission)
    customers={}
    for name in (*actors,'pool','legacy'):
        customer=uuid4(); customers[name]=customer
        await c.execute("""INSERT INTO crm.customer(id,workspace_id,name,normalized_name,created_by_user_ref_id,owner_user_ref_id,owner_team_id)
          VALUES($1,$2,$3,$3,$4,$4,$5)""",customer,workspace,name,UUID(actors['manager'].user_id),teams['south'])
        # owner_team intentionally disagrees with the effective north claimant.
        state='unclaimed' if name=='pool' else 'legacy_review' if name=='legacy' else 'claimed'
        user=UUID(actors[name].user_id) if name in actors else None
        await c.execute("""INSERT INTO crm.customer_ownership(customer_id,workspace_id,owner_user_ref_id,state)
         VALUES($1,$2,$3,$4) ON CONFLICT(customer_id) DO UPDATE SET owner_user_ref_id=excluded.owner_user_ref_id,state=excluded.state""",customer,workspace,user,state)
    return workspace,teams,actors,customers


async def insert(c, actor, customer, *, owner=None, meta=None):
    opportunity=uuid4();owner=owner or actor
    async with native_creation(c,opportunity):
        await c.execute("""INSERT INTO crm.opportunity(id,workspace_id,customer_id,name,created_by_user_ref_id,
          owner_user_ref_id,owner_team_id,source_code,amount,probability,stage_code,import_meta)
          VALUES($1,$2::uuid,$3,$4,$5::uuid,$6::uuid,$7::uuid,'manual',100,10,'identified',$8::jsonb)""",
          opportunity,actor.workspace_id,customer,'Synthetic '+str(opportunity),actor.user_id,owner.user_id,owner.team_ids[0],json.dumps(meta or {}))
    return opportunity


async def fails(c, call, code):
    try:
        async with c.transaction(): await call()
    except (OpportunityCreationError,asyncpg.PostgresError) as error:
        assert getattr(error,'code',None)==code or getattr(error,'message',None)==code,(type(error),str(error),code)
        return
    raise AssertionError('Expected '+code)


async def verify(socket):
    cfg=dict(host=str(socket),port=PORT,database='postgres',password='',passfile='/dev/null',ssl=False)
    owner=await asyncpg.connect(user='claim_scope_owner',command_timeout=120,**cfg)
    api=None; rival=None
    try:
        for role in ('salegent_feishu_worker','salegent_api','salegent_worker','salegent_platform'):
            await owner.execute(f'CREATE ROLE {role} NOLOGIN')
        with redirect_stdout(io.StringIO()): await migrate(owner,ROOT/'database',adopt_existing=True)
        policy=load_policy()
        await owner.execute(f'CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOBYPASSRLS')
        async with owner.transaction(): await reconcile_service_roles(owner,{'api':ROLE},policy)
        async with owner.transaction(): ws,teams,actors,customers=await seed(owner)
        async with owner.transaction(): ws2,t2,a2,c2=await seed(owner)
        api=await asyncpg.connect(user=ROLE,command_timeout=30,**cfg)
        rival=await asyncpg.connect(user='claim_scope_owner',command_timeout=5,**cfg)
        assert not await api.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
        expected={'s1':{'s1'},'s2':{'s2'},'sc':{'sc'},'sup1':{'sup1','s1','sc'},'sup2':{'sup2','s1','sc'},
          'manager':{'manager','s1','s2','sc','sup1','sup2'},'admin':set(actors),'fde':set(actors)}
        for name,allowed in expected.items():
            async with api.transaction():
                await set_request_context(api,actors[name])
                options=await creation_customers(api,'',100,0)
                assert {r['name'] for r in options['items']}==allowed,(name,options)
                for customer_name,customer in customers.items():
                    access=await creation_access(api,customer)
                    assert access['can_create_opportunity']==(customer_name in allowed),(name,customer_name,access)
                    if customer_name in allowed:
                        assert set(access['customer_reference'])=={'id','name'}
                        await require_creation_customer(api,customer)
                    else:
                        assert access['customer_reference'] is None
                assert (await creation_access(api,c2['s1']))['denial_reason']=='OPPORTUNITY_CUSTOMER_NOT_FOUND'
        checks=['sales_supervisor_ancestor_manager_non_sales_matrix_and_cross_company_minimal_reference']
        # A forged role is ineffective without the corresponding valid binding.
        async with api.transaction():
            await set_request_context(api,actors['s1'].model_copy(update={'role':RoleCode.MANAGER}))
            assert (await creation_access(api,customers['s2']))['denial_reason']=='OPPORTUNITY_CREATE_FORBIDDEN'
        # DB trigger runs even when callers skip the shared Python validation.
        async with api.transaction():
            await set_request_context(api,actors['s1'])
            for target in ('pool','legacy','s2'):
                code='OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED' if target in ('pool','legacy') else 'OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED'
                await fails(api,lambda:insert(api,actors['s1'],customers[target],meta={'import_type':'crm_history','import_record_id':str(uuid4()),'import_batch_id':str(uuid4())}),code)
            own=await insert(api,actors['s1'],customers['s1'])
            # Original update remains legal after release; no creation gate on UPDATE.
        await owner.execute("UPDATE crm.customer_ownership SET state='unclaimed',owner_user_ref_id=NULL WHERE customer_id=$1",customers['s1'])
        async with api.transaction():
            await set_request_context(api,actors['s1'])
            await fails(api,lambda:require_creation_customer(api,customers['s1']),'OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED')
            assert await api.fetchval('SELECT id FROM crm.opportunity WHERE id=$1',own)==own
            await api.execute("UPDATE crm.opportunity SET name=name||' edited' WHERE id=$1",own)
        await owner.execute("UPDATE crm.customer_ownership SET state='claimed',owner_user_ref_id=$2::uuid WHERE customer_id=$1",customers['s1'],actors['s1'].user_id)
        checks.append('forged_role_and_import_flags_denied_direct_insert_guard_existing_read_update_retained')
        # Preserve exactly V292's manager -> sales (not supervisor) visibility evidence.
        async with api.transaction():
            await set_request_context(api,actors['manager'])
            sales_opportunity=await insert(api,actors['manager'],customers['s1'])
            supervisor_opportunity=await insert(api,actors['manager'],customers['sup1'])
        assert await owner.fetchval('SELECT count(*) FROM security.opportunity_sales_visibility WHERE opportunity_id=$1',sales_opportunity)==1
        assert await owner.fetchval('SELECT count(*) FROM security.opportunity_sales_visibility WHERE opportunity_id=$1',supervisor_opportunity)==0
        checks.append('manager_sales_visibility_capture_retained_without_supervisor_broadening')
        # Actual current organization data, never customer.owner_team or stale actor teams.
        await owner.execute("UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE workspace_id=$1 AND user_ref_id=$2::uuid",ws,actors['sc'].user_id)
        async with api.transaction():
            await set_request_context(api,actors['sup1'])
            await fails(api,lambda:require_creation_customer(api,customers['sc']),'OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED')
        # Reference and create_for_others gates are not replaced by the new capability.
        await owner.execute('INSERT INTO config.account_authorization(workspace_id,user_ref_id) VALUES($1,$2::uuid)',ws,actors['s1'].user_id)
        await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2::uuid,'customer.reference','deny')",ws,actors['s1'].user_id)
        async with api.transaction():
            await set_request_context(api,actors['s1'])
            assert (await creation_customers(api,'',10,0))['total']==0
            await fails(api,lambda:require_creation_customer(api,customers['s1']),'OPPORTUNITY_CREATE_FORBIDDEN')
        await owner.execute("DELETE FROM config.account_permission_override WHERE workspace_id=$1 AND user_ref_id=$2::uuid",ws,actors['s1'].user_id)
        await owner.execute("INSERT INTO config.account_permission_override(workspace_id,user_ref_id,permission_code,effect) VALUES($1,$2::uuid,'opportunity.create_for_others','deny')",ws,actors['s1'].user_id)
        async with api.transaction():
            await set_request_context(api,actors['s1'])
            try:
                async with api.transaction(): await insert(api,actors['s1'],customers['s1'],owner=actors['s2'])
            except asyncpg.InsufficientPrivilegeError: pass
            else: raise AssertionError('delegated owner bypassed original INSERT RLS')
        checks.append('expired_appointments_reference_denial_and_original_delegated_owner_rls')
        # One successful check holds claim and effective membership facts until commit.
        async with api.transaction():
            await set_request_context(api,actors['sup1'])
            await require_creation_customer(api,customers['s1'])
            for statement,args in [
                ("UPDATE crm.customer_ownership SET state='unclaimed',owner_user_ref_id=NULL WHERE customer_id=$1",[customers['s1']]),
                ("UPDATE platform.team_membership SET valid_to=clock_timestamp() WHERE user_ref_id=$1::uuid",[actors['s1'].user_id]),
            ]:
                try:
                    async with rival.transaction():
                        await rival.execute("SET LOCAL lock_timeout='150ms'")
                        await rival.execute(statement,*args)
                except asyncpg.LockNotAvailableError: pass
                else: raise AssertionError('creation did not retain scope locks')
        # Reverse interleaving: release owns the row first. The waiting creation
        # must re-read its committed unclaimed state instead of accepting preview.
        async with api.transaction():
            await set_request_context(api,actors['sup1'])
            release=rival.transaction()
            await release.start()
            pending=None
            try:
                await rival.execute("UPDATE crm.customer_ownership SET state='unclaimed',owner_user_ref_id=NULL WHERE customer_id=$1",customers['s1'])
                pending=asyncio.create_task(fails(api,lambda:require_creation_customer(api,customers['s1']),
                    'OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED'))
                await asyncio.sleep(0.05)
                assert not pending.done(), 'creation did not wait for the competing release'
                await release.commit()
                await pending
            finally:
                if rival.is_in_transaction(): await release.rollback()
                if pending and not pending.done():
                    pending.cancel()
                    try: await pending
                    except asyncio.CancelledError: pass
        checks.append('claim_release_and_appointment_locks_and_release_first_recheck')
        await verify_service_roles(owner,{'api':ROLE},policy)
        assert not await owner.fetchval("SELECT has_function_privilege($1,'security.opportunity_create_customer_reason(uuid)','EXECUTE')",ROLE)
        checks.append('exact_runtime_policy_no_private_predicate_grant_original_rls_fingerprint_retained')
        print(json.dumps({'status':'passed','checks':checks,'synthetic':True,'running_business_data_touched':False,
          'migration_sha256':hashlib.sha256((ROOT/'database/migrations/V297__opportunity_creation_claim_scope.sql').read_bytes()).hexdigest(),
          'migration_fingerprint':policy['migration_fingerprint'],'rls_fingerprint':policy['rls_fingerprint'],
          'policy_sha256':hashlib.sha256((ROOT/'tools/installation/runtime_permissions.json').read_bytes()).hexdigest()},indent=2),flush=True)
    finally:
        for c in (api,rival,owner):
            if c: await c.close()


def run():
    binary=Path(os.environ['PG_BIN'])
    with tempfile.TemporaryDirectory(prefix='sb-create-scope-',dir='/tmp') as temporary:
        root=Path(temporary);data=root/'pg';socket=root/'socket';socket.mkdir()
        subprocess.run([str(binary/'initdb'),'-D',str(data),'-U','claim_scope_owner','--auth=trust','--encoding=UTF8','--no-locale'],check=True,capture_output=True)
        try:
            subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-l',str(root/'postgres.log'),'-o',f"-k {socket} -p {PORT} -c listen_addresses=''",'-w','start'],check=True,capture_output=True)
            asyncio.run(verify(socket))
        finally:
            if (data/'postmaster.pid').exists(): subprocess.run([str(binary/'pg_ctl'),'-D',str(data),'-m','fast','-w','stop'],check=True,capture_output=True)
    print('Synthetic PostgreSQL stopped and removed',flush=True)


if __name__=='__main__': run()
