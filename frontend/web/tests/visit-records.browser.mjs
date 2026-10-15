// Synthetic browser contract checks only. No daily LOCAL API/DB is contacted.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import fs from 'node:fs';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser, server, base;
before(async()=>{server=createSalesWebServer({previewOnly:true}); await new Promise(r=>server.listen(0,'127.0.0.1',r)); base=`http://127.0.0.1:${server.address().port}`; browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close(); if(server) await new Promise(r=>{server.close(r);server.closeAllConnections();});});
const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);
function fixture() {
  const rows=[
    {id:'v-a',customer_id:'c-a',customer_name:'合成甲客户',recorder_id:'u1',recorder_name:'成员甲',visit_date:today,follow_up_record:'甲'.repeat(600)+'全文尾部证据',next_action:'下一步全文',status:'confirmed',recording_role_code_snapshot:'sales',linked_opportunities:[{id:'o-a',name:'合成甲项目',can_open:false},{id:'o-b',name:'合成乙项目',can_open:true}],can_open_customer:false,can_open_opportunity:false},
    {id:'v-b',customer_id:'c-a',customer_name:'合成甲客户',recorder_id:'u2',recorder_name:'成员乙',visit_date:today,follow_up_record:'客户级跟进',status:'confirmed',recording_role_code_snapshot:'fde',linked_opportunities:[],can_open_customer:false},
    {id:'v-c',customer_id:'c-b',customer_name:'合成乙客户',recorder_id:'u1',recorder_name:'成员甲',visit_date:today,follow_up_record:'第三条内容',status:'confirmed',recording_role_code_snapshot:null,linked_opportunities:[],can_open_customer:false},
  ];
  return {calls:[],denied:false,partial:false,detailDenied:false,async handle(req){
    const url=new URL(req.url,base),p=url.searchParams; this.calls.push({path:url.pathname,...Object.fromEntries(p)});
    if(url.pathname.endsWith('/range')) {
      if(this.denied) return {statusCode:403,data:{code:'FORBIDDEN',message:'当前范围已不可读取'}};
      const items=rows.filter(r=>!p.get('q')||r.follow_up_record.includes(p.get('q')));
      return {statusCode:200,data:{items:items.map(r=>({...r,follow_up_record:r.follow_up_record.slice(0,600),is_summary:true})),summary:{total:this.partial?99:items.length},has_more:this.partial,next_offset:this.partial?0:null}};
    }
    if(this.detailDenied) return {statusCode:403,data:{code:'FORBIDDEN',message:'当前详情已不可读取'}};
    return {statusCode:200,data:rows.find(r=>url.pathname.endsWith('/'+r.id))};
  }};
}
async function client(f,width=1440) {
  const context=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'});
  await context.exposeBinding('__recordFixture',(_,req)=>f.handle(req));
  await context.addInitScript(()=>{
    let provider;
    Object.defineProperty(window,'SalesPreview',{configurable:true,get:()=>provider,set(value){const original=value.request.bind(value);provider={...value,request(options){
      const path=new URL(options.url,location.href).pathname;
      if(!path.endsWith('/visits/range')&&!/\/visits\/v-[abc]$/.test(path)) return original(options);
      let aborted=false;window.__recordFixture({url:options.url}).then(response=>{if(!aborted){options.success?.(response);options.complete?.(response);}});return {abort(){aborted=true;}};
    }};}});
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(12000);
  await page.goto(`${base}/?mode=preview#/pages/visit-records/index`);await page.getByText('客户级跟进',{exact:true}).waitFor();
  return {page,context,errors};
}
async function pick(page,name,value) {
  const control=page.getByRole('combobox',{name,exact:true});
  if(!await control.isVisible()) await page.getByRole('button',{name:/更多条件/}).click();
  await control.click();await page.locator('.ant-select-dropdown:visible').getByText(value,{exact:true}).click();
  if(await page.getByRole('button',{name:'收起',exact:true}).isVisible()) await page.getByRole('button',{name:'收起',exact:true}).click();
}
test('customer and multi-opportunity filters agree with cards; one desktop filter row at 1024', {timeout:40000},async()=>{
  const f=fixture(),c=await client(f,1024),{page}=c;
  try {
    assert.ok((await page.locator('.pc-page-filters .pc-filterbar-row').boundingBox()).height<60);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await pick(page,'筛选客户','合成甲客户');await page.getByText('第三条内容',{exact:true}).waitFor({state:'hidden'});
    await pick(page,'筛选商机','未关联商机');assert.equal(await page.locator('.pc-fu').count(),1);assert.equal((await page.locator('.pc-vr-kpis .pc-kpi-value').first().innerText()).replace(/\s/g,''),'1条');
    await pick(page,'筛选商机','合成乙项目');await page.getByText('客户级跟进',{exact:true}).waitFor({state:'hidden'});assert.equal(await page.locator('.pc-fu').count(),1);
    assert.equal(await page.getByRole('button',{name:'合成甲客户',exact:true}).count(),0);
    assert.equal(await page.getByRole('button',{name:'合成甲项目',exact:true}).count(),0);
    assert.equal(await page.getByRole('button',{name:'合成乙项目',exact:true}).count(),1);
    if(process.env.VISIT_SHOTS){fs.mkdirSync(process.env.VISIT_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.VISIT_SHOTS}/records-1024.png`});}
    assert.deepEqual(c.errors,[]);
  } finally {await c.context.close();}
});
test('search finds content beyond the summary; drawer fetches full record and handles revoked read', {timeout:40000},async()=>{
  const f=fixture(),c=await client(f),{page}=c;
  try {
    await page.getByPlaceholder('搜索客户、商机、人员或完整内容').fill('全文尾部证据');await page.getByPlaceholder('搜索客户、商机、人员或完整内容').press('Enter');
    await page.getByText('客户级跟进',{exact:true}).waitFor({state:'hidden'});assert.equal(f.calls.filter(x=>x.path.endsWith('/range')).at(-1).q,'全文尾部证据');
    await page.getByRole('button',{name:'详情',exact:true}).click();await page.locator('.pc-fu-detail').getByText(/全文尾部证据/).waitFor();
    assert.equal(await page.getByRole('button',{name:'客户时间轴',exact:true}).count(),0);assert.equal(f.calls.filter(x=>x.path.endsWith('/v-a')).length,1);
    if(process.env.VISIT_SHOTS){fs.mkdirSync(process.env.VISIT_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.VISIT_SHOTS}/detail-1440.png`});}
    await page.keyboard.press('Escape');await page.locator('.ant-drawer-open').waitFor({state:'hidden'});
    f.detailDenied=true;await page.getByRole('button',{name:'详情',exact:true}).click();await page.locator('.pc-fu-drawer').getByText('当前详情已不可读取',{exact:true}).waitFor();assert.equal(await page.locator('.pc-fu-detail').count(),0);
    assert.deepEqual(c.errors,[]);
  } finally {await c.context.close();}
});
test('refresh hides old drawer and rows on 403; partial read does not claim complete totals or allow export', {timeout:40000},async()=>{
  const f=fixture(),c=await client(f),{page}=c;
  try {
    await page.getByRole('button',{name:'详情',exact:true}).first().click();await page.locator('.pc-fu-detail').waitFor();await page.keyboard.press('Escape');
    f.denied=true;await page.getByRole('button',{name:'刷新跟进记录',exact:true}).click();await page.getByText('当前范围已不可读取',{exact:true}).waitFor();assert.equal(await page.locator('.pc-fu').count(),0);
    f.denied=false;f.partial=true;await page.getByRole('button',{name:'刷新跟进记录',exact:true}).click();await page.getByText('当前记录未读取完整',{exact:true}).waitFor();
    assert.ok((await page.locator('.pc-vr-kpis .pc-kpi-value').first().innerText()).includes('—'));assert.equal(await page.getByRole('button',{name:'导出摘要',exact:true}).isDisabled(),true);assert.deepEqual(c.errors,[]);
  } finally {await c.context.close();}
});
