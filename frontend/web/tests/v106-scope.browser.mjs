/** v1.0.6 directory/catalog/map parity, using isolated synthetic preview only. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
async function session(run){
 const server=createSalesWebServer({target:'',localLogin:null});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base='http://127.0.0.1:'+server.address().port,browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1366,height:900}}),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin!==base)return route.abort();if(url.pathname.startsWith('/api/')||url.pathname==='/local-login'){requests.push(url.pathname);return route.abort();}return route.continue();});
 try{await page.goto(base+'/?mode=preview');await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session);await run(page);assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}
 finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
}
async function go(page,route){await page.evaluate(route=>SalesRuntime.route('/pages/'+route+'/index',{tab:true}),route);}
async function role(page,value){await page.locator('#preview-role').evaluate((select,value)=>{select.value=value;select.dispatchEvent(new Event('change',{bubbles:true}));},value);await page.waitForFunction(value=>window.SalesRuntime?.app?.globalData.session?.role===value,value);}
async function picker(page){await page.waitForFunction(()=>document.querySelector('#web-select-input')?.tomselect?.isOpen);}

test('v1.0.6 dashboard uses exactly one all-or-team choice including an authorized empty team',async()=>session(async page=>{
 await role(page,'manager');await go(page,'bi');await page.waitForFunction(()=>!SalesRuntime.current.data.optionsLoading&&SalesRuntime.current.data.teamOptions.length>=3);
 await page.locator('[data-mode="team"]').first().click();
 const choices=await page.evaluate(()=>SalesRuntime.current.data.teamOptions),empty=choices.find(row=>row.name==='示例空团队');assert.ok(empty);assert.match(empty.id,/^team:/);
 await page.locator('dashboard-picker button').first().click();await picker(page);
 assert.equal(await page.evaluate(()=>document.querySelector('#web-select-input').tomselect.settings.maxItems),1);
 await page.locator(`#web-select-dialog .option[data-value="${empty.id}"]`).click();
 await page.waitForFunction(id=>SalesRuntime.current.data.selectedTeamChoice===id,empty.id);
 assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.selectedTeamGroups),[empty.id]);
 await page.locator('dashboard-picker button').first().click();await picker(page);await page.locator('#web-select-dialog .option[data-value="all"]').click();
 await page.waitForFunction(()=>SalesRuntime.current.data.selectedTeamChoice==='all');
 assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.selectedTeamGroups),choices.filter(row=>row.id!=='all').map(row=>row.id).sort());
 await page.locator('dashboard-picker button').first().click();await picker(page);
 await page.evaluate(()=>SalesRuntime.current.setData({optionsError:'目录读取失败'}));await page.waitForSelector('#web-select-dialog',{state:'detached'});
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.selectedTeamChoice),'all','directory failure cannot apply a stale selection');
}));

test('v1.0.6 profile retains department/team/person scope and reports Web picker visibility',async()=>session(async page=>{
 await role(page,'manager');await go(page,'profile');await page.waitForFunction(()=>SalesRuntime.current.data.scopeTeams.length>=2&&!SalesRuntime.current.data.directoryLoading);
 assert.deepEqual(await page.locator('profile-scope-picker .role-scope-option').allTextContents(),['部门','团队','个人']);
 await page.locator('profile-scope-picker [data-mode="team"]').click();
 await page.locator('profile-scope-picker .subject-trigger').click();await picker(page);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.scopePickerOpen),true);
 await page.keyboard.press('Escape');await page.waitForFunction(()=>!SalesRuntime.current.data.scopePickerOpen);
 await page.locator('profile-scope-picker .subject-trigger').click();await picker(page);
 const empty=await page.evaluate(()=>SalesRuntime.current.data.scopeTeams.find(row=>row.name==='示例空团队'));
 await page.locator(`#web-select-dialog .option[data-value="${empty.id}"]`).click();
 await page.waitForFunction(id=>SalesRuntime.current.data.profileTeamId===id,empty.id);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.scopePickerOpen),false);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.targetContext().query.team_id),empty.id);
 await page.locator('profile-scope-picker [data-mode="person"]').click();
 await page.locator('profile-scope-picker .subject-trigger').click();await picker(page);
 assert.ok(await page.locator('#web-select-dialog .web-select-option-meta').count()>0,'authorized member team labels remain visible');
 await page.evaluate(()=>SalesRuntime.current.setData({scopeMembers:[]}));await page.waitForSelector('#web-select-dialog',{state:'detached'});
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.scopePickerOpen),false);
}));

test('v1.0.6 customer map shares quadrant list/count and resolves overlapping hit areas',async()=>session(async page=>{
 await role(page,'manager');await go(page,'customers');await page.waitForFunction(()=>SalesRuntime.current.data.customers.length>=2&&!SalesRuntime.current.data.directoryLoading);
 // The source battle map is visible alongside the customer list.
 assert.equal(await page.locator('.battle-section').isVisible(),true);
 assert.equal(await page.locator('#customer-view-switch,[data-customer-view]').count(),0);
 const total=await page.evaluate(()=>SalesRuntime.current.data.customers.length);
 await page.locator('.map-zone.zone-attack').click({position:{x:8,y:8}});
 await page.waitForFunction(()=>SalesRuntime.current.data.quadrantIndex===1&&SalesRuntime.current.data.customers.every(row=>row.quadrant==='主攻区'));
 const zoomed=await page.evaluate(()=>({list:SalesRuntime.current.data.customers.map(row=>row.id),dots:SalesRuntime.current.data.plotCustomers.map(row=>row.id),quadrants:SalesRuntime.current.data.customers.map(row=>row.quadrant)}));
 assert.deepEqual(zoomed.list,zoomed.dots);assert.ok(zoomed.quadrants.every(name=>name==='主攻区'));
 await page.locator('.zone-expanded').click({position:{x:8,y:8}});await page.waitForFunction(total=>SalesRuntime.current.data.quadrantIndex===0&&SalesRuntime.current.data.customers.length===total,total);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.customers.length),total);
 const ids=await page.evaluate(()=>{const owner=SalesRuntime.current,rows=owner.data.plotCustomers.slice(0,2).map(row=>({...row,plotStyle:'left:45%;top:45%'}));owner.setData({plotCustomers:rows});owner.showBattleCustomer=id=>{window.selectedMapCustomer=id;};return rows.map(row=>row.id);});
 await page.locator('.plot-hit-area').last().click();await page.waitForFunction(()=>SalesRuntime.current.data.plotCandidates.length===2);
 await page.locator('.plot-candidate').first().waitFor();assert.equal(await page.locator('.plot-candidate').count(),2);
 await page.locator(`.plot-candidate[data-id="${ids[0]}"]`).click();await page.waitForFunction(id=>window.selectedMapCustomer===id,ids[0]);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.plotCandidates.length),0);
 await page.locator('.filter-team select').click();await picker(page);
 await page.evaluate(()=>SalesRuntime.current.setData({directoryError:'目录不可用'}));await page.waitForSelector('#web-select-dialog',{state:'detached'});
 assert.equal(await page.locator('.filter-team select').isDisabled(),true);
}));

test('v1.0.6 preview charts read stage codes and labels from the authenticated catalog',async()=>session(async page=>{
 await go(page,'bi');await page.waitForFunction(()=>SalesRuntime.businessOptions().isReady()&&!SalesRuntime.current.data.loading);
 const snapshot=await page.evaluate(()=>{
   const catalog=SalesRuntime.businessOptions(),open=catalog.stages.filter(row=>row.status==='open');
   open.forEach((row,index)=>{row.code='custom-'+index;row.label='后端阶段 '+index;});
   const raw=open.map((stage,index)=>({id:'snapshot-'+index,stage_code:stage.code,probability:stage.probability,status:'open'}));
   const owner={route:'pages/bi/index',_raw:{source_label:'CRM 合成数据',opportunities:raw},data:{loading:false,loadError:''}};
   const tree=SalesDashboard.adapt([{tag:'section',key:'root',attrs:{class:'metrics-section'},children:[]}],owner);
   let spec;const walk=rows=>rows.forEach(row=>{if(row.chart)spec=row.chart;walk(row.children||[]);});walk(tree);
   return {labels:spec.option.baseOption.yAxis.data,values:spec.option.baseOption.series[0].data.map(row=>row.value),expected:open.slice().sort((a,b)=>b.probability-a.probability).map(row=>row.label)};
 });
 assert.deepEqual(snapshot.labels,snapshot.expected);assert.deepEqual(snapshot.values,snapshot.expected.map(()=>1));assert.ok(snapshot.labels.every(label=>label.startsWith('后端阶段')));
}));
