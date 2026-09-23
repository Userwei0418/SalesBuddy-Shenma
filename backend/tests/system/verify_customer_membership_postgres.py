"""Customer claims and own-opportunity RLS exercised using a non-superuser role."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import uuid
import asyncpg

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'backend/src'),str(ROOT/'database/scripts')]
from migrate import migrate
from verify_jobs_postgres import settings
from verify_profile_postgres import verify_profiles
from sales_backend.db import Database
from sales_backend.domain.agent import ActorContext
from sales_backend.repositories.customer_members import CustomerMemberRepository
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.services.opportunities import save_opportunity
from sales_backend.repositories.opportunities import opportunity_name_available
from sales_backend.repositories.notifications import NotificationRepository
from sales_backend.services.customer_claims import claim_customer


async def main():
    config={'host':os.environ.get('PGHOST','/tmp'),'user':os.environ.get('PGUSER','postgres')}
    admin=await asyncpg.connect(database='postgres',**config)
    suffix=uuid.uuid4().hex[:12];name='salegent_verify_claims_'+suffix;role='salegent_verify_role_'+suffix
    conn=db=None;checks=[];role_created=False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0')
        conn=await asyncpg.connect(database=name,**config);await migrate(conn)
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS');role_created=True
        schemas=['platform','crm','activity','workflow','insight','ops','config','common','security','agent']
        for schema in schemas:
            await conn.execute(f'GRANT USAGE ON SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA {schema} TO "{role}"')
            await conn.execute(f'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {schema} TO "{role}"')
        # Claims must go through the checked function, never arbitrary member INSERT.
        await conn.execute(f'REVOKE INSERT,UPDATE,DELETE ON crm.customer_sales_member FROM "{role}"')
        workspace=uuid.uuid4()
        await conn.execute("INSERT INTO platform.workspace(id,external_workspace_id,name) VALUES($1,$2,'权限测试')",workspace,str(workspace))
        teams=[]
        for label in ['南区','北区']:
            team=uuid.uuid4();teams.append(team)
            await conn.execute('INSERT INTO platform.team(id,workspace_id,code,name) VALUES($1,$2,$3,$3)',team,workspace,label)
        actors={}
        for code,r,t in [('A','sales',teams[0]),('B','sales',teams[0]),('C','sales',teams[1]),('M','manager',teams[0]),('S','supervisor',teams[0]),('T','supervisor',teams[1])]:
            user=uuid.uuid4()
            await conn.execute('INSERT INTO platform.user_ref(id,workspace_id,external_user_id,display_name,account_code) VALUES($1,$2,$3,$3,$3)',user,workspace,code)
            await conn.execute('INSERT INTO platform.role_binding(workspace_id,user_ref_id,role_code,data_scope_code,team_id) VALUES($1,$2,$3,$4,$5)',workspace,user,r,'workspace' if r=='manager' else 'team' if r=='supervisor' else 'self',t)
            await conn.execute('INSERT INTO platform.team_membership(workspace_id,user_ref_id,team_id,membership_role) VALUES($1,$2,$3,$4)',workspace,user,t,r)
            actors[code]=ActorContext(workspace_id=str(workspace),user_id=str(user),role=r,data_scope='workspace' if r=='manager' else 'team' if r=='supervisor' else 'self',team_ids=(str(t),))
        customers=[]
        for label,owner in [('共享客户','A'),('第二客户','A'),('外部门客户','C')]:
            cid=uuid.uuid4();customers.append(str(cid));a=actors[owner]
            await conn.execute('''INSERT INTO crm.customer(id,workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,created_by_user_ref_id)
                VALUES($1,$2,$3,$3,$4::uuid,$5::uuid,$4::uuid)''',cid,workspace,label,a.user_id,a.team_ids[0])
        db=Database(settings(name));await db.connect()
        @asynccontextmanager
        async def as_actor(code):
            async with db.transaction(actors[code]) as c:
                await c.execute(f'SET LOCAL ROLE "{role}"')
                assert await c.fetchval('SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname=current_user') is False
                yield c
        member=CustomerMemberRepository();customer_repo=CustomerRepository()
        async with as_actor('B') as c:
            assert await customer_repo.list(c,query=None,level=None,unassigned=None,limit=None)==[]
            pool=await member.claim_pool(c)
            assert {r['id'] for r in pool}==set(customers[:2]) and all('opportunity_amount' not in r for r in pool)
            first=await claim_customer(c,actors['B'],customers[0]);assert first['added']
            assert not (await claim_customer(c,actors['B'],customers[0]))['added']
            assert (await claim_customer(c,actors['B'],customers[1]))['added']
        assert await conn.fetchval('SELECT count(*) FROM crm.customer_sales_member WHERE customer_id=$1::uuid',customers[0])==2
        assert str(await conn.fetchval('SELECT owner_user_ref_id FROM crm.customer WHERE id=$1::uuid',customers[0]))==actors['A'].user_id
        checks.append('claims_add_multiple_customers_without_replacing_existing_sales')
        for code,target in [('B',customers[2]),('C',customers[0])]:
            try:
                async with as_actor(code) as c: await claim_customer(c,actors[code],target)
                raise AssertionError('out of department claim accepted')
            except PermissionError: pass
        checks.append('out_of_department_claim_rejected_by_database_function')
        opportunities={}
        for code,amount in [('A',Decimal(10000)),('B',Decimal(25000))]:
            async with as_actor(code) as c:
                opportunities[code]=await save_opportunity(c,actors[code],customer_id=customers[0],data=dict(
                    name='商机'+code,amount=amount,probability=30,status='open',expected_close_date=date(2026,12,1),quarterly_forecasts=[]))
        for code,expected_amount in [('A',10000),('B',25000),('M',35000),('S',35000)]:
            async with as_actor(code) as c:
                detail=await customer_repo.detail(c,customer_id=customers[0])
                ids={o['id'] for o in detail['opportunities']}
                assert ids==({opportunities[code]['id']} if code in ('A','B') else {o['id'] for o in opportunities.values()})
                rows=await customer_repo.list(c,query=None,level=None,unassigned=None,limit=None)
                row=next(r for r in rows if r['id']==customers[0]);assert int(row['opportunity_amount'])==expected_amount
                assert set(m['name'] for m in row['sales_members'])=={'A','B'}
        checks.append('shared_customer_details_and_amounts_only_contain_own_opportunities_for_sales')
        async with as_actor('B') as c:
            assert await c.fetchval('SELECT id FROM crm.opportunity WHERE id=$1::uuid',opportunities['A']['id']) is None
            try:
                await save_opportunity(c,actors['B'],customer_id=customers[0],data={**opportunities['A'],'action':'update','opportunity_id':opportunities['A']['id']})
                raise AssertionError('foreign opportunity update accepted')
            except LookupError: pass
            notices=await NotificationRepository().list(c,unread_only=False,limit=100)
            assert any(n['object_id']==opportunities['B']['id'] for n in notices)
            assert not any(n['object_id']==opportunities['A']['id'] for n in notices)
        async with as_actor('B') as c:
            assert not await opportunity_name_available(c,customers[0],'商机A')
        checks.append('direct_id_update_and_notification_do_not_leak_colleague_opportunities')
        checks.append('customer_name_collision_check_covers_hidden_opportunities_without_returning_details')
        # Old cached map conclusions are scoped to their author/subject, not the shared customer.
        rule=await conn.fetchval("SELECT id FROM config.rule_set WHERE rule_code='customer_quadrant' LIMIT 1")
        for code,score in [('A',90),('B',40)]:
            await conn.execute('''INSERT INTO insight.quadrant_score(workspace_id,customer_id,subject_user_ref_id,potential_score,relationship_score,quadrant_code,rule_set_id,input_snapshot)
                VALUES($1,$2::uuid,$3::uuid,$4,$4,'main_attack',$5,'{}')''',workspace,customers[0],actors[code].user_id,score,rule)
        async with as_actor('B') as c:
            detail=await customer_repo.detail(c,customer_id=customers[0]);assert int(detail['potential_score'])==40
            assert await c.fetchval('SELECT count(*) FROM insight.quadrant_score WHERE customer_id=$1::uuid',customers[0])==1
        checks.append('customer_map_cached_ai_conclusions_are_scoped_to_the_salesperson')
        async with as_actor('M') as c:
            assigned=await CustomerMutationRepository().assign(c,actors['M'],customer_id=customers[0],
                assignee_account_code='C',first_action='共同维护客户',due_at=None)
            assert assigned['sales_account_code']=='C'
        async with as_actor('C') as c:
            assert await customer_repo.detail(c,customer_id=customers[0]) is not None
            created=await save_opportunity(c,actors['C'],customer_id=customers[0],data=dict(name='商机C',amount=Decimal(5000),
                probability=10,status='open',expected_close_date=date(2026,12,1),quarterly_forecasts=[]))
        async with as_actor('T') as c:
            detail=await customer_repo.detail(c,customer_id=customers[0])
            assert {o['id'] for o in detail['opportunities']}=={created['id']}
            again=await CustomerMutationRepository().assign(c,actors['T'],customer_id=customers[0],
                assignee_account_code='C',first_action='再次添加同一销售',due_at=None)
            assert again['already_member']
        assert await conn.fetchval('SELECT count(*) FROM crm.customer_sales_member WHERE customer_id=$1::uuid',customers[0])==3
        checks.append('manager_can_add_cross_team_member_while_supervisors_only_see_own_team_opportunities')
        checks.extend(await verify_profiles(conn,as_actor,actors,customers,opportunities))
        print(json.dumps({'passed':len(checks),'checks':checks}))
    finally:
        if db: await db.close()
        if conn: await conn.close()
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if role_created: await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()

if __name__=='__main__':asyncio.run(main())
