import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Isolated synthetic state only: the VM has no fetch and never uses browser storage.
function workspace() {
  const storage = new Map();
  const window = {SALES_MODE:'preview', setTimeout, clearTimeout, localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}};
  vm.runInNewContext(fs.readFileSync(new URL('../preview-api.js',import.meta.url),'utf8'),{window,URL,URLSearchParams,setTimeout,clearTimeout});
  return (role, path, method='GET', data) => new Promise(resolve=>window.SalesPreview.request({url:'https://offline.invalid/api/v1'+path,method,data,header:{Authorization:'Bearer preview-access-'+role},success:resolve}));
}
const unknown = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
const value = async promise => {const response=await promise; assert.equal(response.statusCode,200,response.data.message);return response.data;};
const anchor = '2026-07-01';

test('authorized empty team stays visible and returns empty customer, asset and quarter slices',async()=>{
  const read=workspace(),directory=await value(read('manager','/directory/teams'));
  const empty=directory.teams.find(team=>team.member_count===0),filled=directory.teams.find(team=>team.member_count>0);assert.ok(empty&&filled);
  for(const path of ['/customer-assets/map','/customer-assets','/customer-assets/quarters']){
    const data=await value(read('manager',path+'?scope=team&team_id='+empty.id));assert.equal(data.items.length,0,path);
    if(data.summary) assert.equal(data.summary.customer_count??data.summary.entry_count,0);
    assert.equal((await read('manager',path+'?team_id='+unknown)).statusCode,403);
    assert.equal((await read('sales',path+'?team_id='+empty.id)).statusCode,403);
  }
  assert.ok((await value(read('manager','/customer-assets/map?team_id='+filled.id))).items.length>0);
  const people=await value(read('manager','/directory/members'));assert.ok(people.items.every(person=>Array.isArray(person.team_ids)));
  const member=people.items.find(person=>person.role==='sales');
  const mine=await value(read('manager','/customer-assets/map?member_id='+member.id));assert.ok(mine.items.length>0);assert.ok(mine.items.every(row=>row.owner_id===member.id));
  assert.equal((await read('manager','/customer-assets?member_id='+unknown)).statusCode,403);
});

test('manager department and valid empty-team targets are read-only; invalid targets remain rejected',async()=>{
  const read=workspace(),options=await value(read('manager','/profile/scope-options'));
  assert.ok(options.allowed_scopes.includes('department'));assert.ok(options.members.some(row=>row.role==='fde'));
  const empty=options.teams.find(row=>row.member_count===0);
  for(const scope of ['scope=department','scope=team&team_id='+empty.id]){
    const target=await value(read('manager','/targets?'+scope+'&period_type=quarter&anchor_date='+anchor));assert.equal(target.editable,false);assert.equal(target.items.length,0);
    const performance=await value(read('manager','/profile/performance?'+scope+'&year=2026&quarter=3'));assert.equal(performance.editable,false);
    if(scope.startsWith('scope=team'))assert.equal(performance.active_opportunity_amount,0);
  }
  assert.equal((await read('supervisor','/targets?scope=department&period_type=quarter&anchor_date='+anchor)).statusCode,403);
  assert.equal((await read('manager','/targets?scope=team&team_id='+unknown+'&anchor_date='+anchor)).statusCode,403);
  const dashboardOptions=await value(read('manager','/dashboard/options'));assert.ok(dashboardOptions.members.some(person=>person.role==='fde'));
  const fde=dashboardOptions.members.find(person=>person.role==='fde');
  const selected=await value(read('manager','/dashboard?personal=true&member_id='+fde.id));assert.equal(selected.selection.member_id,fde.id);
  assert.equal((await read('sales','/dashboard?personal=true&member_id='+fde.id)).statusCode,403);
});

test('FDE empty team clears projects, task totals, activity, targets and selected team identity',async()=>{
  const read=workspace(),directory=await value(read('fde_lead','/fde/scope-options'));
  const empty=directory.teams.find(team=>team.member_count===0);assert.ok(empty);
  const query='?scope=team&team_id='+empty.id;
  const dash=await value(read('fde_lead','/fde/dashboard'+query));
  for(const key of ['opportunities','open_opportunities','visits','pending_tasks','completed_tasks','own_demo_scene_count'])assert.equal(dash.summary[key],0,key);
  assert.equal(dash.ranking.length,0);assert.deepEqual(Array.from(dash.company_rankings.selection.team_ids),[empty.id]);
  const emptyRank=dash.company_rankings.items.find(row=>row.user_id===empty.id);assert.equal(emptyRank.opportunity_count,0);
  assert.equal((await value(read('fde_lead','/fde/activity'+query))).items.length,0);
  assert.equal((await value(read('fde_lead','/fde/profile'+query))).sample_count,0);
  assert.equal((await value(read('fde_lead','/targets'+query+'&anchor_date='+anchor))).editable,false);
  for(const path of ['/fde/dashboard','/fde/activity','/fde/profile'])assert.equal((await read('fde_lead',path+'?scope=team&team_id='+unknown)).statusCode,403,path);
});

test('customer creation stores selected team ID without changing claim approval semantics',async()=>{
  const read=workspace(),directory=await value(read('manager','/directory/teams?purpose=assignment')),empty=directory.teams.find(row=>row.member_count===0);
  const body={name:'Synthetic team assignment check',customer_type:'潜在客户',level_code:'Tier-2',source:'市场活动',target_team:empty.name,target_team_id:empty.id,contact_name:'Synthetic person',contact_title:'Synthetic role',contact_role:'决策者'};
  const created=await value(read('manager','/customers','POST',body));assert.equal(created.owner_team_id,empty.id);assert.equal(created.target_team_id,empty.id);assert.equal(created.team_name,empty.name);assert.equal(created.owner_id,null);
  assert.equal((await read('manager','/customers','POST',{...body,name:body.name+' unknown',target_team_id:unknown})).statusCode,403);
  assert.equal((await read('manager','/customers','POST',{...body,name:body.name+' mismatch',target_team:'wrong name'})).statusCode,422);
  const claim=await value(read('sales','/customers/'+created.id+'/claims','POST',{}));assert.equal(claim.status,'pending');
  const current=await value(read('manager','/customers/'+created.id+'/reference'));assert.equal(current.owner_id,null);assert.equal(current.owner_team_id,empty.id);
});
