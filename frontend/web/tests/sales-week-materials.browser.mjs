// Frozen synthetic material only; every browser request is intercepted locally.
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
let browser,js,css;
before(async()=>{
 const result=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
 import React,{useState}from'react';import{createRoot}from'react-dom/client';import{App}from'antd';
 import{PcProvider}from'./department-ui/pc/PcProvider.jsx';import MaterialReview from'./department-ui/work-report/MaterialReview.jsx';
 import{materialView}from'./department-ui/work-report/presentation.js';import{SALES_WEEK_FACTS}from'./department-ui/work-report/sales-week.js';
 import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';
 import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/work-report/work-report.css';
 const ids=['summary','actual_collection','next_week_plan','next_week_collection','ppl_first_30','ppl_open_50'];
 const titles=['本周工作总结','本周实际回款','下周工作计划','下周预计回款','本周首次达到 ≥30% PPL：进展与下一步','全部在推 ≥50% PPL：进展与下一步'];
 const config={sections:ids.map((id,i)=>({id,title:titles[i],enabled:true})),style:{tone:'concise',length:'medium'}};
 const snapshot={status:'ready',period:{label:'2026-10-05 至 2026-10-11',cutoff:'2026-10-09T08:00:00Z'},scope:{label:'本部门'},data_as_of:'2026-10-09T08:00:00Z',coverage:[],selection:{supplements:[]},
 sales_week_facts:{facts_contract:SALES_WEEK_FACTS,data_as_of:'2026-10-09T08:00:00Z',next_period:{start:'2026-10-12T00:00:00+08:00',end_exclusive:'2026-10-19T00:00:00+08:00'},
 finance:{precision_warning:'not_allocated_to_days',unallocated_period_count:2},sections:ids.map((section_id,i)=>({section_id,availability:i===3||i===4?'unknown':'available',population_total:i===3||i===4?null:2,included:i===3||i===4?0:2,population_complete:i!==3&&i!==4,content_complete:i!==3&&i!==4,missing_reasons:i===3?['当前没有按日登记的正式预计回款计划；可整理用户补充，须标为未核实。']:i===4?['旧商机首次达到 30% 的历史不完整，不能按当前概率补计。']:[]}))}};
 function Fixture(){const[v,set]=useState('current');window.setVersion=set;const input={...snapshot,sales_week_facts:v==='legacy'?null:{...snapshot.sales_week_facts,facts_contract:v==='future'?'unsupported':SALES_WEEK_FACTS}};return <PcProvider><App><main className="department-ui" style={{padding:24,minHeight:'100vh'}}><MaterialReview value={materialView(input,config,true)} onGenerate={()=>{window.generated=true}}/></main></App></PcProvider>}
 createRoot(document.getElementById('root')).render(<Fixture/>);
 `},bundle:true,write:false,outdir:'.runtime/sales-week-components',define:{'process.env.NODE_ENV':'"production"'}});
 js=result.outputFiles.find(x=>x.path.endsWith('.js')).text;css=result.outputFiles.find(x=>x.path.endsWith('.css')).text;
 browser=await chromium.launch({channel:'chrome',headless:true});
});
after(async()=>browser?.close());
for(const width of [1440,1024])test(`six-section material coverage at ${width}px retains gaps and old reports`,async()=>{
 const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[],unexpected=[];
 page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',route=>{
  if(route.request().resourceType()!=='document'){unexpected.push(route.request().url());return route.abort();}
  return route.fulfill({contentType:'text/html',body:'<!doctype html><html><head><style>'+css+'</style></head><body style="margin:0"><div id="page-root"><div id="root"></div></div><script>'+js+'</script></body></html>'});
 });
 try{
  await page.goto('https://sales-week-materials.test');
  const coverage=page.getByRole('region',{name:'销售周报章节材料'});await coverage.waitFor();
  assert.equal(await coverage.locator('article').count(),6);assert.equal(await coverage.getByText('暂无法核定',{exact:true}).count(),2);
  await coverage.getByText(/未计入本周回款/).waitFor();assert.equal(await page.getByRole('button',{name:'生成报告草稿'}).isEnabled(),true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  if(process.env.REPORT_SHOTS){await fs.mkdir(process.env.REPORT_SHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.REPORT_SHOTS,'weekly-six-'+width+'.png'),fullPage:true});}
  await page.evaluate(()=>setVersion('future'));await page.getByText(/材料版本暂不受支持/).waitFor();assert.equal(await page.getByRole('button',{name:'生成报告草稿'}).isDisabled(),true);
  await page.evaluate(()=>setVersion('legacy'));await coverage.waitFor({state:'hidden'});assert.equal(await page.getByRole('button',{name:'生成报告草稿'}).isEnabled(),true);
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
 }finally{await context.close();}
});
