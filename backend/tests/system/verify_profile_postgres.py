"""Called by the shared non-bypass-RLS fixture; no production business data is used."""
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sales_backend.contracts.models import CustomerCreate
from sales_backend.repositories.customer_mutations import CustomerMutationRepository
from sales_backend.repositories.customers import CustomerRepository
from sales_backend.repositories.customer_assets import CustomerAssetRepository, today
from sales_backend.services.idempotency import execute_mutation
from sales_backend.services.profile_performance import performance, quarter_period
from sales_backend.contracts.targets import TargetBatchSave
from sales_backend.repositories.profile_performance import ProfilePerformanceRepository
from sales_backend.services.targets import save_target_batch


async def verify_profiles(conn, as_actor, actors, customers, opportunities):
    checks=[];year=today().year
    current=quarter_period(year,(today().month-1)//3+1)
    create=CustomerCreate(name='新等级客户',customer_type='潜在客户',level_code='Tier-2',source='销售自拓',
                          target_team='南区',contact_name='林经理',contact_title='采购经理',contact_role='影响者')
    async with as_actor('A') as c:
        result=await CustomerMutationRepository().create(c,actors['A'],data=create.model_dump())
        detail=await CustomerRepository().detail(c,customer_id=result['id']);assert detail['level_code']=='Tier-2'
        await CustomerMutationRepository().update(c,actors['A'],customer_id=result['id'],data={'level_code':'Tier-1'})
        listed=await CustomerRepository().list(c,query=None,level='Tier-1',unassigned=None,limit=None)
        assert result['id'] in {v['id'] for v in listed}
    checks.append('customer_tier_create_update_filter_round_trip')
    def body(amount, **kw):
        return TargetBatchSave(scope='self',period_type='quarter',anchor_date=current['start'],
            reason='与负责人确认的季度目标',items=[{'kind':'recognized','amount':amount,**kw}])
    for code,amount in [('A',1000),('B',2000),('S',3000),('M',6000)]:
        async with as_actor(code) as c:
            result=await save_target_batch(c,actors[code],body(amount))
            assert result['status']=='effective'
    for code,kwargs,amount in [('A',{},1000),('M',dict(scope='person',member_id=actors['B'].user_id),2000),
                             ('S',{},3000),('M',{},6000)]:
        async with as_actor(code) as c:
            result=await performance(c,actors[code],**kwargs)
            assert result['targets']['recognized']==amount
            assert result['target_period']['start']==current['start']
            assert (await performance(c,actors[code],year=year+1,**kwargs))['targets']=={}
    checks.append('quarter_targets_do_not_copy_annual_or_other_quarter_values')
    for code,kwargs in [('A',dict(scope='person',member_id=actors['B'].user_id)),
                       ('M',dict(scope='team',team='南区')),('S',dict(scope='person',member_id=actors['A'].user_id))]:
        try:
            async with as_actor(code) as c:
                await ProfilePerformanceRepository().scope(c,actors[code],write=True,**kwargs)
            raise AssertionError('another subject target marked writable')
        except PermissionError: pass
    for code,kwargs in [('A',dict(scope='person',member_id=actors['B'].user_id)),
                       ('S',dict(scope='person',member_id=actors['C'].user_id))]:
        try:
            async with as_actor(code) as c: await performance(c,actors[code],**kwargs)
            raise AssertionError('foreign profile read')
        except PermissionError: pass
    async with as_actor('A') as c:
        assert await c.fetchval('SELECT count(*) FROM crm.sales_target')==1
        assert await c.execute('UPDATE crm.sales_target SET amount=1 WHERE user_ref_id=$1::uuid',actors['B'].user_id)=='UPDATE 0'
    checks.append('quarter_target_view_and_write_permissions_are_distinct')
    key=uuid4();data=body(1500,version_no=1);request_id=None
    for _ in range(2):
        async with as_actor('A') as c:
            replay=await execute_mutation(c,actors['A'],key,'target.batch.save',data.model_dump(),
                                         lambda:save_target_batch(c,actors['A'],data))
            assert replay['status']=='pending'
            if request_id: assert replay['request']['id']==request_id
            request_id=replay['request']['id']
            assert (await performance(c,actors['A']))['targets']['recognized']==1000
    assert await conn.fetchval('SELECT version_no FROM crm.sales_target WHERE user_ref_id=$1::uuid',actors['A'].user_id)==1
    checks.append('quarter_target_change_replays_one_request_without_changing_effective_target')
    # Same customer has two owners; only the opportunity's owner receives its actuals.
    async with as_actor('M') as c:
        for code,amount,occurred in [('A',100,date(year-1,5,1)),('A',80,current['start']),('B',600,current['start'])]:
            await CustomerAssetRepository().create(c,actors['M'],dict(customer_id=UUID(customers[0]),
                opportunity_id=UUID(opportunities[code]['id']),kind='recognized',amount=Decimal(amount),
                occurred_on=occurred,source_ref=uuid4().hex,note='隔离测试',request_id=uuid4()))
    for code,kwargs,amount in [('A',{},80),('B',{},600),('S',dict(scope='team',team='南区'),680),
                             ('M',dict(scope='department'),680),('M',dict(scope='person',account_code='B'),600)]:
        async with as_actor(code) as c:
            result=await performance(c,actors[code],**kwargs)
            assert result['actuals']['recognized']==amount, (code,result)
            assert result['retention']['rate']==(None if code=='B' or kwargs.get('account_code')=='B' else
                                                Decimal(80) if code=='A' else Decimal(680))
            if code=='A':
                assert result['active_opportunity_amount']==10000
                assert result['efficiency']['customers']['denominator']>=0
            assert result['provenance']['efficiency_formula'].endswith('_v2')
    checks.append('profile_actuals_and_retention_follow_selected_scope_without_shared_customer_duplication')
    async with as_actor('M') as c:
        assets = CustomerAssetRepository()
        for code, amount in [('A', 80), ('B', 600)]:
            result = await assets.read(c, owner_id=UUID(actors[code].user_id))
            assert result['summary']['recognized_amount'] == amount
            assert result['total'] == len(result['items']) == 1
            owner_name = await c.fetchval("SELECT display_name FROM platform.user_ref WHERE id=$1::uuid", actors[code].user_id)
            assert result['items'][0]['owner_name'] == owner_name
        result = await assets.read(c)
        assert result['summary']['recognized_amount'] == 680
        assert result['total'] == len(result['items']) == 1
        assert not result['has_more']
    checks.append('asset_filters_follow_opportunity_owners_and_shared_customer_pagination_stays_unique')
    # CustomerAsset.read without a customer returns aggregate rows, not dated entries.
    # Profile now computes the cohort in SQL and never feeds those aggregates into a date-based client calculation.
    return checks
