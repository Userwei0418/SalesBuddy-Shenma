// Isolated real React/AntD UI. Synthetic editor state only; no API/DB/model access.
import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {build} from '../node_modules/esbuild/lib/main.js';
import path from 'node:path';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
let browser,js,css;
before(async()=>{
 const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
 import React,{useState} from 'react';import{createRoot}from'react-dom/client';import{App}from'antd';import{PcProvider}from'./department-ui/pc/PcProvider.jsx';
 import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import{ReportFrame,ReportBlank}from'./department-ui/work-report/ReportFrame.jsx';import SchemeEditor from './department-ui/work-report/SchemeEditor.jsx';import DocumentEditor from './department-ui/work-report/DocumentEditor.jsx';
 import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/pc/workspace-layout.css';import './department-ui/pc/overview-filters.css';
 import MaterialReview,{ReportFilters,ReportMetrics,ReportSupplement} from './department-ui/work-report/MaterialReview.jsx';import ReportsList from './department-ui/work-report/ReportsList.jsx';
 import {ShareDialog,ExportDialog,HistoryDrawer} from './department-ui/work-report/ReportDialogs.jsx';
 import {useLeaveGuard} from './department-ui/work-report/useLeaveGuard.js';import {STYLE_CHOICES} from './department-ui/work-report/presentation.js';
 function GuardFixture(){const [dirty,D]=useState(false),[saving,S]=useState(false);const [page]=useState({});window.guardOwner ||= 'fixture-owner';const leave=useLeaveGuard({page,dirty,saving,owner:'fixture-owner',getIdentity:()=>window.guardOwner});window.guard={page,dirty:D,saving:S};return <button onClick={()=>leave(()=>{window.left=true})}>离开编辑器</button>}
 function StateFixture(){const[v,S]=useState({kind:'materials',blocked:true,error:false});window.setScenario=S;
 const materials={periodLabel:'2026-09-28 — 2026-10-11',scopeLabel:'本人',asOfLabel:'统计截至 2026-10-08',coverageState:v.blocked?'failed':'complete',coverageMessage:'跟进材料暂未取得',canGenerate:!v.blocked,sources:[{id:'visits',label:'拜访跟进',description:'按业务日期',countLabel:v.blocked?'读取失败':'共 3 条',complete:!v.blocked,canRead:!v.blocked}],sections:[{id:'progress',title:'重点进展'}],styleLabel:'简洁 · 中文'};
 return <PcProvider><App><main className="department-ui" style={{height:'100vh',padding:20}}>{v.kind==='guard'?<GuardFixture/>:v.kind==='materials'?<ReportFrame tab="compose" onTabChange={()=>{}} overview={<ReportMetrics items={[{id:'visits',label:'跟进记录',complete:!v.blocked,display:'3',unit:'条'}]}/>} filters={<ReportFilters value={{period:'biweek',anchor:'2026-10-08',schemeId:'sales'}} onChange={()=>{}} schemes={[{value:'sales',label:'销售周报'}]}/>}><div className="wr-compose"><MaterialReview value={materials} onGenerate={()=>{window.generated=true}} onSources={()=>{window.sourcesOpened=true}}/><ReportSupplement text="" onChange={()=>{}}/></div></ReportFrame>:v.kind==='share'?<ShareDialog open options={[{value:'allowed-member',label:'授权成员'}]} selected={v.blocked?[]:['allowed-member']} onChange={()=>{}} onClose={()=>{}} onSave={()=>{window.shared=true}} alreadyShared onRevoke={()=>{window.revoked=true}} error={v.error?'共享权限已变化':null}/>:v.kind==='export'?<ExportDialog open value={{title:'旧报告'}} onClose={()=>{}} onCopy={()=>{window.copied=true}} onDownload={()=>{}} error={v.error?'报告权限已变化':null}/>:v.kind==='history'?<HistoryDrawer open onClose={()=>{}} items={[{id:'revision-1',label:'版本 1'}]} onSelect={()=>{}} detail={{title:'旧权限的标题',text:'旧权限的正文'}} detailError={v.error?'无法读取历史版本':null}/>:<ReportsList items={[]} loading={false} onCreate={()=>{}} query={{text:'',status:''}} onQuery={()=>{}} statuses={[]} />}</main></App></PcProvider>}
 const opt=values=>values.map(value=>({value,label:value}));const choices={...STYLE_CHOICES,sources:[{value:'visit',label:'拜访跟进'},{value:'opportunity',label:'商机进展'}]};
 function Fixture(){const[tab,T]=useState('compose'),[scheme,S]=useState({name:'销售周报',sources:['visit'],period:'week',groupBy:'customer',sections:[{id:'overview',title:'本期概览',enabled:true,instructions:''},{id:'progress',title:'重点进展',enabled:true,instructions:''}],references:[],style:{audience:'直属负责人',tone:'concise',length:'medium',voice:'first_person',language:'zh-CN',focus:''}}),[doc,D]=useState({title:'合成周报',sections:[{id:'progress',title:'重点进展',items:[{kind:'fact',text:'合成事实：已完成客户需求确认。',source_refs:['visit:synthetic'],metric_refs:[]}]}],missing_data:[]}),[dirty,C]=useState(false),[saved,V]=useState('');window.fixture={scheme,doc,saved};return <PcProvider><App><main className="department-ui" style={{height:'100vh',padding:20}}><ReportFrame tab={tab} onTabChange={T}>{tab==='schemes'?<div className="wr-scheme-layout"><div className="wr-scheme-list"><button className="wr-scheme-item is-active">销售周报<small>个人方案</small></button></div><SchemeEditor value={scheme} onChange={S} choices={choices} onSave={()=>V('saved')}/></div>:tab==='documents'?<DocumentEditor value={doc} onChange={v=>{D(v);C(true)}} dirty={dirty} onSave={()=>{V(doc.sections[0].items[0].text);C(false)}} onShare={()=>V('shared')} meta="本周 · 本人"/>:<div className="wr-compose"><ReportBlank onPrepare={()=>T('documents')}/><aside className="wr-aside"><div className="wr-panel"><h2>本次报告</h2><p>本人 · 本周</p><p>销售周报</p></div></aside></div>}</ReportFrame></main></App></PcProvider>};createRoot(document.getElementById('root')).render(location.hash === '#states' ? <StateFixture/> : <Fixture/>);
 `},bundle:true,write:false,outdir:'.runtime/report-component-check',define:{'process.env.NODE_ENV':'"production"'}});
 js=built.outputFiles.find(f=>f.path.endsWith('.js')).text;css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
 browser=await chromium.launch({channel:'chrome',headless:true});
});
test('materials and sharing obey supplied availability; denied reads hide prior content',async()=>{
 const context=await browser.newContext({viewport:{width:1440,height:900}});const p=await context.newPage();const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',r=>r.fulfill({contentType:'text/html',body:`<!doctype html><html><head><style>${css}</style></head><body style="margin:0;background:var(--ui-background)"><div id="page-root"><div id="root"></div></div><script>${js}</script></body></html>`}));
 try{
  await p.goto('https://report-components.test/#states');
  await p.getByText('跟进材料暂未取得',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'生成报告草稿'}).isDisabled(),true);
  assert.equal(await p.getByRole('button',{name:'查看材料'}).count(),0);
  await p.evaluate(()=>setScenario({kind:'materials',blocked:false}));
  await p.getByRole('button',{name:'查看材料'}).click();assert.equal(await p.evaluate(()=>window.sourcesOpened),true);
  await p.getByRole('button',{name:'生成报告草稿'}).click();assert.equal(await p.evaluate(()=>window.generated),true);
  if(process.env.REPORT_SHOTS)await p.screenshot({path:path.join(process.env.REPORT_SHOTS,'materials-1440.png'),fullPage:true});
  await p.evaluate(()=>setScenario({kind:'share',blocked:true}));await p.getByRole('dialog').waitFor();assert.equal(await p.getByRole('button',{name:'确认共享',exact:true}).isDisabled(),true);
  await p.evaluate(()=>setScenario({kind:'share',blocked:false,error:true}));await p.getByText('共享权限已变化',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'确认共享',exact:true}).isDisabled(),true);assert.equal(await p.getByRole('button',{name:'取消全部共享'}).isDisabled(),true);
  await p.evaluate(()=>setScenario({kind:'share',blocked:false,error:false}));await p.getByRole('button',{name:'确认共享',exact:true}).click();assert.equal(await p.evaluate(()=>window.shared),true);
  await p.evaluate(()=>setScenario({kind:'export',error:true}));await p.getByText('报告权限已变化',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'复制正文'}).isDisabled(),true);assert.equal(await p.getByText('旧报告',{exact:true}).count(),0);
  await p.evaluate(()=>setScenario({kind:'history',error:true}));await p.getByText('无法读取历史版本',{exact:true}).waitFor();assert.equal(await p.getByText('旧权限的正文',{exact:true}).count(),0);
  await p.evaluate(()=>setScenario({kind:'guard'}));await p.getByRole('button',{name:'离开编辑器'}).waitFor();
  await p.evaluate(()=>guard.dirty(true));await p.getByRole('button',{name:'离开编辑器'}).click();await p.getByRole('button',{name:'继续编辑'}).click();assert.equal(await p.evaluate(()=>window.left),undefined);
  await p.getByRole('button',{name:'离开编辑器'}).click();await p.evaluate(()=>{window.guardOwner='changed-identity'});await p.getByRole('button',{name:'放弃修改并离开'}).click();assert.equal(await p.evaluate(()=>window.left),undefined);
  await p.evaluate(()=>{window.guardOwner='fixture-owner'});await p.getByRole('button',{name:'离开编辑器'}).click();await p.getByRole('button',{name:'放弃修改并离开'}).click();assert.equal(await p.evaluate(()=>window.left),true);
  assert.deepEqual(errors,[]);
 }finally{await context.close();}
});
after(async()=>browser?.close());
for(const width of [1440,1024])test(`report composition, scheme controls and draft confirmation at ${width}px`,async()=>{
 const context=await browser.newContext({viewport:{width,height:900}});const p=await context.newPage();const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',r=>r.fulfill({contentType:'text/html',body:`<!doctype html><html><head><style>${css}</style></head><body style="margin:0"><div id="page-root"><div id="root"></div></div><script>${js}</script></body></html>`}));
 try{
  await p.goto('https://report-components.test');await p.getByRole('button',{name:'准备报告材料'}).click();
  await p.getByRole('textbox',{name:'报告正文：重点进展 1',exact:true}).fill('人工核对后的报告正文');
  assert.equal(await p.getByRole('button',{name:'确认并共享'}).isDisabled(),true);
  await p.getByRole('button',{name:'保存草稿'}).click();assert.equal(await p.evaluate(()=>fixture.saved),'人工核对后的报告正文');
  assert.equal(await p.getByRole('button',{name:'确认并共享'}).isEnabled(),true);
  await p.getByRole('button',{name:'报告方案',exact:true}).click();
  await p.getByRole('button',{name:'上移重点进展'}).click();assert.equal(await p.evaluate(()=>fixture.scheme.sections[0].id),'progress');
  await p.getByRole('button',{name:'添加参考范文',exact:true}).click();await p.getByLabel('参考范文 1',{exact:true}).fill('仅作为写作风格参考');await p.getByRole('button',{name:'添加参考范文',exact:true}).click();await p.getByLabel('参考范文 2',{exact:true}).fill('第二篇表达参考');await p.getByLabel('章节 1 写作要求',{exact:true}).fill('先说结果，再列需要协调的问题');await p.getByLabel('汇报对象',{exact:true}).fill('区域销售负责人');
  await p.getByRole('combobox',{name:'表达风格'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^正式$/}).click();
  await p.getByRole('button',{name:'保存方案'}).click();assert.equal(await p.evaluate(()=>fixture.scheme.style.tone),'formal');assert.equal(await p.evaluate(()=>fixture.scheme.references[0].text),'仅作为写作风格参考');assert.equal(await p.evaluate(()=>fixture.scheme.references[1].text),'第二篇表达参考');assert.equal(await p.evaluate(()=>fixture.scheme.sections[0].instructions),'先说结果，再列需要协调的问题');assert.equal(await p.evaluate(()=>fixture.scheme.style.audience),'区域销售负责人');
  assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.ok(await p.locator('#wr-scheme-name').evaluate(el=>el.getBoundingClientRect().height>=32));assert.deepEqual(errors,[]);
  await p.getByRole('heading',{name:'编辑报告方案'}).click();
  await p.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});
  if(process.env.REPORT_SHOTS){await fs.mkdir(process.env.REPORT_SHOTS,{recursive:true});await p.screenshot({path:path.join(process.env.REPORT_SHOTS,`scheme-${width}.png`),fullPage:true});await p.getByRole('button',{name:'保存方案'}).scrollIntoViewIfNeeded();await p.screenshot({path:path.join(process.env.REPORT_SHOTS,`scheme-${width}-actions.png`),fullPage:true});}
 }finally{await context.close();}
});
