import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));
const base=`http://127.0.0.1:${server.address().port}`,browser=await chromium.launch({channel:'chrome',headless:true});
const rows=[],issues=[];const out=process.env.UI_EVIDENCE || '../evidence';await mkdir(out,{recursive:true});
const uid=(n,i=1)=>`${String(n).padStart(8,'0')}-0000-4000-8000-${String(i).padStart(12,'0')}`;
const config=JSON.parse(await readFile(new URL('../source/miniprogram/app.json',import.meta.url)));
const query={'customer-detail':`id=${uid(10)}`,'customer-edit':`customerId=${uid(10)}`,'customer-assets':`customer_id=${uid(10)}&opportunity_id=${uid(11)}`,'opportunity-create':`customerId=${uid(10)}`,'visit-detail':`visit_id=${uid(13)}&customer_id=${uid(10)}`,'task-detail':`id=${uid(15)}`,'risk-detail':`id=${uid(14)}`,'member-growth':'account=PREVIEW_SALES','report-detail':'runId=synthetic-report&action=report_department_daily&section=safe_customers&row=0','customer-assign-confirm':`customerId=${uid(10)}`,'demo-create':`opportunity_id=${uid(11)}&customer_id=${uid(10)}`};
try{
 for(const role of ['manager','sales','fde_lead','fde']){
  const ctx=await browser.newContext({viewport:{width:1440,height:900}});await ctx.addInitScript(r=>sessionStorage.setItem('sales-web:preview-role',r),role);
  await ctx.route('**/*',r=>new URL(r.request().url()).origin===base&&!new URL(r.request().url()).pathname.startsWith('/api/')?r.continue():r.abort());
  const p=await ctx.newPage();p.on('pageerror',e=>issues.push({role,error:e.message}));
  await p.goto(base+'/?mode=preview');await p.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
  await p.evaluate(()=>{const api=SalesPreview;window.SalesPreview={...api,request(o){if(o.url.endsWith('/agent/runs/synthetic-report')){queueMicrotask(()=>o.success?.({statusCode:200,data:{id:'synthetic-report',status:'succeeded',completed_at:new Date().toISOString(),result:{title:'合成经营报告',scope:'合成授权范围',safe_customers:[{title:'合成客户回顾',detail:'用于验证报告详情展示，不代表真实经营结论。'}]}}}));return {abort(){}};}return api.request(o);}};});
  const routes=role==='manager'?[...config.pages.filter(p=>!p.includes('/login/')),'pages/weekly-report/index']:['pages/customers/index','pages/management-task-create/index','pages/tasks/index','pages/profile/index','pages/bi/index','pages/workbench/index','pages/visit-entry/index','pages/fde-records/index','pages/demo-create/index'];
  for(const route of routes){
   await p.evaluate(url=>SalesRuntime.wx.reLaunch({url}),'/'+route+(query[route.split('/')[1]]?'?'+query[route.split('/')[1]]:''));
   await p.waitForTimeout(450);
   const result=await p.evaluate(()=>({route:SalesRuntime.current?.route,errors:SalesRuntime.errors,text:document.querySelector('#page-root')?.innerText || '',data:SalesRuntime.current?.data}));
   if(result.route!==route || !result.text.trim() || result.errors?.length)issues.push({role,route,kind:'route-or-render',actual:result.route});
   if(result.data?.error || result.data?.loadError)issues.push({role,route,kind:'unexpected-data-error',error:result.data.error || result.data.loadError});
   rows.push({role,expected:route,actual:result.route,text:result.text.slice(0,320),errors:result.errors,state:Object.fromEntries(Object.entries(result.data || {}).filter(([k,v])=> /error|loading|blocked/i.test(k) && (typeof v==='string'||typeof v==='boolean')))});
   if(role==='manager' && ['customers','tasks','management-task-create','bi','profile','weekly-report'].includes(route.split('/')[1]))await p.screenshot({path:`${out}/${route.split('/')[1]}-1440.png`});
  }
  await ctx.close();
 }
}finally{await writeFile(`${out}/route-audit.json`,JSON.stringify({rows,issues},null,2));await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
console.log(JSON.stringify({routes:rows.length,issues}));assert.deepEqual(issues,[]);
