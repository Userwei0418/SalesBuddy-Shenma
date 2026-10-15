/** Approved UI package integration, with original Page/Component actions and synthetic HTTP only. */
import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;
const customer='00000010-0000-4000-8000-000000000001', visit='00000013-0000-4000-8000-000000000001', opportunity='00000011-0000-4000-8000-000000000001';
before(async()=>{server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();if(server)await new Promise(r=>server.close(r));});
async function fixture(run,{width=1440,role='sales'}={}) {
  const ctx=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'}),errors=[],blocked=[];
  await ctx.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
  await ctx.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin!==base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)){blocked.push(url.pathname);return route.abort();}return route.continue();});
  const page=await ctx.newPage();page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
  const go=async(route,query='')=>{await page.evaluate(url=>SalesRuntime.wx.navigateTo({url}),`/pages/${route}/index${query?'?'+query:''}`);await page.locator(`[data-department-page="pages/${route}/index"]`).waitFor();};
  try {
    await page.goto(base+'/?mode=preview#/pages/index/index');await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await run({page,go});assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'document must not overflow horizontally');
  } catch(error) {console.error('WORKFLOW_COMPLETION',JSON.stringify({errors,state:await page.evaluate(()=>({route:SalesRuntime?.current?.route,errors:SalesRuntime?.errors,state: Object.fromEntries(['accessBlocked','opportunityId','opportunityTab','opportunityLoading','opportunityError','adviceExpanded','opportunityAdvice','canOpportunityAdvice'].map(k=>[k,SalesRuntime.current.data[k]])),retry:SalesRuntime.current?._departmentRetry,components:[...SalesRuntime.current._components.values()].map(c=>({tag:c._tag,data:c._tag==='advice-actions'?c.data:undefined})),text:document.querySelector('#page-root')?.innerText?.slice(0,1800)}))}));throw error;}
  finally {await ctx.close();}
}
async function shot(page,name){if(process.env.UI_EVIDENCE){await mkdir(process.env.UI_EVIDENCE,{recursive:true});await page.screenshot({path:`${process.env.UI_EVIDENCE}/${name}.png`,fullPage:true});}}
async function mockAdvice(page) {
  await page.evaluate(()=>{
    const original=SalesRuntime.wx.request;
    window.uiAdvice={writes:[],value:{id:'ui-synthetic-advice',status:'succeeded',summary:'合成建议，仅验证 UI 操作链。',subject_kind:'visit',section:'overview',suggestions:[{id:'ui-suggestion',title:'核对合成验收清单',evidence:'合成跟进约定核对材料。',action:'明日由合成负责人核对清单。',decision:'pending',version_no:1}]}};
    SalesRuntime.wx.request=options=>{
      const path=new URL(options.url,location.href).pathname.replace(/^\/api\/v1/,'');let data;
      if(path==='/advice' && options.method==='POST'){uiAdvice.value.subject_id=options.data.subject_id;uiAdvice.value.subject_kind=options.data.subject_kind;data=uiAdvice.value;}
      else if(path==='/advice/ui-synthetic-advice')data=uiAdvice.value;
      else if(path==='/advice/suggestions/ui-suggestion/decision'){
        uiAdvice.writes.push(structuredClone(options.data));Object.assign(uiAdvice.value.suggestions[0],{decision:options.data.decision,decision_note:options.data.note,version_no:2});data=uiAdvice.value.suggestions[0];
      }else return original(options);
      const timer=setTimeout(()=>{const response={statusCode:200,data:structuredClone(data),header:{}};options.success?.(response);options.complete?.(response);},5);return {abort(){clearTimeout(timer);}};
    };
  });
}
for(const width of [1440,390])test(`${width}px claims, recording guidance and brand retain accessible controls`,{timeout:40000},()=>fixture(async({page,go})=>{
  await go('customer-claim');await page.waitForFunction(()=>!SalesRuntime.current.data.loading);
  const eligible=await page.evaluate(()=>SalesRuntime.current.data.customers.find(row=>row.claimEligible));assert.ok(eligible);
  const radio=page.getByRole('radio',{name:`选择 ${eligible.name}`,exact:true});await radio.check();assert.equal(await page.evaluate(()=>SalesRuntime.current.data.selectedCustomerId),eligible.id);
  await page.getByPlaceholder('客户名称 / 拼音 / 首字母').fill(eligible.name);await page.waitForFunction(()=>SalesRuntime.current.data.query!==''&&!SalesRuntime.current.data.loading);
  await page.getByLabel('已选条件').waitFor();await page.getByRole('button',{name:'清除筛选',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('input[placeholder="客户名称 / 拼音 / 首字母"]')?.value==='');assert.equal(await page.getByPlaceholder('客户名称 / 拼音 / 首字母').inputValue(),'');await shot(page,`claim-${width}`);
  await go('visit-entry');const guide=page.getByRole('button',{name:'录音时可以说些什么',exact:true});assert.equal(await guide.getAttribute('aria-expanded'),'false');
  await guide.focus();await page.keyboard.press('Enter');await page.locator('#ds-recording-guide-body').waitFor();assert.equal(await page.getByRole('button',{name:'收起录音指引'}).getAttribute('aria-expanded'),'true');
  await shot(page,`guide-${width}`);await page.getByRole('button',{name:'收起录音指引'}).click();await page.locator('#ds-recording-guide-body').waitFor({state:'detached'});assert.equal(await page.locator('#ds-recording-guide-body').count(),0);
  assert.match(await page.title(),width<=600?/ · SalesBuddy$/:/ · Raccoon SalesBuddy$/);
},{width}));
test('visit advice uses real components, persists no-task reason and follows current version after refresh',{timeout:45000},()=>fixture(async({page,go})=>{
  await mockAdvice(page);await go('visit-detail',`customer_id=${customer}&visit_id=${visit}`);
  await page.getByRole('button',{name:'无需待办',exact:true}).waitFor();
  const components=await page.evaluate(()=>SalesRuntime.current.selectAllComponents('advice-actions').map(c=>({analysisId:c.data.analysisId,suggestionId:c.data.suggestion.id,version:c.data.suggestion.version_no})));
  assert.deepEqual(components,[{analysisId:'ui-synthetic-advice',suggestionId:'ui-suggestion',version:1}]);
  await page.getByRole('button',{name:'无需待办',exact:true}).click();const modal=page.locator('.wx-modal-mask');await modal.locator('input,textarea').fill('已在例会上安排');await modal.getByRole('button',{name:'确认',exact:true}).click();
  await page.getByText('已处理 · 无需待办 · 已在例会上安排',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'采纳并建待办',exact:true}).count(),0);
  assert.deepEqual(await page.evaluate(()=>uiAdvice.writes),[{decision:'no_task',version_no:1,note:'已在例会上安排'}]);
  assert.equal(await page.evaluate(()=>SalesRuntime.current.selectAllComponents('advice-actions')[0].data.suggestion.version_no),2);await shot(page,'advice-no-task');
}));
test('opportunity advice collapse, late validation and adopt route keep original task source',{timeout:45000},()=>fixture(async({page,go})=>{
  await mockAdvice(page);await go('customer-assets',`customer_id=${customer}&opportunity_id=${opportunity}`);
  assert.equal(await page.getByRole('button',{name:'查看完整建议',exact:true}).count(),0,'no advice capability means no advice actions');
  await page.evaluate(()=>SalesRuntime.current.setData({canOpportunityAdvice:true}));
  await page.getByRole('button',{name:'查看完整建议',exact:true}).click();
  await page.getByRole('button',{name:'采纳并建待办',exact:true}).waitFor();
  await page.evaluate(()=>{const p=SalesRuntime.current,key=p.data.opportunityTab;p.setData({opportunityAdvice:{...p.data.opportunityAdvice,[key]:{...p.data.opportunityAdvice[key],needsCheck:true,checkError:'合成校验暂不可用'}}});});
  await page.getByText('合成校验暂不可用',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'采纳并建待办',exact:true}).count(),0);
  await page.getByRole('button',{name:'检查更新',exact:true}).click();await page.getByRole('button',{name:'采纳并建待办',exact:true}).click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/management-task-create/index');assert.match(page.url(),/adviceId=ui-synthetic-advice/);assert.match(page.url(),/suggestionId=ui-suggestion/);assert.equal(await page.evaluate(()=>uiAdvice.writes.length),0);
}));
test('archived followup primary action opens that exact visit, not only its customer',{timeout:35000},()=>fixture(async({page,go})=>{
  await go('visit-confirm');await page.evaluate(({customer,visit})=>SalesRuntime.current.setData({archived:true,customerId:customer,visitId:visit,customerName:'合成归档客户',archivedCount:5,score:90,grade:'合成检查',values:{next_action:'明天核对合成清单'}}),{customer,visit});
  const button=page.getByRole('button',{name:'查看本次跟进详情',exact:true});await button.waitFor();assert.match(await button.getAttribute('class'),/ant-btn-primary/);await button.click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-detail/index');assert.equal(await page.evaluate(()=>SalesRuntime.current.data.visitId),visit);
}));
test('team ranking shows actual per-member count, including zero and former contributors',{timeout:40000},()=>fixture(async({page,go})=>{
  await go('bi');await page.waitForFunction(()=>!SalesRuntime.current.data.loading&&!SalesRuntime.current.data.rankingLoading);
  await page.evaluate(()=>SalesRuntime.current.setData({viewMode:'team',rankingCards:[{key:'followup',title:'团队人均跟进',subtitle:'合成团队',period:'2026 Q3',summaryIds:['team-a'],rows:[{id:'team-a',rank:1,name:'合成南区',value:2,displayValue:'2 次/人',memberSummary:'6 ÷ 3 = 2 次/人',members:[{user_id:'a',name:'成员甲',followup_count:5,customer_count:2,current_member:true},{user_id:'b',name:'成员乙',followup_count:0,customer_count:0,current_member:true},{user_id:'c',name:'成员丙',followup_count:0,customer_count:0,current_member:true},{user_id:'d',name:'原成员',followup_count:1,customer_count:1,current_member:false}]}]}]}));
  await page.getByRole('button',{name:'查看 合成南区 成员次数',exact:true}).click();await page.getByText('6 ÷ 3 = 2 次/人',{exact:true}).waitFor();await page.getByText('0 次',{exact:true}).first().waitFor();await page.getByText('原团队贡献，不计入当前人数',{exact:true}).waitFor();if(process.env.UI_EVIDENCE)await page.locator('.ds-ranking-members').screenshot({path:`${process.env.UI_EVIDENCE}/ranking-members-detail.png`});
},{role:'manager'}));
