import {test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from '../node_modules/esbuild/lib/main.js';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from'react';import{createRoot}from'react-dom/client';import{App}from'antd';import{PcProvider}from'./department-ui/pc/PcProvider.jsx';import ReportDocument from'./department-ui/work-report/ReportDocument.jsx';
import './runtime.css';import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.SalesRuntime={app:{globalData:{session:{}}},require:()=>({identity:()=> window.actorIdentity})};window.actorIdentity='owner';window.calls=[];window.documentReads=0;window.readFailure=0;window.holdReads=false;window.readFlights=[];window.saveTransportFailure=0;window.commitBeforeFailure=true;window.saveFailure=0;window.exportFailure=0;window.runReads=[];
const clone=value=>JSON.parse(JSON.stringify(value));const content={title:'合成销售周报',sections:[{id:'progress',title:'重点进展',items:[{kind:'fact',text:'已完成需求确认',source_refs:['visit:one'],metric_refs:[]}]}],missing_data:[]};
const revision={id:'revision-1',document_id:'document',revision_no:1,origin:'generated',created_at:'2026-10-09T08:00:00+08:00',content};
window.currentDocument={id:'document',owner_id:'actor',version_no:1,title:content.title,period:{label:'本周'},scope:{label:'本人'},snapshot_id:'snapshot',snapshot_hash:'a'.repeat(64),scheme:{scheme_id:'scheme',version_no:4,name:'销售周报',config_hash:'b'.repeat(64)},status:'private',latest_revision_id:revision.id,shared_revision_id:null,revision,metrics:[],coverage:[],can_edit:true,can_share:true,can_export:true};window.historyRows=[clone(revision)];
if(location.hash==='#reader'){Object.assign(currentDocument,{owner_id:'other',status:'shared',can_edit:false,can_share:false,can_export:false,shared_revision_id:'revision-1'});}
const transportError=()=>Object.assign(Error('request:fail 请求超时'),saveTransportFailure===500?{statusCode:500}:{code:'NETWORK_ERROR'});
const service={document:async()=>{documentReads++;if(readFailure)throw Object.assign(Error('读取暂时失败'),{statusCode:readFailure});if(holdReads)return new Promise(resolve=>readFlights.push(resolve));return clone(currentDocument)},saveRevision:async(id,payload)=>{calls.push({kind:'save',payload});if(saveFailure)throw Object.assign(Error('版本冲突，保留当前编辑'),{status:saveFailure});if(saveTransportFailure && !commitBeforeFailure)throw transportError();const n=currentDocument.version_no+1;const revision={id:'revision-'+n,document_id:id,revision_no:n,origin:'manual',created_at:'2026-10-09T09:00:00+08:00',content:clone(payload.content)};historyRows.push(revision);Object.assign(currentDocument,{version_no:n,revision,latest_revision_id:revision.id,title:payload.content.title});if(saveTransportFailure)throw transportError();return clone(currentDocument)},
sources:async()=>({items:[{source_id:'visit:one',source_type:'visit',title:'合成拜访',text:'原始沟通材料',contributors:[],state_kind:'occurred',business_at:'2026-10-08T08:00:00+08:00'}],has_more:false}),snapshot:async()=>({selection:{supplements:[{id:'supplement',text:'本人补充的背景说明'}]}}),
options:async()=>({can_share:true,share_targets:{allowed_kinds:['members','team','company'],members:[{id:'reader',name:'授权接收者'}],teams:[{id:'team',name:'授权部门'}]}}),
share:async(id,payload)=>{calls.push({kind:'share',payload});Object.assign(currentDocument,{version_no:currentDocument.version_no+1,status:'shared',shared_revision_id:currentDocument.revision.id,audience:payload.audience});return clone(currentDocument)},
unshare:async(id,payload)=>{calls.push({kind:'unshare',payload});Object.assign(currentDocument,{version_no:currentDocument.version_no+1,status:'private',shared_revision_id:null,audience:null});return clone(currentDocument)},
revisions:async()=>({items:clone(historyRows),has_more:false}),revision:async(id,rid)=>clone(historyRows.find(r=>r.id===rid)),
export:async(id,rid)=>{calls.push({kind:'export',id,rid});if(exportFailure)throw Object.assign(Error('导出权限已变化'),{status:exportFailure});return{document_id:id,revision_id:rid,text:'人工编辑：再次核实的工作进展',filename:'合成周报.md',mime_type:'text/markdown; charset=utf-8'}},
generate:async payload=>{calls.push({kind:'generate',payload});return{id:'rewrite'}},run:id=>new Promise(resolve=>runReads.push({id,resolve})),cancel:async(id,payload)=>({id,status:'cancelled',version_no:2})};
Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copied=text}}});
const rootNode=createRoot(document.getElementById('root'));window.unmountDocument=()=>rootNode.unmount();rootNode.render(<PcProvider><App><main className="department-ui" style={{padding:20}}><ReportDocument page={{}} documentId="document" owner="owner" actorId="actor" canGenerate service={service} pollDelay={25}/></main></App></PcProvider>);
`},bundle:true,write:false,outdir:'.runtime/document-check',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
async function fixture(run,hash='') {
 const browser=await chromium.launch({channel:'chrome',headless:true}),context=await browser.newContext({viewport:{width:1280,height:1000}}),p=await context.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(10000);
 await context.route('**/*',r=>r.fulfill({contentType:'text/html',body:'<!doctype html><head><style>'+css+'</style></head><div id="page-root"><div id="root"></div></div><script>'+js+'</script>'}));
 try{await p.goto('https://report-document.test/'+hash);await p.getByLabel('报告阅读视图',{exact:true}).waitFor();await run(p);assert.deepEqual(errors,[]);}catch(error){await fs.mkdir(root+'.runtime/work-report-document-shots',{recursive:true});await fs.writeFile(root+'.runtime/work-report-document-shots/failure.txt',JSON.stringify({errors,body:await p.locator('body').innerText(),calls:await p.evaluate(()=>calls),buttons:await p.locator('button').evaluateAll(buttons=>buttons.filter(b=>b.textContent.includes('保存草稿')).map(b=>{const parents=[];for(let e=b;e;e=e.parentElement)parents.push({tag:e.tagName,class:e.className,hidden:e.getAttribute('aria-hidden'),inert:e.inert,role:e.getAttribute('role')});return{html:b.outerHTML,parents};}))},null,2));throw error;}finally{await context.close();await browser.close();}
}

test('document conflicts preserve edits, sharing is explicit, later edits retain the shared revision, and denied export clears content',()=>fixture(async p=>{
 await p.getByRole('button',{name:'查看来源',exact:true}).click();await p.getByText('本人补充的背景说明',{exact:true}).waitFor();await p.getByText('原始沟通材料',{exact:true}).waitFor();await p.getByRole('button',{name:'关闭',exact:true}).click();
 await p.getByRole('button',{name:'编辑报告',exact:true}).click();
 const body=p.getByLabel('报告正文：重点进展 1',{exact:true});await body.fill('人工核对后的工作进展');await p.evaluate(()=>saveFailure=409);
 await p.getByRole('button',{name:'保存草稿',exact:true}).click();await p.getByText('版本冲突，保留当前编辑',{exact:true}).waitFor();assert.equal(await body.inputValue(),'人工核对后的工作进展');
 await p.getByRole('button',{name:'重新读取报告',exact:true}).click();await p.getByRole('button',{name:'继续编辑',exact:true}).click();assert.equal(await body.inputValue(),'人工核对后的工作进展');
 await p.evaluate(()=>saveFailure=0);await p.getByRole('button',{name:'保存草稿',exact:true}).click();await p.getByRole('button',{name:'确认并共享',exact:true}).waitFor();await p.getByRole('button',{name:'确认并共享',exact:true}).click();
 await p.getByRole('combobox',{name:'共享给成员'}).click();await p.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({hasText:/^授权接收者$/}).click();await p.getByRole('dialog').getByText('确认并共享报告',{exact:true}).click();await p.getByRole('button',{name:'确认共享',exact:true}).click();
 await p.getByRole('button',{name:'管理共享',exact:true}).waitFor();const share=await p.evaluate(()=>calls.find(c=>c.kind==='share').payload);assert.deepEqual(share,{version_no:2,confirm:true,audience:{kind:'members',member_ids:['reader']}});
 await body.fill('再次核实的工作进展');await p.getByRole('button',{name:'保存草稿',exact:true}).click();await p.getByText('当前修改尚未共享',{exact:true}).waitFor();assert.equal(await p.evaluate(()=>calls.filter(c=>c.kind==='share').length),1);assert.equal(await p.evaluate(()=>currentDocument.shared_revision_id),'revision-2');
 const saved=await p.evaluate(()=>calls.filter(c=>c.kind==='save').at(-1).payload);assert.equal(saved.content.sections[0].items[0].kind,'manual');assert.deepEqual(saved.content.sections[0].items[0].source_refs,[]);
 await p.getByRole('button',{name:'历史版本',exact:true}).click();await p.getByRole('button',{name:/第 1 版/}).click();await p.locator('.wr-history-paper').getByText('已完成需求确认',{exact:false}).waitFor();assert.equal(await body.inputValue(),'再次核实的工作进展');await p.getByRole('button',{name:'关闭',exact:true}).click();
 await p.getByRole('button',{name:'复制与下载',exact:true}).click();await p.getByRole('button',{name:'复制正文',exact:true}).click();assert.equal(await p.evaluate(()=>copied),'人工编辑：再次核实的工作进展');assert.equal(await p.evaluate(()=>calls.filter(c=>c.kind==='export').at(-1).rid),'revision-4');
 const downloading=p.waitForEvent('download');await p.getByRole('button',{name:'下载 Markdown',exact:true}).click();const download=await downloading;assert.equal(download.suggestedFilename(),'合成周报.md');assert.equal(await fs.readFile(await download.path(),'utf8'),'人工编辑：再次核实的工作进展');await p.getByRole('button',{name:'关闭',exact:true}).last().click();
 await p.evaluate(()=>exportFailure=403);await p.getByRole('button',{name:'复制与下载',exact:true}).click();await p.getByText('导出权限已变化',{exact:true}).waitFor();assert.equal(await body.count(),0);assert.equal(await p.getByRole('button',{name:'复制正文',exact:true}).count(),0);
}));

test('shared readers receive no private source or mutation controls',()=>fixture(async p=>{
 for(const name of ['查看来源','保存草稿','重写本节','调整表达','确认并共享','管理共享','复制与下载'])assert.equal(await p.getByRole('button',{name,exact:true}).count(),0);
 assert.equal(await p.locator('textarea,input').count(),0);await p.getByText('已完成需求确认',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'编辑报告',exact:true}).count(),0);assert.deepEqual(await p.evaluate(()=>calls),[]);
},'#reader'));

test('rewrite freezes the original version and section ids, then rereads the persisted result',()=>fixture(async p=>{
 await p.getByRole('button',{name:'编辑报告',exact:true}).click();
 await p.getByRole('button',{name:'重写本节',exact:true}).click();await p.waitForFunction(()=>runReads.length===1);const payload=await p.evaluate(()=>calls.find(c=>c.kind==='generate').payload);
 assert.deepEqual(payload,{snapshot_id:'snapshot',snapshot_hash:'a'.repeat(64),scheme:{scheme_id:'scheme',version_no:4},document_id:'document',version_no:1,base_revision_id:'revision-1',section_ids:['progress']});assert.equal(await p.getByLabel('报告标题',{exact:true}).isDisabled(),true);
 await p.evaluate(()=>{currentDocument.revision.content.sections[0].items[0].text='已保存的重写内容';runReads[0].resolve({id:'rewrite',status:'succeeded',document_id:'document',result_status:'generated',version_no:2});});
 await p.getByText('已保存的重写内容',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'编辑报告',exact:true}).isEnabled(),true);
}));

test('reading, editing and preview preserve the draft without implicit writes',()=>fixture(async p=>{
 assert.equal(await p.locator('textarea,input').count(),0);await p.getByRole('button',{name:'编辑报告',exact:true}).click();
 await p.getByLabel('报告正文：重点进展 1',{exact:true}).fill('未保存的预览内容');await p.getByRole('button',{name:'预览报告',exact:true}).click();
 await p.getByText('未保存的预览内容',{exact:true}).waitFor();await p.getByText('未保存预览',{exact:true}).waitFor();assert.deepEqual(await p.evaluate(()=>calls),[]);
 assert.equal(await p.getByRole('button',{name:'确认并共享',exact:true}).isDisabled(),true);
 await p.getByRole('button',{name:'继续编辑',exact:true}).click();assert.equal(await p.getByLabel('报告正文：重点进展 1',{exact:true}).inputValue(),'未保存的预览内容');
}));

async function editAndSave(p,setup){
 await p.getByRole('button',{name:'编辑报告',exact:true}).click();
 await p.getByLabel('报告正文：重点进展 1',{exact:true}).fill('本次冻结的人工稿');
 await p.evaluate(setup);
 await p.getByRole('button',{name:'保存草稿',exact:true}).click();
}

test('committed save with lost response is confirmed by one GET without a second write',()=>fixture(async p=>{
 await editAndSave(p,()=>saveTransportFailure=1);
 await p.getByText('第 2 版 · 人工编辑',{exact:false}).waitFor();
 assert.equal(await p.getByText('保存结果待确认',{exact:true}).count(),0);
 assert.equal(await p.getByRole('button',{name:'保存草稿',exact:true}).isDisabled(),true);
 assert.equal(await p.getByLabel('报告正文：重点进展 1',{exact:true}).inputValue(),'本次冻结的人工稿');
 assert.deepEqual(await p.evaluate(()=>({writes:calls.filter(c=>c.kind==='save').length,reads:documentReads,version:currentDocument.version_no})),{writes:1,reads:2,version:2});
}));

test('mismatched readback preserves and locks local edit; explicit check only reads the eventual saved revision',()=>fixture(async p=>{
 await editAndSave(p,()=>{saveTransportFailure=1;commitBeforeFailure=false;});
 await p.getByText('保存结果待确认',{exact:true}).waitFor();
 await p.getByText('服务器当前版本与本次提交不一致。本地稿已保留，请稍后核对保存结果，暂不重复保存。',{exact:true}).waitFor();
 const body=p.getByLabel('报告正文：重点进展 1',{exact:true});
 assert.equal(await body.inputValue(),'本次冻结的人工稿');assert.equal(await body.isDisabled(),true);
 assert.equal(await p.getByRole('button',{name:'保存草稿',exact:true}).isDisabled(),true);
 await p.evaluate(()=>{const content=JSON.parse(JSON.stringify(calls[0].payload.content));Object.assign(currentDocument,{version_no:2,latest_revision_id:'r2',revision:{id:'r2',document_id:'document',revision_no:2,origin:'manual',content}});});
 await p.getByRole('button',{name:'核对保存结果',exact:true}).click();
 await p.getByText('第 2 版 · 人工编辑',{exact:false}).waitFor();
 assert.equal(await body.isEnabled(),true);assert.equal(await p.evaluate(()=>calls.filter(c=>c.kind==='save').length),1);
}));

test('500 and failing readback retain the local draft and allow read-only recovery',()=>fixture(async p=>{
 await editAndSave(p,()=>{saveTransportFailure=500;readFailure=503;});
 await p.getByText('暂时无法读取保存结果。本地稿已保留，请稍后再次核对。',{exact:true}).waitFor();
 assert.equal(await p.getByLabel('报告正文：重点进展 1',{exact:true}).inputValue(),'本次冻结的人工稿');
 await p.evaluate(()=>readFailure=0);
 await p.getByRole('button',{name:'核对保存结果',exact:true}).click();
 await p.getByText('第 2 版 · 人工编辑',{exact:false}).waitFor();
 assert.deepEqual(await p.evaluate(()=>({writes:calls.filter(c=>c.kind==='save').length,reads:documentReads})),{writes:1,reads:3});
}));

test('late save readback cannot apply after identity changes',()=>fixture(async p=>{
 await editAndSave(p,()=>{saveTransportFailure=1;holdReads=true;});
 await p.waitForFunction(()=>readFlights.length===1);
 await p.evaluate(async()=>{actorIdentity='other';readFlights[0](JSON.parse(JSON.stringify(currentDocument)));await new Promise(resolve=>setTimeout(resolve,0));});
 assert.equal(await p.getByText('第 2 版 · 人工编辑',{exact:false}).count(),0);
 assert.equal(await p.getByText('第 1 版 · 生成草稿',{exact:false}).count(),1,'late response did not update component state');
 assert.equal(await p.evaluate(()=>calls.filter(c=>c.kind==='save').length),1);
}));

test('late save readback is ignored after unmount',()=>fixture(async p=>{
 await editAndSave(p,()=>{saveTransportFailure=1;holdReads=true;});
 await p.waitForFunction(()=>readFlights.length===1);
 await p.evaluate(()=>unmountDocument());
 await p.evaluate(async()=>{readFlights[0](JSON.parse(JSON.stringify(currentDocument)));await new Promise(resolve=>setTimeout(resolve,0));});
 assert.equal(await p.getByLabel('报告正文：重点进展 1',{exact:true}).count(),0);
 assert.equal(await p.evaluate(()=>calls.filter(c=>c.kind==='save').length),1);
}));
