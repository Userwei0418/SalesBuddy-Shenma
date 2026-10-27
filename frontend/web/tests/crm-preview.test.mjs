import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {mkdtemp, writeFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createSalesWebServer} from '../server.mjs';
import {readLocalPreview} from '../local-preview.mjs';

// Fictional test records only. The actual workbook/dataset is never a fixture.
function fixture() {
  const source = {source_kind:'crm_export',source_ref:{row:2}};
  const customers = Array.from({length:3},(_,i)=>({id:'c'+i,name:'Fixture '+i,owner_id:'person',created_at:'2026-09-01T00:00:00Z',...source}));
  const opportunities = customers.map((c,i)=>({id:'o'+i,customer_id:c.id,customer_name:c.name,name:'Fixture op '+i,owner_id:'person',owner_name:'Fixture owner',amount:10000,expected_close_date:null,source_close_quarter:'Q3',status:'open',stage_code:'qualified',probability:30,fde_member_ids:[],...source}));
  const visits = opportunities.map((o,i)=>({id:'v'+i,customer_id:o.customer_id,opportunity_id:o.id,customer_type:['客户','伙伴',null][i],...source}));
  return {format:'sales-crm-preview-v1',dataset_id:'test-v1',source_kind:'crm_export',actors:[{user_id:'person',display_name:'Fixture owner',role:'sales',team_ids:['00000003-0000-4000-8000-000000000001'],team_names:['Fixture group']}],role_bindings:{sales:'person'},state:{customers,opportunities,visits,
    ...Object.fromEntries(['contacts','tasks','actuals','notifications','claims','assignments','opportunityEvents','risks'].map(k=>[k,[]])),
    ...Object.fromEntries(['targets','conversations','runs','advice','idempotency'].map(k=>[k,{}])),serial:100}};
}
function workspace(data=fixture(), storage=new Map()) {
  let fetches=0;
  const window = {SALES_MODE:'preview',setTimeout,clearTimeout,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
    fetch:async url=>{fetches++; assert.equal(url,'/local-preview-data');return {ok:true,status:200,json:async()=>data};}};
  vm.runInNewContext(readFileSync(new URL('../preview-api.js',import.meta.url),'utf8'),{window,URL,URLSearchParams});
  const api = path=>new Promise(resolve=>window.SalesPreview.request({url:'/api/v1'+path,header:{Authorization:'Bearer preview-access-manager'},success:resolve}));
  return {window,api,storage,fetches:()=>fetches};
}
test('CRM replaces the local seed, isolates saved data and preserves partner/unknown fields',async()=>{
  const storage=new Map([['sales-web:preview-workspace:v1','synthetic-sentinel']]);
  const w=workspace(fixture(),storage); await w.window.SalesPreview.loadLocal();
  const ops=await w.api('/opportunities?year=2026&include_closed=true');
  assert.equal(ops.statusCode,200);assert.equal(ops.data.items.length,3);
  assert.equal(ops.data.items[0].expected_close_date,null);assert.equal(ops.data.items[0].actuals.collection_amount,null);
  assert.match(ops.data.source_label,/CRM 真实样本/);
  assert.deepEqual(Array.from((await w.api('/visits')).data.items,v=>v.customer_type),['客户','伙伴',null]);
  assert.equal((await w.api('/opportunities?year=2026&quarters=3&include_closed=true')).data.items.length,0,'unknown year/date cannot enter a precise period');
  assert.equal((await w.api('/opportunities?close_to=2026-12-31')).data.items.length,0);
  assert.equal((await w.api('/opportunities/overview?year=2026')).data.metrics.total,3);
  assert.equal((await w.api('/customer-assets')).data.summary.recognized_amount,null);
  assert.equal((await w.api('/tasks')).data.items.length,0);
  assert.equal((await w.api('/directory/partners')).data.items.length,0);
  w.window.SalesPreview.reset();
  assert.equal(storage.get('sales-web:preview-workspace:v1'),'synthetic-sentinel');
  assert.equal(JSON.parse(storage.get('sales-web:crm-preview:test-v1')).opportunities.length,3);
  const refreshed=workspace(fixture(),storage);await refreshed.window.SalesPreview.loadLocal();
  assert.equal((await refreshed.api('/customers')).data.items.length,3);
});
test('portfolio preview counts customers once, keeps inactive pipeline, and returns full map outside pagination', async () => {
  const data = fixture(), day = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10), year = Number(day.slice(0, 4));
  Object.assign(data.state.customers[0], {potential_score: 80, relationship_score: 75});
  data.state.opportunities[0].probability = 10;
  data.state.opportunities.push({...data.state.opportunities[0], id: 'higher', probability: 50});
  data.state.opportunities[1].status = 'won'; data.state.opportunities[1].probability = 100;
  data.state.opportunities[2].status = 'lost';
  data.state.visits.forEach((row, i) => Object.assign(row, {recorder_id: 'person', status: i === 2 ? 'draft' : 'confirmed', visit_date: i === 0 ? `${year - 1}-12-01` : day}));
  const w = workspace(data); await w.window.SalesPreview.loadLocal();
  const query = `scope=team&activity_start=${year}-01-01&activity_end=${day}`;
  const pipeline = await w.api('/customers/portfolio?' + query + '&segment=pipeline&min_probability=30');
  assert.equal(pipeline.statusCode, 200, JSON.stringify(pipeline.data));
  assert.equal(pipeline.data.total, 1); assert.equal(pipeline.data.items[0].id, 'c0');
  assert.equal(pipeline.data.items[0].is_active, false); assert.equal(pipeline.data.items[0].max_open_probability, 50);
  assert.equal(pipeline.data.summary.pipeline_ge_10, 1); assert.equal(pipeline.data.summary.pipeline_ge_50, 1);
  assert.equal(pipeline.data.summary.pipeline_ge_70, 0); assert.equal(pipeline.data.summary.active, 1);
  assert.equal(pipeline.data.summary.won, 1);
  const active = await w.api('/customers/portfolio?' + query + '&segment=active');
  assert.deepEqual(Array.from(active.data.items, row => row.id), ['c1']);
  const paged = await w.api('/customers/portfolio?' + query + '&segment=claimed&offset=1&limit=1');
  assert.equal(paged.data.total, 3); assert.equal(paged.data.items.length, 1);
  assert.equal(paged.data.map_points.length, 1); assert.equal(paged.data.unscored_count, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(paged.data.summary)), JSON.parse(JSON.stringify(pipeline.data.summary)));
});
test('portfolio distinguishes missing amounts and unknown plans from zero and explicit future plans', async () => {
  const data = fixture(), year = new Date(Date.now() + 8 * 3600000).getUTCFullYear();
  Object.assign(data.state.opportunities[0], {amount: null, expected_close_year: year});
  Object.assign(data.state.opportunities[1], {amount: 123, expected_close_year: year + 1});
  Object.assign(data.state.opportunities[2], {amount: -1, expected_close_year: null});
  const w = workspace(data); await w.window.SalesPreview.loadLocal();
  const all = (await w.api('/customers/portfolio?scope=team')).data;
  assert.equal(all.items.find(row => row.id === 'c0').acv_amount, null);
  assert.equal(all.items.find(row => row.id === 'c2').unknown_amount_count, 1);
  const future = (await w.api('/customers/portfolio?scope=team&plan=long_term')).data;
  assert.deepEqual(Array.from(future.items, row => row.id), ['c1']);
  const current = (await w.api('/customers/portfolio?scope=team&plan=current_year')).data;
  assert.deepEqual(Array.from(current.items, row => row.id), ['c0']);
  const amount = (await w.api('/customers/portfolio?scope=team&amount_min=0&amount_max=200')).data;
  assert.deepEqual(Array.from(amount.items, row => row.id), ['c1']);
  assert.equal(amount.summary.claimed, 1);
  assert.equal(amount.summary.pool_total, all.summary.pool_total);
});
test('live never loads local CRM; malformed associations fail rather than mix in synthetic data',async()=>{
  const w=workspace();w.window.SALES_MODE='live';assert.equal(await w.window.SalesPreview.loadLocal(),null);assert.equal(w.fetches(),0);
  const bad=fixture();bad.state.visits[0].customer_id='another-customer';
  await assert.rejects(workspace(bad).window.SalesPreview.loadLocal(),/校验失败/);
  const missing=workspace();missing.window.fetch=async()=>({status:404});assert.equal(await missing.window.SalesPreview.loadLocal(),null);
  assert.match((await missing.api("/customers")).data.source_label,/工作区/);
});
test('full CRM retains orphan and multiple-opportunity visits once and persists only local changes', async()=>{
  const data=fixture(); data.scope='full';
  data.state.opportunities[2].customer_id=null;
  data.state.visits[2].customer_id=null;
  data.state.visits.forEach(v=>v.opportunity_ids=[v.opportunity_id]);
  data.state.visits.push({...data.state.visits[2],id:'orphan',opportunity_id:null,opportunity_ids:[]});
  data.state.visits.push({...data.state.visits[2],id:'multiple',opportunity_id:null,opportunity_ids:['o0','o1']});
  data.counts={customers:3,opportunities:3,visits:5};
  const w=workspace(data); await w.window.SalesPreview.loadLocal();
  assert.equal((await w.api('/visits')).data.total,5);
  assert.equal((await w.api('/visits?opportunity_id=o0')).data.total,2);
  assert.equal((await w.api('/visits/orphan')).statusCode,200);
  w.window.SalesPreview.reset();
  const key='sales-web:crm-preview:test-v1';
  assert.deepEqual(JSON.parse(w.storage.get(key)).arrays,{});
  const changed=await new Promise(resolve=>w.window.SalesPreview.request({url:'/api/v1/customers/c0',method:'PATCH',data:{industry:'Edited locally',version_no:1},header:{Authorization:'Bearer preview-access-manager'},success:resolve}));
  assert.equal(changed.statusCode,200,JSON.stringify(changed.data));
  const saved=JSON.parse(w.storage.get(key));
  assert.equal(saved.format,'crm-local-delta-v1');assert.equal(saved.arrays.customers.upserts.length,1);
  assert.equal(saved.arrays.opportunities,undefined);assert.equal(saved.arrays.visits,undefined);
  const refreshed=workspace(data,w.storage);await refreshed.window.SalesPreview.loadLocal();
  assert.equal((await refreshed.api('/customers')).data.items[0].industry_code,'Edited locally');
  assert.equal((await refreshed.api('/visits')).data.total,5);
  assert.equal(data.state.customers[0].name,'Fixture 0','source snapshot remains immutable');
});
test('full CRM rejects incorrect totals and dangling relationship identifiers',async()=>{
  const data=fixture();data.scope='full';data.state.visits.forEach(v=>v.opportunity_ids=[v.opportunity_id]);
  data.counts={customers:3,opportunities:3,visits:4};
  await assert.rejects(workspace(data).window.SalesPreview.loadLocal(),/校验失败/);
  data.counts.visits=3;data.state.visits[0].opportunity_ids=['not-in-dataset'];
  await assert.rejects(workspace(data).window.SalesPreview.loadLocal(),/校验失败/);
});
test('full local department dashboard includes unmapped owners while personal and team scopes stay restricted',async()=>{
  const data=fixture();data.scope='full';data.counts={customers:3,opportunities:3,visits:3};data.state.visits.forEach(v=>v.opportunity_ids=[v.opportunity_id]);
  data.state.opportunities[2].owner_id=null;data.state.customers[2].owner_id=null;
  data.state.visits.forEach(v=>{v.status='archived';v.recorder_id='person';});data.state.visits[2].recorder_id=null;
  const w=workspace(data);await w.window.SalesPreview.loadLocal();
  const all=(await w.api('/dashboard?personal=false')).data;
  assert.equal(all.opportunities.length,3);assert.equal(all.customers.length,3);assert.equal(all.recent_visits.length,3);
  for(const query of ['personal=true&member_id=person','personal=false&team_groups=team:00000003-0000-4000-8000-000000000001']){
    const scoped=(await w.api('/dashboard?'+query)).data;
    assert.equal(scoped.opportunities.length,2);assert.equal(scoped.customers.length,2);assert.equal(scoped.recent_visits.length,2);
  }
});
test('CRM quarterly metrics carry date coverage without inventing a year for source quarters', async()=>{
  const data=fixture();data.state.opportunities[0].status='won';
  data.state.opportunities[1].expected_close_date='2025-03-01';
  const w=workspace(data);await w.window.SalesPreview.loadLocal();
  const response=await w.api('/opportunities/overview?year=2026&quarters=1&quarters=2&quarters=3');
  assert.equal(response.statusCode,200);
  const m=response.data.metrics,c=m.crm_date_coverage;
  assert.equal(m.total,0,'2025 dates and yearless Q3 cannot enter 2026');
  assert.equal(c.source_count,3);assert.equal(c.close_dated,1);assert.equal(c.quarter_only,2);
  assert.equal(c.won_count,1);assert.equal(c.won_dated,0);assert.equal(c.quarters.Q3,2);
  assert.equal((await w.api('/opportunities/overview?year=2026')).data.metrics.total,3);
});
test('CRM endpoint serves only its fixed file on loopback, with no-store and no static leak',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'crm-preview-')), file=join(dir,'runtime.json');
  await writeFile(file,JSON.stringify(fixture()));
  const server=createSalesWebServer({target:'',localPreviewPath:file});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base='http://127.0.0.1:'+server.address().port;
  try {
    const response=await fetch(base+'/local-preview-data');assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');assert.equal((await response.json()).dataset_id,'test-v1');
    assert.equal((await fetch(base+'/local-preview-data',{headers:{Origin:'https://foreign.invalid'}})).status,403);
    assert.equal((await fetch(base+'/local-preview-data',{method:'POST'})).status,405);
    assert.equal((await fetch(base+'/.runtime/crm-preview.json')).status,404);
    assert.equal((await fetch(base+'/api/v1/customers')).status,503);
    await writeFile(file,'broken');assert.equal((await fetch(base+'/local-preview-data')).status,500);
    const req={method:'GET',headers:{host:'attacker.invalid'},socket:{localPort:80,localAddress:'127.0.0.1',remoteAddress:'127.0.0.1'}};
    assert.equal((await readLocalPreview(req,file)).status,403);
  } finally {await new Promise(resolve=>server.close(resolve));await rm(dir,{recursive:true,force:true});}
});
