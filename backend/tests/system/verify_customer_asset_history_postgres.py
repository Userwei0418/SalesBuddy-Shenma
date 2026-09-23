"""Disposable PostgreSQL: historical assets remain separate, scoped and paginated."""
import asyncio
import json
from datetime import date
from pathlib import Path
import sys
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'backend/src'), str(ROOT / 'database/tests')]
from run_integration_postgres import main
from verify_fde_postgres import context
from sales_backend.repositories.customer_assets import CustomerAssetRepository


async def verify(config, name, role):
    c = await asyncpg.connect(database=name, **config)
    for t in ('json', 'jsonb'):
        await c.set_type_codec(t, schema='pg_catalog', encoder=json.dumps, decoder=json.loads)
    checks = []
    try:
        ws = await c.fetchval("SELECT id FROM platform.workspace WHERE external_workspace_id='demo-sales-workspace'")
        users = {r['account_code']: r['id'] for r in await c.fetch('SELECT id,account_code FROM platform.user_ref WHERE workspace_id=$1', ws)}
        team = await c.fetchval('SELECT id FROM platform.team WHERE workspace_id=$1 LIMIT 1', ws)
        year = date.today().year
        customers, opportunities = [], []
        await context(c, ws, users['ZJL001'], 'manager')
        batch = await c.fetchval("INSERT INTO ops.crm_import_batch(workspace_id,source_system,source_base_id,manifest_sha256,source_snapshot_at) VALUES($1,'synthetic','assets',repeat('a',64),clock_timestamp()) RETURNING id", ws)
        for i, owner in enumerate((users['XS001'], users['XS002'])):
            cid = await c.fetchval("INSERT INTO crm.customer(workspace_id,name,normalized_name,owner_user_ref_id,owner_team_id,created_by_user_ref_id) VALUES($1,$2,$2,$3,$4,$3) RETURNING id", ws, f'Asset synthetic {i}', owner, team)
            customers.append(cid)
            oid = await c.fetchval("INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount) VALUES($1,$2,$3,$4,$5,$6) RETURNING id", ws,cid,f'Asset opportunity {i}',owner,team,100*(i+1))
            opportunities.append(oid)
        await c.execute("INSERT INTO crm.opportunity(workspace_id,customer_id,name,owner_user_ref_id,owner_team_id,amount) VALUES($1,$2,'Unknown ACV',$3,$4,NULL)",ws,customers[0],users['XS001'],team)
        async def snapshot(oid, yr, kind, amount, unit, source):
            rid = await c.fetchval("INSERT INTO ops.crm_import_record(workspace_id,batch_id,source_table_id,source_record_id,object_kind,source_sha256,raw_fields,status) VALUES($1,$2,'synthetic',$3,'period_actual_snapshot',repeat('b',64),'{}','approved') RETURNING id",ws,batch,str(uuid4()))
            await c.execute("INSERT INTO crm.opportunity_period_actual_snapshot(workspace_id,opportunity_id,year,quarter,kind,source_field,raw_amount,source_unit,tax_basis,source_record_id,import_batch_id) VALUES($1,$2,$3,1,$4,$5,$6,$7,'unknown',$8,$9)",ws,oid,yr,kind,source,amount,unit,rid,batch)
        await snapshot(opportunities[0],year,'recognized',3,'wan_cny','Q1原值')
        await snapshot(opportunities[1],year,'recognized',200,'cny','Q1原值')
        await snapshot(opportunities[0],year-1,'recognized',1,'wan_cny','上年原值')
        await snapshot(opportunities[0],year+1,'recognized',999,'wan_cny','未来原值')
        await snapshot(opportunities[0],year,'collection',0,'wan_cny','Q1回款')
        await c.execute("INSERT INTO crm.customer_actual(workspace_id,customer_id,opportunity_id,kind,amount,occurred_on,source_ref,request_id,confirmed_by_user_ref_id) VALUES($1,$2,$3,'recognized',500,current_date,'same quarter entry',$4,$5)",ws,customers[0],opportunities[0],uuid4(),users['ZJL001'])
        before = await c.fetchval('SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM crm.opportunity_period_actual_snapshot s')
        await c.execute(f'SET ROLE "{role}"')
        assert not await c.fetchval('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')
        repo = CustomerAssetRepository()
        entries = await repo.read(c)
        assert entries['basis']=='entries' and entries['summary']['recognized_amount']==500
        assert entries['summary']['acv_amount']==300 and entries['summary']['unknown_acv_count']==1
        checks.append('empty_acv_counts_zero_without_overwriting_source')
        history = await repo.read(c,basis='auto',limit=1)
        assert history['basis']=='historical' and history['summary']['recognized_amount']==30200
        assert history['summary']['collection_amount']==0 and history['summary']['entry_count']==3
        assert history['total']==2 and history['has_more'] and len(history['items'])==1
        second = await repo.read(c,basis='historical',limit=1,offset=1)
        assert not second['has_more'] and history['items'][0]['customer_id']!=second['items'][0]['customer_id']
        checks.append('historical_and_same_quarter_actual_never_added_together_and_paging_preserves_total')
        detail = await repo.read(c,basis='historical',customer_id=customers[0],kind='recognized')
        row=detail['items'][0]
        assert detail['total']==1 and row['amount']==30000 and row['occurred_on'] is None
        assert row['period_label']==f'{year} Q1' and row['tax_basis']=='unknown' and row['source_ref']=='Q1原值'
        checks.append('source_unit_quarter_unknown_tax_preserved_without_fabricated_date')
        cumulative = await repo.read(c,basis='historical',period='all',kind='recognized')
        assert cumulative['summary']['recognized_amount']==40200 and cumulative['summary']['entry_count']==3
        checks.append('year_and_all_filter_original_year_and_exclude_future_quarters')
        filtered = await repo.read(c,basis='historical',owner_id=users['XS002'])
        assert filtered['summary']['recognized_amount']==200
        empty = await repo.read(c,basis='auto',customer_ids=[])
        assert empty['basis']=='entries' and empty['total']==0 and empty['summary']['acv_amount']==0
        checks.append('owner_and_fde_empty_customer_scope_applied_to_both_sources')
        wrong_team = await repo.read(c,basis='historical',team_id=uuid4())
        assert wrong_team['total']==0
        checks.append('team_filter_cannot_return_unrelated_historical_values')
        await context(c,ws,users['XS001'],'sales')
        own=await repo.read(c,basis='historical')
        assert own['summary']['recognized_amount']==30000 and own['total']==1
        denied=await repo.read(c,basis='historical',customer_id=customers[1])
        assert denied['total']==0
        checks.append('sales_rls_and_explicit_customer_filter_cannot_read_other_owner')
        await context(c,uuid4(),users['XS001'],'manager')
        foreign=await repo.read(c,basis='historical')
        assert foreign['total']==0 and foreign['summary']['recognized_amount'] is None
        checks.append('cross_workspace_context_cannot_read_snapshots')
        await context(c,ws,users['ZJL001'],'manager')
        after=await c.fetchval('SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM crm.opportunity_period_actual_snapshot s')
        assert before==after
        assert await c.fetchval('SELECT count(*) FROM crm.opportunity WHERE workspace_id=$1 AND amount IS NULL',ws)==1
        checks.append('all_reads_leave_business_original_values_unchanged')
        print(json.dumps({'passed':len(checks),'checks':checks}))
    finally:
        await c.close()


if __name__=='__main__':
    asyncio.run(main(serve=verify))
