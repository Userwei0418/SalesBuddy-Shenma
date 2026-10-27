import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from'react';import{createRoot}from'react-dom/client';import{App}from'antd';import{PcProvider}from'./department-ui/pc/PcProvider.jsx';import ReportCompose from'./department-ui/work-report/ReportCompose.jsx';
import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';
import './department-ui/pc/pc-theme.css';import './department-ui/pc/workspace-layout.css';import './department-ui/pc/overview-filters.css';
window.SalesRuntime={app:{globalData:{session:{}}},require:()=>({identity:()=>window.owner})};window.owner='owner';window.calls=[];window.snapReads=[];window.runReads=[];window.opened=[];window.sourceFailure=0;window.holdSnapshots=false;
const id='00000000-0000-4000-8000-000000000001';
const config={sections:[{id:'progress',title:'重点进展',enabled:true,instructions:''}],group_by:'topic',style:{audience:'直属负责人',tone:'concise',length:'medium',voice:'first_person',language:'zh-CN',focus:[]},reference_texts:[],default_period:'week',default_sources:['visit']};
if(location.hash==='#weekly'){config.facts_contract='work_report.sales_week_facts.v1';config.sections=[{id:'summary',title:'本周工作总结',enabled:true,instructions:''},{id:'next_week_plan',title:'下周工作计划',enabled:true,instructions:''},{id:'next_week_collection',title:'下周预计回款',enabled:true,instructions:''}];}
const schemes=[{id,version_no:3,name:'销售周报',config,visibility:'private'}];
window.ready=(snapshotId,selection)=>({id:snapshotId,status:'ready',selection,period:{label:'9月28日—10月11日'},scope:{label:'合成成员'},data_as_of:'2026-10-09T08:00:00+08:00',snapshot_hash:'a'.repeat(64),coverage:['visit','opportunity','task','risk'].map(source_type=>({source_type,selected:selection.sources.includes(source_type),status:selection.sources.includes(source_type)?'complete':'not_selected',included:selection.sources.includes(source_type)?2:0,total:selection.sources.includes(source_type)?2:null,missing_reasons:[]})),metrics:[{metric_id:'visit.count',label:'本期跟进',display_value:'9',complete:true,period_basis:'occurred_in_period',definition:'来自后端完整范围'}]});
window.selections={};window.prepareNumber=0;
const source=n=>({source_id:'visit:'+n,source_type:'visit',title:'合成材料'+n,text:'授权范围内材料',contributors:[],state_kind:'occurred',business_at:'2026-10-08T08:00:00+08:00'});
const service={schemes:async()=>({items:schemes,has_more:false}),prepare:async selection=>{calls.push({kind:'prepare',selection});const snapshotId='snapshot-'+ ++prepareNumber;selections[snapshotId]=selection;return{id:snapshotId}},snapshot:async snapshotId=>{if(holdSnapshots)return new Promise(resolve=>snapReads.push({snapshotId,resolve}));return ready(snapshotId,selections[snapshotId])},
sources:async(snapshotId,query)=>{calls.push({kind:'sources',snapshotId,query});if(query.offset && sourceFailure)throw Object.assign(Error('材料权限已变化'),{status:sourceFailure});return{items:[source(query.offset+1)],has_more:query.offset===0,next_offset:query.offset===0?1:null}},
generate:async payload=>{calls.push({kind:'generate',payload});return{id:'run-'+calls.filter(c=>c.kind==='generate').length}},run:runId=>new Promise(resolve=>runReads.push({runId,resolve})),cancel:async(runId,payload)=>{calls.push({kind:'cancel',runId,payload});return{id:runId,status:'cancelled'}}};
window.options={periods:['day','week','two_weeks','month','quarter'],available_scope_kinds:['self','member','team','workspace'],members:[{id:'00000000-0000-4000-8000-000000000002',name:'合成成员'}],teams:[{id:'00000000-0000-4000-8000-000000000003',name:'合成部门'}],sources:['visit','opportunity','task','risk'].map(id=>({id,label:{visit:'拜访跟进',opportunity:'商机进展',task:'任务完成',risk:'风险情况'}[id],available:true})),default_scheme:config,can_create:true,generation_available:true};
window.reportPage={};window.root=createRoot(document.getElementById('root'));window.render=()=>root.render(<PcProvider><App><main className="department-ui" style={{height:'100vh',padding:20}}><ReportCompose page={reportPage} owner={owner} options={options} service={service} pollDelay={25} onDocument={id=>opened.push(id)}/></main></App></PcProvider>);render();
`},bundle:true,write:false,outdir:'.runtime/compose-check',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
async function fixture(run,hash='') {
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1440,height:1050}}),p=await context.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));await context.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><head><style>'+css+'</style></head><div id="page-root"><div id="root"></div></div><script>'+js+'</script>'}));
 p.setDefaultTimeout(10000);
 try{await p.goto('https://report-compose.test/'+hash);await p.getByRole('button',{name:'准备材料',exact:true}).waitFor();await run(p);assert.deepEqual(errors,[]);}catch(error){
  await fs.mkdir(root+'.runtime/work-report-compose-shots',{recursive:true});await p.screenshot({path:root+'.runtime/work-report-compose-shots/failure.png',fullPage:true});await fs.writeFile(root+'.runtime/work-report-compose-shots/failure.txt',await p.locator('body').innerText());throw error;
 }finally{await context.close();await browser.close();}
}
async function prepare(p) {await p.getByRole('button',{name:await p.getByRole('button',{name:'重新准备',exact:true}).count()?'重新准备':'准备材料',exact:true}).click();await p.getByRole('button',{name:'生成报告草稿',exact:true}).waitFor();}

test('compose sends natural periods and authorized selectors, retains server statistics, and clears denied source pages',()=>fixture(async p=>{
 await p.getByRole('combobox',{name:'报告周期'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^双周报$/}).click();
 await p.getByRole('combobox',{name:'报告范围'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^按成员$/}).click();
 await p.getByRole('combobox',{name:'报告成员'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^合成成员$/}).click();
 await p.getByLabel('周期内日期').fill('2026-10-08');await p.getByLabel('补充说明').fill('下期需要协调演示时间');await prepare(p);
 const selection=await p.evaluate(()=>calls.find(c=>c.kind==='prepare').selection);
 assert.deepEqual(selection.period,{kind:'two_weeks',anchor_date:'2026-10-08',timezone:'Asia/Shanghai'});assert.deepEqual(selection.scope,{contract_version:'work_report.scope_selection.v2',kind:'member',member_ids:['00000000-0000-4000-8000-000000000002']});assert.equal(selection.supplements[0].declared_kind,'context');assert.equal(selection.supplements[0].text,'下期需要协调演示时间');assert.equal('metrics' in selection,false);assert.equal('member_id' in selection.scope,false);
 assert.match(await p.getByRole('region',{name:'报告材料统计'}).innerText(),/9/);
 await fs.mkdir(root+'.runtime/work-report-compose-shots',{recursive:true});
 for(const width of [1440,1024]){await p.setViewportSize({width,height:900});await p.getByText('本次报告材料',{exact:true}).click();await p.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});await p.screenshot({path:root+'.runtime/work-report-compose-shots/materials-'+width+'.png',fullPage:false});assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));}
 await p.setViewportSize({width:1440,height:1050});
 await p.getByRole('button',{name:'查看材料',exact:true}).click();await p.getByText('合成材料1',{exact:true}).waitFor();await p.getByRole('button',{name:'加载更多来源',exact:true}).click();await p.getByText('合成材料2',{exact:true}).waitFor();
 assert.deepEqual(await p.evaluate(()=>calls.filter(c=>c.kind==='sources').map(c=>c.query.offset)),[0,1]);assert.equal(await p.getByRole('button',{name:'加载更多来源',exact:true}).count(),0);
 assert.match(await p.getByRole('region',{name:'报告材料统计'}).innerText(),/9/,'source pages cannot replace trusted full-scope statistics');
 await p.getByRole('button',{name:'关闭',exact:true}).click();await p.getByRole('button',{name:'查看材料',exact:true}).click();await p.getByText('合成材料1',{exact:true}).waitFor();await p.evaluate(()=>sourceFailure=403);await p.getByRole('button',{name:'加载更多来源',exact:true}).click();await p.getByText('材料权限已变化',{exact:true}).waitFor();
 assert.equal(await p.getByText('合成材料1',{exact:true}).count(),0);assert.equal(await p.getByRole('button',{name:'生成报告草稿',exact:true}).count(),0);assert.equal(await p.getByRole('region',{name:'报告材料统计'}).count(),0);
}));

test('only the submitted supplement is recoverable from a successful run; later edits remain dirty',()=>fixture(async p=>{
 await p.getByLabel('补充说明',{exact:true}).fill('已提交本次报告的补充');await prepare(p);await p.getByRole('button',{name:'生成报告草稿',exact:true}).click();await p.waitForFunction(()=>runReads.length===1);
 await p.evaluate(()=>runReads[0].resolve({id:'run-1',status:'running',version_no:1}));await p.waitForFunction(()=>runReads.length===2);
 const prevented=()=>p.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;});
 assert.equal(await prevented(),false,'the exact submitted supplement is held by the recoverable run');
 await p.evaluate(()=>runReads[1].resolve({id:'run-1',status:'succeeded',result_status:'generated',document_id:'saved-document',version_no:2}));await p.getByText('已生成',{exact:true}).waitFor();assert.equal(await prevented(),false);
 await p.getByLabel('补充说明',{exact:true}).fill('成功后新增且未提交的补充');assert.equal(await prevented(),true);
 await p.evaluate(()=>{window.left=false;reportPage._requestWebExit(()=>left=true);});await p.getByRole('button',{name:'继续编辑',exact:true}).click();
 assert.equal(await p.evaluate(()=>left),false);assert.equal(await p.getByLabel('补充说明',{exact:true}).inputValue(),'成功后新增且未提交的补充');
}));

test('condition changes drop late snapshots and cancellation drops late run completion',()=>fixture(async p=>{
 await p.evaluate(()=>holdSnapshots=true);await p.getByRole('button',{name:'准备材料',exact:true}).click();await p.waitForFunction(()=>snapReads.length===1);
 await p.getByLabel('周期内日期').fill('2026-10-07');await p.evaluate(()=>{holdSnapshots=false;snapReads[0].resolve(ready(snapReads[0].snapshotId,selections[snapReads[0].snapshotId]))});
 await p.waitForTimeout(70);assert.equal(await p.getByRole('button',{name:'生成报告草稿',exact:true}).count(),0);
 await prepare(p);await p.getByRole('button',{name:'生成报告草稿',exact:true}).click();await p.waitForFunction(()=>runReads.length===1);
 const payload=await p.evaluate(()=>calls.find(c=>c.kind==='generate').payload);assert.equal(payload.snapshot_id,'snapshot-2');assert.equal(payload.snapshot_hash,'a'.repeat(64));assert.equal(payload.scheme.version_no,3);
 await p.evaluate(()=>runReads[0].resolve({id:'run-1',status:'running',version_no:4}));await p.waitForFunction(()=>runReads.length===2);await p.getByRole('button',{name:'取消生成',exact:true}).click();await p.waitForFunction(()=>runReads.length===3);
 await p.evaluate(()=>{runReads[1].resolve({id:'run-1',status:'succeeded',document_id:'late-document',version_no:5});runReads[2].resolve({id:'run-1',status:'cancelled',version_no:5})});await p.getByText('已取消',{exact:true}).waitFor();
 assert.deepEqual(await p.evaluate(()=>opened),[]);assert.deepEqual(await p.evaluate(()=>calls.find(c=>c.kind==='cancel').payload),{version_no:4});
 await prepare(p);await p.getByRole('button',{name:'生成报告草稿',exact:true}).click();await p.waitForFunction(()=>runReads.length===4);
 await p.evaluate(()=>runReads[3].resolve({id:'run-2',status:'succeeded',result_status:'basic_summary',document_id:'saved-draft',version_no:2}));await p.getByText('基础汇总',{exact:true}).waitFor();assert.deepEqual(await p.evaluate(()=>opened),['saved-draft']);
}));

test('weekly supplement target is frozen with the input and changing it invalidates saved material',()=>fixture(async p=>{
 await p.getByRole('combobox',{name:'补充内容用于'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^下周预计回款$/}).click();
 await p.getByLabel('补充说明',{exact:true}).fill('客户甲预计下周回款 10 万元，尚待确认');await prepare(p);
 const selection=await p.evaluate(()=>calls.find(c=>c.kind==='prepare').selection);
 assert.equal(selection.facts_contract,'work_report.sales_week_facts.v1');assert.equal(selection.supplements[0].section_id,'next_week_collection');assert.equal(selection.supplements[0].declared_kind,'plan');
 await p.getByRole('button',{name:'生成报告草稿',exact:true}).click();await p.waitForFunction(()=>runReads.length===1);
 await p.evaluate(()=>runReads[0].resolve({id:'run-1',status:'succeeded',result_status:'generated',document_id:'saved-document',version_no:2}));await p.getByText('已生成',{exact:true}).waitFor();
 const prevented=()=>p.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented;});
 assert.equal(await prevented(),false);
 await p.getByRole('combobox',{name:'补充内容用于'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^下周工作计划$/}).click();
 assert.equal(await prevented(),true,'moving the same text to another chapter creates unsaved material');
 assert.equal(await p.getByRole('button',{name:'生成报告草稿',exact:true}).count(),0);
 await p.getByRole('combobox',{name:'报告周期'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^月报$/}).click();
 // This fixture provides only a weekly scheme and no server default for month.
 // Period switching now drops incompatible schemes instead of retaining one.
 await p.getByText('请先选择或创建报告方案',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'准备材料',exact:true}).isDisabled(),true);
 assert.equal(await p.evaluate(()=>calls.filter(c=>c.kind==='prepare').length),1);
 await fs.mkdir(root+'.runtime/work-report-compose-shots',{recursive:true});await p.screenshot({path:root+'.runtime/work-report-compose-shots/weekly-target.png',fullPage:true});
},'#weekly'));
