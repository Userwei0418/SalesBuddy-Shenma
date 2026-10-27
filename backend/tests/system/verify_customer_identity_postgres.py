"""V238 -> V239 on a private, disposable PG16 cluster; never opens LOCAL/production."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'database/scripts')]
from migrate import migrate
from sales_backend.db import _initialize_connection, set_request_context
from sales_backend.auth.passwords import encode_password
from sales_backend.domain.concurrency import VersionConflict
from sales_backend.repositories.operations_customers import OperationsCustomerRepository
from sales_backend.contracts.customer_creation import CustomerCreationRequestBody
from sales_backend.repositories.customer_creation_requests import CustomerCreationRequestRepository
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customer_members import CustomerMemberRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.partners import PartnerRepository
from sales_backend.repositories.identity import IdentityRepository
from sales_backend.repositories.authorization import AuthorizationRepository
from sales_backend.contracts.authorization import AccountAuthorizationSave
from sales_backend.services.idempotency import execute_mutation
from sales_backend.repositories.customer_portfolio import CustomerPortfolioRepository
from sales_backend.contracts.customer_portfolio import CustomerPortfolioQuery
from sales_backend.repositories.customer_map import CustomerMapRepository

BASE = 'f7344c5bc8d7249b5c3ddc209fd07c9cb29b05a0'
CLUSTER = Path('/tmp/sb-identity-pg16-20261013-03')
SOCKET = CLUSTER / 'socket'
PORT = 15539
OWNER = 'identity_verify_owner'
DB = 'salesbuddy_identity_verify'
PG = Path('/opt/homebrew/opt/postgresql@16/bin')
DELIVERY = Path('/Users/zhouwei5/Developer/SalesBuddy-deliveries/backend/identity-draft-20261013-01/pg16-run03')
AUTH = Path('/Users/zhouwei5/Developer/SalesBuddy-deliveries/control/identity-draft-20261013-01/ISOLATED_PG16_WINDOW_03.json')
REGISTRY = Path('/Users/zhouwei5/Developer/Raccoon SalesBuddy/docs/TEAM_REGISTRY.json')
checks = []


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(name, obj):
    with (DELIVERY / name).open('x') as f: json.dump(obj, f, ensure_ascii=False, indent=2, default=str); f.write('\n')
def check(name, condition=True):
    assert condition, name
    checks.append(name); print(name, flush=True)

def check_window():
    auth = json.loads(AUTH.read_text())
    now = datetime.now(timezone.utc)
    assert datetime.fromisoformat(auth.get('not_before') or auth['opened_at']) <= now < datetime.fromisoformat(auth['expires_at'])
    assert auth['cluster_path'] == str(CLUSTER) and auth['port'] == PORT and auth['database'] == DB
    registry = json.loads(REGISTRY.read_text())
    window = registry['active_isolated_db_window']
    assert window and (window.get('id') or window.get('request')) == auth['request']


async def connect(database=DB):
    c = await asyncpg.connect(host=str(SOCKET), port=PORT, user=OWNER, database=database,
                              passfile='/dev/null', command_timeout=120)
    await _initialize_connection(c)
    return c


def body(name, roles=None, **changes):
    data = dict(name=name, industry='企业软件', customer_type='潜在客户', level_code='Tier-2',
                source='合作伙伴', target_team='Identity team', partner_name='', contact_name='Synthetic',
                contact_title='Director', contact_role='决策者')
    if roles is not None:
        data['business_identities'] = roles
        if 'customer' not in roles: data['customer_type'] = None
    data.update(changes)
    return CustomerCreationRequestBody.model_validate(data).model_dump(mode='json')


async def verify(baseline):
    server = await connect('postgres')
    await server.execute(f'CREATE DATABASE {DB} TEMPLATE template0')
    await server.close()
    admin = await connect()
    try:
        await migrate(admin, baseline / 'database')
        before = [dict(r) for r in await admin.fetch('SELECT * FROM ops.migration_checksum ORDER BY migration_key')]
        before_count = await admin.fetchval('SELECT count(*) FROM ops.schema_migration')
        save('LEDGER_BEFORE.json', before)
        check('v238_has_202_immutable_checksums', len(before) == 202)
        result = await migrate(admin, ROOT / 'database')
        after = [dict(r) for r in await admin.fetch('SELECT * FROM ops.migration_checksum ORDER BY migration_key')]
        save('LEDGER_AFTER.json', after)
        check('all_202_rows_byte_values_preserved', [r for r in after if r['migration_key'] != 'V239'] == before)
        check('exactly_one_checksum_and_registration_added', len(after) == 203 and
              await admin.fetchval('SELECT count(*) FROM ops.schema_migration') == before_count + 1)
        check('migration_replay_does_not_reapply', all(r['status'] == 'unchanged' for r in await migrate(admin, ROOT/'database')))
        role = 'salegent_verify_role_identity239'
        await admin.execute(f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS')
        for schema in ['platform','crm','activity','workflow','insight','ops','config','common','security','agent']:
            await admin.execute(f'GRANT USAGE ON SCHEMA {schema} TO {role}')
            await admin.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO {role}')
            await admin.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO {role}')
            await admin.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO {role}')
        await admin.execute(f'REVOKE INSERT,UPDATE,DELETE ON crm.customer_sales_member FROM {role}')
        await admin.fetchval('SELECT security.reconcile_runtime_grants()')
        actors = {}; workspaces=[]
        for wi in (1,2):
            ws, team = uuid4(), uuid4(); workspaces.append(ws)
            await admin.execute('INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,$2)', ws,f'identity-fixture-{wi}')
            await admin.execute("UPDATE control.tenant_module SET enabled=true,source='isolated-test-fixture' WHERE workspace_id=$1", ws)
            await admin.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'identity','Identity team')",team,ws)
            other_team=uuid4()
            await admin.execute("INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,'outside','Outside team')",other_team,ws)
            for code,r,scope in [('S','sales','self'),('O','operations','workspace'),('A','administrator','workspace'),('C','fde','self'),('T','sales','self')]:
                actor_team=other_team if code=='T' else team
                u=uuid4()
                await admin.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)',u,ws,code)
                await admin.execute('INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)',ws,u,r,scope,actor_team)
                await admin.execute('INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)',ws,u,actor_team,r)
                if r in ('operations', 'administrator'):
                    await admin.execute('INSERT INTO platform.password_credential(user_ref_id,workspace_id,password_hash,must_change_password) VALUES($1,$2,$3,false)',u,ws,encode_password('Isolated-Testing-2026'))
                rec=await admin.fetchrow('SELECT * FROM security.resolve_account_actor($1,$2,NULL)',f'identity-fixture-{wi}',code)
                assert rec, (wi,code)
                actors[f'{wi}{code}']=IdentityRepository._actor(rec).context

        @asynccontextmanager
        async def tx(code):
            check_window(); c=await connect()
            try:
                async with c.transaction():
                    await c.execute(f'SET LOCAL ROLE {role}')
                    await set_request_context(c,actors[code])
                    assert not await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    yield c
            finally: await c.close()

        mutation=CustomerMutationRepository(); requests=CustomerCreationRequestRepository()
        async with tx('1S') as c:
            check('sales_has_no_partner_manage', not await c.fetchval("SELECT security.authorization_has('partner.manage')"))
            drafts=[await requests.submit(c,actors['1S'],body('Identity '+label,roles)) for label,roles in
                    [('customer',['customer']),('partner',['partner']),('dual',['customer','partner'])]]
            check('application_only_creates_three_pending_requests', all(d['status']=='pending' for d in drafts))
        check('pending_does_not_create_enterprise_or_partner', await admin.fetchval('SELECT count(*) FROM crm.customer WHERE workspace_id=$1',workspaces[0])==0 and await admin.fetchval('SELECT count(*) FROM crm.partner WHERE workspace_id=$1',workspaces[0])==0)
        created=[]
        for d in drafts:
            async with tx('1O') as c:
                key=str(uuid4())
                async def action(): return await requests.decide(c,actors['1O'],d['id'],'approved',expected_version=1)
                saved=await execute_mutation(c,actors['1O'],key,'identity.verify.approve',{'id':d['id']},action)
                repeated=await execute_mutation(c,actors['1O'],key,'identity.verify.approve',{'id':d['id']},action)
                assert saved==repeated
                created.append(saved['customer'])
        check('approval_idempotency_returns_same_enterprise_and_profile',len(created)==3)
        ids=[c['id'] for c in created]; pure=created[1]; dual=created[2]
        check('approved_records_unclaimed', await admin.fetchval("SELECT count(*) FROM crm.customer_ownership WHERE customer_id=ANY($1::uuid[]) AND state='unclaimed'",ids)==3)
        check('two_profile_rows_no_double_enterprise', await admin.fetchval('SELECT count(*) FROM crm.customer WHERE workspace_id=$1',workspaces[0])==3 and await admin.fetchval('SELECT count(*) FROM crm.partner WHERE workspace_id=$1',workspaces[0])==2)
        async with tx('1S') as c:
            allowed=await c.fetchval("SELECT security.authorization_customer('customer.read',$1::uuid)",pure['id'])
            check('v203_same_team_read_matches_actual_permission_before_claim',allowed and await CustomerRepository().base(c,customer_id=pure['id']) is not None)
            for identity,total in [('all',3),('customer',2),('partner',2)]:
                page=await CustomerMemberRepository().claim_pool_page(c,business_identity=identity,limit=1)
                beyond=await CustomerMemberRepository().claim_pool_page(c,business_identity=identity,limit=1,offset=99)
                assert page['total']==total and beyond['total']==total and beyond['items']==[]
            check('identity_directory_count_before_pagination_and_empty_page')
            check('sales_still_cannot_manage_partner',not await c.fetchval("SELECT security.authorization_has('partner.manage')"))
        async with tx('1T') as c:
            check('outside_team_permission_negative',not await c.fetchval("SELECT security.authorization_customer('customer.read',$1::uuid)",pure['id']))
            check('outside_team_detail_remains_invisible', await CustomerRepository().base(c,customer_id=pure['id']) is None)
        # Same-name archive is never guessed. A failed approval rolls all side effects back.
        async with tx('1O') as c:
            p=await PartnerRepository().save(c,actors['1O'],{'name':'Identity collision','status':'active'})
        async with tx('1S') as c: req=await requests.submit(c,actors['1S'],body('Identity collision',['partner']))
        try:
            async with tx('1O') as c: await requests.decide(c,actors['1O'],req['id'],'approved',expected_version=1)
        except FileExistsError: pass
        else: raise AssertionError('same-name auto merge')
        check('failed_approval_remains_pending_without_partial_enterprise',await admin.fetchval('SELECT status FROM crm.customer_creation_request WHERE id=$1::uuid',req['id'])=='pending' and not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM crm.customer WHERE name='Identity collision')"))
        async with tx('1O') as c:
            linked=await requests.decide(c,actors['1O'],req['id'],'approved',expected_version=1,
                approved_payload=body('Identity collision',['partner'],partner_profile_id=str(p['id'])))
        check('explicit_existing_profile_links_exact_id',linked['customer']['partner_profile_id']==str(p['id']))
        # Global directory status is independent of enterprise identity removal.
        async with tx('1O') as c:
            version=await c.fetchval('SELECT version_no FROM crm.customer WHERE id=$1::uuid',dual['id'])
            await mutation.update(c,actors['1O'],customer_id=dual['id'],expected_version=version,data={'business_identities':['customer']})
            visible=await PartnerRepository().list(c,q='Identity dual')
            check('removing_partner_role_preserves_global_active_profile',visible['total']==1 and visible['items'][0]['status']=='active')
        # Backfilling self profile cannot make the associated external partner self-referential.
        try:
            async with tx('1O') as c:
                await mutation.create(c,actors['1O'],data=body('Identity self',['partner'],partner_profile_id=str(p['id']),partner_id=str(p['id'])))
        except ValueError: check('self_reference_rejected')
        else: raise AssertionError('self association accepted')
        # Cross-workspace explicit IDs never link, despite knowing a real UUID.
        async with tx('2O') as c: foreign=await PartnerRepository().save(c,actors['2O'],{'name':'Foreign','status':'active'})
        for key in ('partner_profile_id','partner_id'):
            try:
                async with tx('1O') as c: await mutation.create(c,actors['1O'],data=body('Cross '+key,['partner'],**{key:str(foreign['id'])}))
            except (ValueError,PermissionError): pass
            else: raise AssertionError('cross workspace accepted')
        check('foreign_profile_and_referral_ids_rejected')
        async with tx('1O') as c:
            inactive = await PartnerRepository().save(c,actors['1O'],{'name':'Inactive archive','status':'inactive'})
        try:
            async with tx('1O') as c:
                await mutation.create(c,actors['1O'],data=body('Inactive enterprise',['partner'],partner_profile_id=str(inactive['id'])))
        except ValueError: check('inactive_self_profile_rejected')
        else: raise AssertionError('inactive self profile accepted')
        try:
            async with tx('1O') as c:
                await requests.decide(c,actors['1O'],drafts[0]['id'],'approved',expected_version=1)
        except VersionConflict: check('repeat_approval_without_same_key_rejected')
        else: raise AssertionError('repeat approval accepted')
        # Force failure after enterprise, self profile and contact have been written.
        async with tx('1S') as c:
            rollback = await requests.submit(c,actors['1S'],body('Rollback complete graph',['partner']))
        class FailAfterContact:
            def __init__(self, inner): self.inner = inner
            def __getattr__(self, key): return getattr(self.inner,key)
            async def execute(self, query, *args):
                result = await self.inner.execute(query,*args)
                if 'INSERT INTO crm.contact' in query: raise RuntimeError('synthetic after-contact failure')
                return result
        try:
            async with tx('1O') as c:
                await requests.decide(FailAfterContact(c),actors['1O'],rollback['id'],'approved',expected_version=1)
        except RuntimeError as exc: assert str(exc)=='synthetic after-contact failure'
        else: raise AssertionError('synthetic failure missing')
        check('after_contact_failure_rolls_back_enterprise_profile_and_review',
            await admin.fetchval('SELECT status FROM crm.customer_creation_request WHERE id=$1::uuid',rollback['id'])=='pending'
            and not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM crm.customer WHERE name='Rollback complete graph')")
            and not await admin.fetchval("SELECT EXISTS(SELECT 1 FROM crm.partner WHERE name='Rollback complete graph')"))

        # Create-only grant can provision its new row, but cannot mutate an old row.
        async with tx('1A') as c:
            await AuthorizationRepository().save_account(c,actors['1C'].user_id,AccountAuthorizationSave(version_no=0,reason='synthetic create-only guard',overrides=[
                {'permission':p,'effect':'allow','scope':'workspace'} for p in ['customer.create','customer.read','partner.read']]+[
                {'permission':'customer.update','effect':'deny'},{'permission':'partner.manage','effect':'deny'}]).model_dump(mode='json'))
        async with tx('1A') as c:
            await AuthorizationRepository().save_account(c,actors['1T'].user_id,AccountAuthorizationSave(version_no=0,
                reason='synthetic directory-only visibility',overrides=[
                    {'permission':'customer.claim_directory','effect':'allow','scope':'workspace'}]).model_dump(mode='json'))
        async with tx('1T') as c:
            directory=await CustomerMemberRepository().claim_pool_page(c,business_identity='partner',limit=100)
            reference=next(r for r in directory['items'] if r['id']==pure['id'])
            check('directory_can_read_false_matches_denied_customer_detail',not reference['can_read_customer']
                and await CustomerRepository().base(c,customer_id=pure['id']) is None)
        async with tx('1C') as c:
            own=await mutation.create(c,actors['1C'],data=body('Create only',['partner']))
            check('create_only_provisions_only_its_own_new_enterprise',bool(own['partner_profile_id']))
        async with tx('1C') as c:
            try:
                async with c.transaction():
                    await c.fetchval('SELECT security.set_customer_business_identity($1::uuid,$2::text[],$3::uuid)',own['id'],['customer','partner'],own['partner_profile_id'])
            except asyncpg.InsufficientPrivilegeError: check('create_only_cannot_edit_own_old_row')
            else: raise AssertionError('create-only old row edit')
        # Match migrated V238 rows: NULL cannot turn denial into SQL UNKNOWN.
        legacy = uuid4()
        await admin.execute('''INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_team_id,
            created_by_user_ref_id,company_verified_by,company_verified_at,identity_created_xid)
            VALUES($1,$2,'Legacy null stamp','legacy null stamp',$3,$4,$4,clock_timestamp(),NULL)''',
            legacy,workspaces[0],await admin.fetchval('SELECT team_id FROM platform.team_membership WHERE user_ref_id=$1::uuid LIMIT 1',actors['1C'].user_id),actors['1C'].user_id)
        async with tx('1C') as c:
            try:
                async with c.transaction():
                    await c.fetchval('SELECT security.set_customer_business_identity($1::uuid,$2::text[],$3::uuid)',legacy,['partner'],None)
            except asyncpg.InsufficientPrivilegeError: check('create_only_null_legacy_marker_is_denied')
            else: raise AssertionError('NULL authorization bypass')
        # Updating an old row in the current tx does not confer creation authority.
        async with tx('1C') as c:
            await admin.execute('UPDATE crm.customer SET updated_at=clock_timestamp() WHERE id=$1::uuid',own['id'])
            try:
                async with c.transaction():
                    await c.fetchval('SELECT security.set_customer_business_identity($1::uuid,$2::text[],$3::uuid)',own['id'],['partner'],own['partner_profile_id'])
            except asyncpg.InsufficientPrivilegeError: check('current_transaction_timing_does_not_reauthorize_old_row')
            else: raise AssertionError('old row reauthorized by transaction time')
        # Current tx row update cannot forge immutable insertion marker.
        try:
            async with tx('1O') as c:
                await c.execute('UPDATE crm.customer SET identity_created_xid=pg_current_xact_id() WHERE id=$1::uuid',own['id'])
        except asyncpg.CheckViolationError: check('creation_marker_cannot_be_rewritten')
        else: raise AssertionError('creation stamp changed')
        # Concurrent approvals selecting one independent archive cannot bind twice.
        async with tx('1O') as c: shared=await PartnerRepository().save(c,actors['1O'],{'name':'Concurrent archive','status':'active'})
        async with tx('1S') as c:
            parallel=[await requests.submit(c,actors['1S'],body('Concurrent '+str(i),['partner'],partner_profile_id=str(shared['id']))) for i in (1,2)]
        async def approve(d):
            try:
                async with tx('1O') as c: return await requests.decide(c,actors['1O'],d['id'],'approved',expected_version=1)
            except FileExistsError: return 'conflict'
        outcomes=await asyncio.gather(*(approve(d) for d in parallel))
        check('concurrent_self_binding_has_one_success_one_rollback',sum(isinstance(x,dict) for x in outcomes)==1 and outcomes.count('conflict')==1)
        check('concurrent_loser_remains_pending',await admin.fetchval(
            "SELECT count(*) FROM crm.customer_creation_request WHERE id=ANY($1::uuid[]) AND status='pending'",[d['id'] for d in parallel])==1)
        async with tx('1O') as c:
            before = dict(await c.fetchrow('SELECT business_identities,partner_profile_id,primary_partner_id,version_no FROM crm.customer WHERE id=$1::uuid',pure['id']))
            await mutation.update(c,actors['1O'],customer_id=pure['id'],expected_version=before['version_no'],data={'contact_title':'Updated role'})
            after = dict(await c.fetchrow('SELECT business_identities,partner_profile_id,primary_partner_id FROM crm.customer WHERE id=$1::uuid',pure['id']))
            check('legacy_update_omission_preserves_identity_and_stable_links',after=={k:v for k,v in before.items() if k!='version_no'})
            for identity in ('all','customer','partner'):
                first = await OperationsCustomerRepository().list(c,business_identity=identity,limit=1)
                empty = await OperationsCustomerRepository().list(c,business_identity=identity,limit=1,offset=999)
                assert first['total']==empty['total'] and first['total']>0 and empty['items']==[]
            check('console_identity_filter_total_survives_empty_page')
        # Ordinary claims preserve existing lifecycle; pure partners stay out of customer analyses.
        for cid in ids:
            async with tx('1S') as c: claim=await c.fetchval('SELECT security.claim_customer($1::uuid)',cid)
            async with tx('1O') as c: await c.fetchval("SELECT security.review_customer_claim($1::uuid,'approved','synthetic')",claim['request_id'])
        async with tx('1S') as c:
            allrows=await CustomerRepository().list(c,query=None,level=None,unassigned=None,limit=100)
            check('claimed_pure_partner_visible_in_enterprise_list',pure['id'] in {r['id'] for r in allrows})
            check('pure_partner_detail_not_applicable',(await CustomerRepository().detail(c,customer_id=pure['id']))['profile']['scope']=='not_applicable')
            portfolio=await CustomerPortfolioRepository().read(c,actors['1S'],CustomerPortfolioQuery(scope='self'))
            check('customer_portfolio_excludes_pure_partner',portfolio['summary']['claimed']==2 and pure['id'] not in {str(r['id']) for r in portfolio['items']})
            points=await CustomerMapRepository().read(c,activity_window='all')
            check('customer_map_excludes_pure_partner',pure['id'] not in {r['id'] for r in points})
        save('RESULT.json',{'status':'passed','checks':checks,'migration_results':result,'before_registered':before_count,
            'after_registered':before_count+1,'source_hashes':json.loads((DELIVERY/'INPUTS.json').read_text()),
            'no_model_calls':True,'no_shared_runtime_access':True})
    finally: await admin.close()


def main():
    check_window()
    assert not CLUSTER.exists(), 'Refuse to reuse any existing cluster'
    sock=socket.socket(); sock.bind(('127.0.0.1',PORT)); sock.close()
    DELIVERY.mkdir(parents=True, exist_ok=True)
    assert not (DELIVERY/'INPUTS.json').exists(), 'Evidence path is immutable'
    baseline=DELIVERY/'baseline-source';baseline.mkdir()
    archive=subprocess.run(['git','archive',BASE,'database'],cwd=ROOT,capture_output=True,check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tf: tf.extractall(baseline,filter='data')
    changed=subprocess.run(['git','diff','--name-only'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.splitlines()
    changed += subprocess.run(['git','ls-files','--others','--exclude-standard'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.splitlines()
    tracked={rel:sha(ROOT/rel) for rel in changed if (ROOT/rel).is_file()}
    tracked.update({str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),ROOT/'database/migrations/V239__enterprise_business_identities.sql',ROOT/'backend/src/sales_backend/domain/customer_identity.py',ROOT/'backend/src/sales_backend/repositories/customer_identity.py']})
    save('INPUTS.json',{'baseline':BASE,'baseline_archive_sha256':hashlib.sha256(archive).hexdigest(),'files':tracked,'authorization_sha256':sha(AUTH)})
    for rel in tracked:
        frozen=DELIVERY/'source-snapshot'/rel; frozen.parent.mkdir(parents=True,exist_ok=True); frozen.write_bytes((ROOT/rel).read_bytes())
    CLUSTER.mkdir(mode=0o700);SOCKET.mkdir(mode=0o700)
    started=False
    try:
        with (DELIVERY/'initdb.stdout').open('x') as f:
            subprocess.run([str(PG/'initdb'),'-D',str(CLUSTER/'data'),'-U',OWNER,'--auth-local=trust','--auth-host=reject','--encoding=UTF8','--locale=C'],stdout=f,stderr=subprocess.STDOUT,check=True)
        subprocess.run([str(PG/'pg_ctl'),'-D',str(CLUSTER/'data'),'-l',str(CLUSTER/'postgres.log'),'-o',f"-k {SOCKET} -p {PORT} -c listen_addresses=''",'-w','start'],check=True)
        started=True
        asyncio.run(verify(baseline))
    except BaseException as exc:
        save('FAILURE.json',{'type':type(exc).__name__,'message':str(exc),'passed_checks':checks})
        raise
    finally:
        if started:
            result=subprocess.run([str(PG/'pg_ctl'),'-D',str(CLUSTER/'data'),'-m','fast','-w','stop'],capture_output=True,text=True)
            save('STOP.json',{'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
            if result.returncode: raise RuntimeError('Isolated cluster stop failed')

if __name__=='__main__': main()
