import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
let browser,html;
before(async()=>{
  const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
    import React,{useState}from'react';import{createRoot}from'react-dom/client';import{App}from'antd';import{PcProvider}from'./department-ui/pc/PcProvider.jsx';
    import MeetingBoardView from './department-ui/work-report/MeetingBoardView.jsx';
    import{withTaskActivity}from'./department-ui/work-report/task-activity.js';
    import{taskActivityFixture,taskAsOf,taskId,taskCount}from'./tests/fixtures/work-report-task-activity.mjs';
    import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';
    import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/work-report/work-report.css';
    const base={title:'日报 · 合成任务验证',scopeLabel:'示例团队',periodLabel:'2026-10-12',asOfLabel:'统计截至 2026/10/12 11:00',quarterLabel:'2026 Q4',
      counts:[{id:'customers',label:'新认领客户',value:0,unit:'家'},{id:'followups',label:'正式跟进',value:0,unit:'条'},{id:'new_opportunities',label:'新增商机',value:0,unit:'个'},{id:'lost',label:'丢单商机',value:0,unit:'个'}],
      amounts:[],changes:[],customers:[],opportunities:[],followups:[],nextSteps:[],members:[],notices:[],definitions:[]};
    function Fixture(){const[member,setMember]=useState(null),activity=taskActivityFixture();
      window.nav=[];const shown=withTaskActivity(base,activity,{memberId:member,dataAsOf:taskAsOf});
      if(!member)shown.members=[{id:taskId(2),name:'成员乙',summary:'查看本期任务'}];
      return <PcProvider><App><main className="department-ui" style={{height:'100vh',overflow:'auto',padding:20}}>
        <MeetingBoardView key={member || 'team'} value={shown} onMember={row=>setMember(row.id)} onBack={member?()=>setMember(null):undefined}
          onOpenTask={row=>window.nav.push(['task',row.taskId])} onOpenCustomer={row=>window.nav.push(['customer',row.customerId])} onOpenOpportunity={row=>window.nav.push(['opportunity',row.opportunityId])}/>
      </main></App></PcProvider>};createRoot(document.getElementById('root')).render(<Fixture/>);
  `},bundle:true,write:false,outdir:'.runtime/task-overview-fixture',define:{'process.env.NODE_ENV':'"production"'}});
  const js=built.outputFiles.find(file=>file.path.endsWith('.js')).text,css=built.outputFiles.find(file=>file.path.endsWith('.css')).text;
  html=`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>${css}</style></head><body style="margin:0;background:var(--ui-background)"><div id="page-root"><div id="root"></div></div><script>${js}</script></body></html>`;
  browser=await chromium.launch({channel:'chrome',headless:true});
});
after(async()=>browser?.close());

for(const width of [1440,1024,600])test(`task KPI drilldown, owner scope and readonly meeting at ${width}px`,async()=>{
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.setDefaultTimeout(5000);
  await context.route('**/*',route=>route.fulfill({contentType:'text/html',body:html}));
  try{
    await page.goto('https://task-overview.test/');
    await page.getByRole('button',{name:'查看本期新增任务',exact:true}).waitFor();
    const counts=page.locator('.wr-change-counts');
    assert.equal(await counts.locator('button').count(),6);
    const boxes=await counts.locator('button').evaluateAll(rows=>rows.map(row=>({x:row.getBoundingClientRect().x,y:row.getBoundingClientRect().y})));
    assert.equal(new Set(boxes.map(row=>Math.round(row.y))).size,width>1050?1:width>700?2:3);
    if(process.env.REPORT_SHOTS){await fs.mkdir(process.env.REPORT_SHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.REPORT_SHOTS,`task-summary-${width}.png`),fullPage:true});}
    await page.getByRole('button',{name:'查看本期完成任务',exact:true}).click();
    assert.equal(await page.getByRole('tab',{name:'任务情况',exact:true}).getAttribute('aria-selected'),'true');
    await page.getByRole('button',{name:'上周创建、本期完成后重新打开',exact:true}).waitFor();
    const oldRow=page.getByRole('row').filter({hasText:'上周创建、本期完成后重新打开'});
    assert.match(await oldRow.innerText(),/2026\/10\/08/);assert.match(await oldRow.innerText(),/2026\/10\/12/);assert.match(await oldRow.innerText(),/执行中/);
    await page.getByRole('button',{name:'上周创建、本期完成后重新打开',exact:true}).click();
    assert.equal((await page.evaluate(()=>window.nav))[0][0],'task');
    await page.getByRole('button',{name:'查看本期新增任务',exact:true}).click();
    assert.equal(await page.getByRole('button',{name:'上周创建、本期完成后重新打开',exact:true}).count(),0);
    await page.getByRole('button',{name:'共同核对交付计划',exact:true}).waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    if(process.env.REPORT_SHOTS){await fs.mkdir(process.env.REPORT_SHOTS,{recursive:true});await page.screenshot({path:path.join(process.env.REPORT_SHOTS,`task-overview-${width}.png`),fullPage:true});}
    await page.getByRole('button',{name:/成员乙.*查看本期任务/}).click();
    assert.match(await page.getByRole('button',{name:'查看本期新增任务',exact:true}).innerText(),/1/);
    await page.getByRole('button',{name:'查看本期完成任务',exact:true}).click();
    assert.equal(await page.getByRole('button',{name:'确认本周回款日期',exact:true}).count(),0);
    await page.getByRole('button',{name:'上周创建、本期完成后重新打开',exact:true}).waitFor();
    await page.getByRole('button',{name:'会议模式',exact:true}).click();
    const dialog=page.getByRole('dialog');await dialog.waitFor();
    await page.waitForFunction(()=>document.activeElement?.classList.contains('wr-presentation-body'));
    await dialog.getByRole('combobox',{name:'跳转章节或成员'}).click();
    const popup=page.locator('.ant-select-dropdown:visible');
    await popup.hover();await page.mouse.wheel(0,480);
    await popup.getByText('本期完成任务',{exact:true}).click({timeout:5000});
    await dialog.getByRole('heading',{name:'本期完成任务',exact:true}).waitFor();
    assert.equal(await dialog.getByRole('button',{name:'上周创建、本期完成后重新打开',exact:true}).count(),0);
    assert.ok((await dialog.innerText()).includes('上周创建、本期完成后重新打开'));
    if(process.env.REPORT_SHOTS)await page.screenshot({path:path.join(process.env.REPORT_SHOTS,`task-meeting-${width}.png`),fullPage:true});
    await dialog.getByRole('button',{name:'退出会议模式 · Esc',exact:true}).click();
    await dialog.waitFor({state:'hidden'});
    assert.equal(await page.getByRole('tab',{name:'任务情况',exact:true}).getAttribute('aria-selected'),'true');
    assert.deepEqual(errors,[]);
  }catch(error){
    if(process.env.REPORT_SHOTS)await page.screenshot({path:path.join(process.env.REPORT_SHOTS,`failure-${width}.png`)});
    throw error;
  }finally{await context.close();}
});
