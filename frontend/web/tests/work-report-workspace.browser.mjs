import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const documentId='10000000-0000-4000-8000-000000000001',runId='20000000-0000-4000-8000-000000000001',schemeId='30000000-0000-4000-8000-000000000001';
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from'react';import{createRoot}from'react-dom/client';import{App}from'antd';import{PcProvider}from'./department-ui/pc/PcProvider.jsx';import ReportWorkspace from'./department-ui/work-report/ReportWorkspace.jsx';
import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';import './department-ui/pc/workspace-layout.css';import './department-ui/pc/overview-filters.css';
window.calls=[];window.runReads=[];window.runRejects=[];window.page={route:'pages/work-report/index',options:Object.fromEntries(new URLSearchParams(location.search))};
window.SalesRuntime={app:{globalData:{session:{userId:'actor'}}},require:()=>({identity:()=> 'owner'}),replacePageQuery:(page,changes)=>{calls.push({kind:'route',changes});const query=new URLSearchParams(location.search);for(const[k,v]of Object.entries(changes)){v==null?query.delete(k):query.set(k,v)}page.options=Object.fromEntries(query);history.replaceState({},'',location.pathname+'?'+query);return true}};
const config={sections:[{id:'progress',title:'重点进展',enabled:true,instructions:''}],group_by:'topic',style:{audience:'直属负责人',tone:'concise',length:'medium',voice:'first_person',language:'zh-CN',focus:[]},reference_texts:[],default_period:'week',default_sources:['visit']};
const options={can_create:true,generation_available:true,default_scheme:config,periods:['day','week','two_weeks','month','quarter'],available_scope_kinds:['self'],members:[],teams:[],sources:[{id:'visit',label:'拜访跟进',available:true}],share_targets:{allowed_kinds:[],members:[],teams:[]}};
const doc={id:'${documentId}',owner_id:'actor',version_no:1,title:'已保存的合成周报',period:{label:'本周'},scope:{label:'本人'},snapshot_id:'snapshot',snapshot_hash:'a'.repeat(64),scheme:{scheme_id:'${schemeId}',version_no:1,name:'销售周报',config_hash:'b'.repeat(64)},status:'private',latest_revision_id:'revision',shared_revision_id:null,revision:{id:'revision',revision_no:1,origin:'generated',content:{title:'已保存的合成周报',sections:[{id:'progress',title:'重点进展',items:[{kind:'fact',text:'合成已保存正文',source_refs:['visit:one'],metric_refs:[]}]}],missing_data:[]}},coverage:[],metrics:[],can_edit:true,can_share:true,can_export:true,updated_at:'2026-10-09T08:00:00+08:00'};
const service={options:async()=>options,overviewOptions:async()=>{throw Error('合成概览尚未就绪')},schemes:async()=>({items:[{id:'${schemeId}',version_no:1,name:'销售周报',visibility:'private',config,can_edit:true}],has_more:false}),scheme:async()=>({id:'${schemeId}',version_no:1,name:'销售周报',visibility:'private',config,can_edit:true}),documents:async query=>{calls.push({kind:'documents',query});return{items:[doc],total:query.q?1:81}},document:async id=>{calls.push({kind:'document',id});return doc},run:id=>{calls.push({kind:'run',id});return new Promise((resolve,reject)=>{runReads.push(resolve);runRejects.push(reject)})}};
createRoot(document.getElementById('root')).render(<PcProvider><App><main className="department-ui" style={{height:'100vh',padding:20}}><ReportWorkspace page={page} service={service}/></main></App></PcProvider>);
`},bundle:true,write:false,outdir:'.runtime/workspace-check',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
async function fixture(query,run) {
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1440,height:900}}),p=await context.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(10000);
 await context.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><head><style>'+css+'</style></head><div id="page-root"><div id="root"></div></div><script>'+js+'</script>'}));
 try{await p.goto('https://report-workspace.test/?'+query);await p.getByRole('heading',{name:'工作报告',exact:true}).waitFor();await run(p);assert.deepEqual(errors,[]);}finally{await context.close();await browser.close();}
}
test('report workspace guards unsaved tab switches and exposes scheme configuration in the same module',()=>fixture('report_tab=documents&document_id='+documentId,async p=>{
 await p.getByRole('button',{name:'编辑报告',exact:true}).click();
 await p.getByLabel('报告正文：重点进展 1',{exact:true}).fill('未保存的本次编辑');await p.getByRole('button',{name:'报告方案',exact:true}).click();await p.getByRole('button',{name:'继续编辑',exact:true}).click();assert.equal(await p.getByLabel('报告正文：重点进展 1',{exact:true}).inputValue(),'未保存的本次编辑');
 await p.getByRole('button',{name:'报告方案',exact:true}).click();await p.getByRole('button',{name:'放弃修改并离开',exact:true}).click();await p.getByRole('button',{name:'销售周报 个人方案',exact:true}).click();await p.getByLabel('方案名称',{exact:true}).waitFor();assert.equal(new URL(p.url()).searchParams.get('report_tab'),'schemes');assert.equal(new URL(p.url()).searchParams.has('document_id'),false);
 await p.getByRole('button',{name:'使用此方案生成报告',exact:true}).click();await p.getByRole('button',{name:'准备报告材料',exact:true}).waitFor();assert.equal(new URL(p.url()).searchParams.get('scheme_id'),schemeId);
 await p.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent==='准备材料' && !button.disabled));
 await fs.mkdir(root+'.runtime/work-report-workspace-shots',{recursive:true});await p.setViewportSize({width:1024,height:900});
 await p.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 assert.equal(await p.getByRole('heading',{name:'工作报告',exact:true}).count(),1);
 await p.screenshot({path:root+'.runtime/work-report-workspace-shots/compose-1024.png',fullPage:false,animations:'disabled'});assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
}));
test('refresh resumes the saved run reference by GET and opens its persisted document',()=>fixture('report_tab=compose&run_id='+runId,async p=>{
 await p.waitForFunction(()=>runReads.length===1);assert.deepEqual(await p.evaluate(()=>calls.filter(c=>c.kind==='run').map(c=>c.id)),[runId]);await p.reload();await p.waitForFunction(()=>runReads.length===1);assert.deepEqual(await p.evaluate(()=>calls.filter(c=>c.kind==='run').map(c=>c.id)),[runId]);
 await p.evaluate(({runId,documentId})=>runReads[0]({id:runId,status:'succeeded',result_status:'generated',document_id:documentId,version_no:2}),{runId,documentId});await p.getByLabel('报告阅读视图',{exact:true}).waitFor();assert.equal(new URL(p.url()).searchParams.get('document_id'),documentId);assert.equal(new URL(p.url()).searchParams.has('run_id'),false);await p.getByText('合成已保存正文',{exact:true}).waitFor();assert.equal(await p.getByLabel('报告正文：重点进展 1',{exact:true}).count(),0);
 const writes=await p.evaluate(()=>calls.filter(c=>!['run','route','document'].includes(c.kind)));assert.deepEqual(writes,[]);
}));
test('long-running report survives overview and navigation without another generation',()=>fixture('report_tab=compose&run_id='+runId,async p=>{
 await p.waitForFunction(()=>runReads.length===1);
 await p.evaluate(runId=>runReads[0]({id:runId,status:'running',version_no:1}),runId);
 await p.locator('b').filter({hasText:/^正在生成报告$/}).waitFor();
 await p.getByRole('button',{name:'本期概览',exact:true}).click();await p.getByRole('button',{name:'查看生成状态',exact:true}).waitFor();
 await p.getByRole('button',{name:'生成报告',exact:true}).click();await p.waitForFunction(()=>runReads.length===2);
 assert.equal(new URL(p.url()).searchParams.get('run_id'),runId);
 // A fresh module entry has no run in the URL; only an ID is recovered from
 // this tab, then reauthorized through GET before any status/content is shown.
 await p.goto('https://report-workspace.test/?report_tab=documents');await p.getByRole('button',{name:'查看生成状态',exact:true}).click();
 await p.waitForFunction(()=>runReads.length===1);assert.deepEqual(await p.evaluate(()=>calls.filter(c=>c.kind==='run').map(c=>c.id)),[runId]);
 assert.equal(await p.locator('b').filter({hasText:/^正在生成报告$/}).count(),0);
 await p.evaluate(runId=>runReads[0]({id:runId,status:'running',version_no:2}),runId);await p.locator('b').filter({hasText:/^正在生成报告$/}).waitFor();
 // The passage of report time does not turn a running server task into failure.
 await p.evaluate(()=>{const original=Date.now;Date.now=()=>original()+10*60*1000;});await p.waitForFunction(()=>runReads.length===2);
 await p.evaluate(({runId,documentId})=>runReads[1]({id:runId,status:'succeeded',result_status:'generated',document_id:documentId,version_no:3}),{runId,documentId});
 await p.getByLabel('报告阅读视图',{exact:true}).waitFor();
 assert.equal(await p.evaluate(()=>sessionStorage.getItem('sales-web:work-report:run:v1')),null);
 assert.deepEqual(await p.evaluate(()=>calls.filter(c=>!['run','route','document','documents'].includes(c.kind))),[]);
}));
test('temporary status failure preserves recovery, while inaccessible records can be left safely',()=>fixture('report_tab=compose&run_id='+runId,async p=>{
 await p.waitForFunction(()=>runReads.length===1);await p.evaluate(()=>runRejects[0](Object.assign(Error('状态读取暂时超时'),{statusCode:504})));
 await p.getByRole('button',{name:'重新读取状态',exact:true}).waitFor();assert.ok(await p.evaluate(()=>sessionStorage.getItem('sales-web:work-report:run:v1')));
 assert.equal(await p.getByRole('button',{name:'准备报告材料',exact:true}).count(),0);
 await p.getByRole('button',{name:'重新读取状态',exact:true}).click();await p.waitForFunction(()=>runReads.length===2);
 await p.evaluate(()=>runRejects[1](Object.assign(Error('未获授权'),{statusCode:403})));
 await p.getByText('这份生成记录已无法访问',{exact:true}).waitFor();assert.equal(await p.evaluate(()=>sessionStorage.getItem('sales-web:work-report:run:v1')),null);
 await p.getByRole('button',{name:'返回本期概览',exact:true}).click();assert.equal(new URL(p.url()).searchParams.has('run_id'),false);
 assert.deepEqual(await p.evaluate(()=>calls.filter(c=>!['run','route'].includes(c.kind))),[]);
}));
test('using a scheme during a rewrite resumes the original document instead of starting a second run',()=>fixture('report_tab=documents&document_id='+documentId+'&run_id='+runId+'&run_document_id='+documentId,async p=>{
 await p.waitForFunction(()=>runReads.length===1);
 await p.getByRole('button',{name:'报告方案',exact:true}).click();await p.getByRole('button',{name:'销售周报 个人方案',exact:true}).click();
 await p.getByRole('button',{name:'使用此方案生成报告',exact:true}).click();await p.waitForFunction(()=>runReads.length===2);
 assert.equal(new URL(p.url()).searchParams.get('document_id'),documentId);assert.equal(new URL(p.url()).searchParams.get('run_id'),runId);
 assert.equal(await p.getByRole('button',{name:'准备报告材料',exact:true}).count(),0);
 assert.deepEqual(await p.evaluate(()=>calls.filter(c=>!['run','route','document'].includes(c.kind))),[]);
}));
test('document search commits on Enter and asks the server for the requested page',()=>fixture('report_tab=documents',async p=>{
 await p.getByRole('button',{name:'已保存的合成周报',exact:true}).waitFor();await p.getByRole('searchbox',{name:'搜索报告名称'}).fill('销售');assert.equal(await p.evaluate(()=>calls.filter(c=>c.kind==='documents').length),1);await p.getByRole('searchbox',{name:'搜索报告名称'}).press('Enter');await p.waitForFunction(()=>calls.filter(c=>c.kind==='documents').length===2);assert.equal(await p.evaluate(()=>calls.filter(c=>c.kind==='documents').at(-1).query.q),'销售');
 await p.getByRole('searchbox',{name:'搜索报告名称'}).fill('');await p.getByRole('searchbox',{name:'搜索报告名称'}).press('Enter');await p.getByTitle('2',{exact:true}).click();await p.waitForFunction(()=>calls.some(c=>c.kind==='documents' && c.query.offset>0 && c.query.offset===c.query.limit));
 assert.equal(new URL(p.url()).searchParams.has('text'),false);
}));
test('supplements edited after insufficient data keep tab and refresh leave protection',()=>fixture('report_tab=compose&run_id='+runId,async p=>{
 await p.waitForFunction(()=>runReads.length===1);await p.evaluate(runId=>runReads[0]({id:runId,status:'succeeded',result_status:'insufficient_data',document_id:null,version_no:2}),runId);
 await p.getByText('材料不足',{exact:true}).waitFor();await p.getByLabel('补充说明',{exact:true}).fill('需要保留的新增补充');
 assert.equal(await p.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;}),true);
 await p.getByRole('button',{name:'报告方案',exact:true}).click();await p.getByRole('button',{name:'继续编辑',exact:true}).click();assert.equal(await p.getByLabel('补充说明',{exact:true}).inputValue(),'需要保留的新增补充');
 await p.getByRole('button',{name:'报告方案',exact:true}).click();await p.getByRole('button',{name:'放弃修改并离开',exact:true}).click();await p.getByRole('button',{name:'销售周报 个人方案',exact:true}).waitFor();
 assert.equal(new URL(p.url()).searchParams.get('report_tab'),'schemes');
}));
