// Isolated component fixture: original report API, polling hook and transport
// receipts, synthetic server; all real browser HTTP is intercepted and blocked.
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || '/Users/zhouwei5/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const root=fileURLToPath(new URL('../',import.meta.url));
const receiptSource=readFileSync(new URL('../source/miniprogram/utils/requestIdentity.js',import.meta.url),'utf8');
const bundle=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';
import ReviewSummaryPanel from './department-ui/review/report/ReviewSummaryPanel.jsx';
import ReviewReportContent from './department-ui/review/report/ReviewReportContent.jsx';
import {ids,selection as initialSelection,options as originalOptions,sample,clone} from './tests/review-report-fixtures.mjs';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';
window.calls=[];window.actor={workspaceId:'company',userId:ids.actor,permissionVersion:1};
window.options={...clone(originalOptions),...(fixture.options || {})};window.reads={snapshot:0,run:0};
window.mode=fixture.mode || 'daily';window.selection={...initialSelection,period_type:mode==='weekly'?'week':'day',...(fixture.selection || {})};window.response=sample(mode);
if(fixture.scopeV2){const other='00000000-0000-4000-8000-000000000010',revision='c'.repeat(64),kind=fixture.scopeV2Kind || 'team',subjects=kind==='team'?[ids.team,other]:[ids.actor,other],scope={contract_version:'review.scope.v2',kind,label:kind==='team'?'两个管理团队':'两位成员',subject_ids:subjects,subjects:subjects.map((id,index)=>({scope_type:kind==='team'?'team':'person',id,label:kind==='team'?(index?'另一个管理团队':'实际管理团队'):(index?'成员乙':'成员甲')})),resolved_member_ids:kind==='team'?[ids.actor]:subjects,scope_key:'review-scope:v2:'+revision,scope_revision:revision,membership_basis:'current_authorized_at_data_as_of'};window.options={...window.options,available_scope_kinds:['self',kind],...(kind==='team'?{teams:[{id:ids.team,name:'实际管理团队'},{id:other,name:'另一个管理团队'}]}:{members:[{id:ids.actor,name:'成员甲'},{id:other,name:'成员乙'}]})};window.selection={scope:kind,period_type:mode==='weekly'?'week':'day',anchor_date:'2026-01-14',...(kind==='team'?{team_ids:subjects}:{member_ids:subjects}),expected_scope_revision:revision};window.response.snapshot={...response.snapshot,selection:{...response.snapshot.selection,scope:{contract_version:'work_report.scope_selection.v2',kind,...(kind==='team'?{team_ids:subjects}:{member_ids:subjects}),expected_scope_revision:revision}},scope,review_facts:{...response.snapshot.review_facts,contract_version:'work_report.review_facts.v2',scope}};window.response.document={...response.document,scope,review_facts:window.response.snapshot.review_facts};}
window.wx={getStorageSync:key=>JSON.parse(sessionStorage.getItem(key) || 'null'),setStorageSync:(key,value)=>sessionStorage.setItem(key,JSON.stringify(value))};
const receiptModule={exports:{}};new Function('module',${JSON.stringify(receiptSource)})(receiptModule);const receipt=receiptModule.exports;
let uncertain=Boolean(fixture.uncertain),currentSelection=response.snapshot.selection;
const respond=async options=>{
 const path=options.path;
 if(fixture.deny && path.includes('/documents/'))throw Object.assign(Error('当前无权读取此报告'),{statusCode:403});
 if(path==='/work-reports/options')return clone(window.options);
 if(options.method==='POST' && path==='/work-reports/snapshots'){
  currentSelection=clone(options.data);reads={snapshot:0,run:0};const value={...clone(response.snapshot),selection:currentSelection,status:'preparing',snapshot_hash:null,review_facts:null};
  if(uncertain){uncertain=false;throw Error('提交回执丢失');}
  if(fixture.holdPrepare)return new Promise(resolve=>{window.releasePrepare=()=>resolve(value);});
  return value;
 }
 if(fixture.scopeChanged && path.includes('/snapshots/'))throw Object.assign(Error('所选范围已变化，请重新取材'),{statusCode:409,code:'WORK_REPORT_SCOPE_CHANGED'});
 if(path.includes('/snapshots/')){const pending=fixture.holdSnapshot || reads.snapshot++===0;return {...clone(response.snapshot),selection:currentSelection,status:pending?'preparing':'ready',snapshot_hash:pending?null:response.snapshot.snapshot_hash,review_facts:pending?null:response.snapshot.review_facts};}
 if(options.method==='POST' && path==='/work-reports/runs')return {...clone(response.run),status:'queued',document_id:null,result_status:null};
 if(path.includes('/runs/')){
  const n=reads.run++,pending=fixture.holdRun || n<2;
  if(pending)return {...clone(response.run),status:n===0?'queued':'running',document_id:null,result_status:null};
  const kind=fixture.result || 'generated';return {...clone(response.run),status:kind==='failed'?'failed':kind==='cancelled'?'cancelled':'succeeded',result_status:kind==='failed'||kind==='cancelled'?null:kind,document_id:['failed','cancelled','insufficient_data'].includes(kind)?null:ids.document,error:kind==='failed'?{detail:'生成服务暂时不可用'}:null};
 }
 if(path.includes('/documents/')){
  const doc=clone(response.document);if(fixture.result==='basic_summary'){doc.revision.origin='basic_summary';delete doc.revision.content.review_content;}
  if(fixture.holdDocument)return new Promise(resolve=>{window.releaseDocument=()=>resolve(doc);});return doc;
 }
 throw Error('Unexpected path '+path);
};
const client={request:async options=>{
 const token=options.method==='GET'?null:receipt.begin(JSON.stringify(actor),options);
 calls.push({...clone(options),idempotencyKey:token?.key || null});
 const value=await respond(options);if(token)receipt.finish(token);return value;
}};
window.SalesRuntime={app:{globalData:{get session(){return actor;}}},require:name=>name==='utils/access'?{identity:who=>JSON.stringify(who)}:name==='utils/requestIdentity'?receipt:client};
function App(){const[visible,setVisible]=useState(true),[tick,setTick]=useState(0),[slice,setSlice]=useState(null);window.hide=()=>setVisible(false);window.show=()=>setVisible(true);window.rerender=()=>setTick(tick+1);window.renderSlice=value=>setSlice(value);
 if(slice){const doc=clone(response.document),content=doc.revision.content.review_content;if(mode==='daily')content.items=Array.from({length:3},(_,i)=>{const row=clone(content.items[0]);row.fact.text='第 '+(i+1)+' 条 '+row.fact.text;return row;});if(slice.invalid){if(mode==='daily')content.items[0].fact.source_refs=['review:missing'];else content.groups[0].progress[0].source_refs=['review:missing'];}return <ReviewReportContent content={content} facts={doc.review_facts} itemIndex={slice.itemIndex} groupIndex={slice.groupIndex}/>;}
 return visible?<ReviewSummaryPanel selection={selection} mode={mode} pollDelay={30} onOpenDocument={id=>{window.openedDocument=id;}}/>:null;}
createRoot(document.getElementById('root')).render(<PcProvider><div style={{width:420,margin:24}}><App/></div></PcProvider>);
`},bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/review-report/browser',define:{'process.env.NODE_ENV':'"production"'}});
const js=bundle.outputFiles.find(row=>row.path.endsWith('.js')).text.replace(/<\/script/gi,'<\\/script'),css=bundle.outputFiles.find(row=>row.path.endsWith('.css')).text;
let browser;
before(async()=>{browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();});
async function fixture(run,config={}){
 const context=await browser.newContext({viewport:{width:1050,height:1100}}),page=await context.newPage(),errors=[],unexpected=[];
 await page.addInitScript(value=>{window.fixture=value;},config);page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',route=>{if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><style>'+css+'</style><div id="root"></div><script>'+js+'</script>'});unexpected.push(route.request().url());return route.abort();});
 try{await page.goto('https://review-report.test/');await page.getByTestId('review-summary-panel').waitFor();await run(page);assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);}catch(error){console.error(JSON.stringify(await page.evaluate(()=>({text:document.body.innerText,buttons:[...document.querySelectorAll('button')].map(el=>el.outerHTML),calls:calls.slice(0,12),storage:Object.fromEntries(Object.entries(sessionStorage).filter(([key])=>key.startsWith('sales-web:review-report')))}))));throw error;}finally{await context.close();}
}
async function generate(page,label='生成AI 日总结'){await page.getByRole('button',{name:label,exact:true}).click();}
async function finished(page){await page.getByText('已保存为私人草稿，尚未共享。重入时会重新校验权限并读取服务器版本。',{exact:true}).waitFor();}
const pause=page=>page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,110)));

test('explicit daily create flows through one snapshot/run and renders typed private draft with evidence',()=>fixture(async page=>{
 await page.getByRole('button',{name:'生成AI 日总结',exact:true}).waitFor();assert.equal(await page.evaluate(()=>calls.filter(row=>row.method!=='GET').length),0);
 await generate(page);await finished(page);const calls=await page.evaluate(()=>window.calls),posts=calls.filter(row=>row.method==='POST');assert.equal(posts.length,2);
 assert.equal(posts[0].data.review_mode,'daily');assert.deepEqual(posts[0].data.sources,[]);assert.equal(posts[1].data.snapshot_hash,'a'.repeat(64));assert.equal(posts[1].data.review_facts,undefined);
 await page.getByText('冻结预计回款 9007199254740993.12345678 元。',{exact:true}).waitFor();await page.getByText('日变化：无可信历史基线。',{exact:true}).waitFor();
 await page.getByRole('button',{name:'查看依据',exact:true}).first().click();await page.getByText('已冻结统计 <script>window.pwned=true</script>',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>window.pwned),undefined);await page.locator('.ant-drawer-close').click();
 await page.getByRole('button',{name:'打开报告草稿',exact:true}).click();assert.equal(await page.evaluate(()=>openedDocument),'00000000-0000-4000-8000-000000000005');
 const before=await page.evaluate(()=>calls.length);await page.evaluate(()=>hide());await page.getByTestId('review-summary-panel').waitFor({state:'hidden'});await pause(page);assert.equal(await page.evaluate(()=>calls.length),before);
 await page.evaluate(()=>show());await finished(page);assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),2);
 await page.evaluate(()=>renderSlice({itemIndex:1}));assert.equal(await page.locator('.rr-daily-items>li').count(),1);await page.getByText('02',{exact:true}).waitFor();await page.getByText('第 2 条 冻结预计回款 9007199254740993.12345678 元。',{exact:true}).waitFor();
 await page.evaluate(()=>renderSlice({itemIndex:1,invalid:true}));await page.getByText('正文引用不属于此报告快照',{exact:true}).waitFor();assert.equal(await page.locator('.rr-daily-items>li').count(),0);
}));
test('v2 multi-team report carries every selected team through prepare, generate and saved-document reread',()=>fixture(async page=>{
 await page.getByRole('button',{name:'生成AI 日总结',exact:true}).click();await finished(page);
 const posts=await page.evaluate(()=>calls.filter(row=>row.method==='POST'));
 assert.equal(posts.length,2);assert.deepEqual(posts[0].data.scope.team_ids,['00000000-0000-4000-8000-000000000002','00000000-0000-4000-8000-000000000010']);assert.equal(posts[0].data.scope.expected_scope_revision,'c'.repeat(64));assert.equal(posts[0].data.scope.member_ids,undefined);assert.equal(posts[1].data.snapshot_hash,'a'.repeat(64));
 await page.getByText('冻结预计回款 9007199254740993.12345678 元。',{exact:true}).waitFor();assert.ok(await page.getByText(/两个管理团队/).count()>0);
 await page.evaluate(()=>hide());await page.getByTestId('review-summary-panel').waitFor({state:'hidden'});await page.evaluate(()=>show());await page.getByText('冻结预计回款 9007199254740993.12345678 元。',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),2);
},{scopeV2:true}));
test('v2 multi-member report preserves every selected member',()=>fixture(async page=>{
 await page.getByRole('button',{name:'生成AI 日总结',exact:true}).click();await finished(page);
 const posts=await page.evaluate(()=>calls.filter(row=>row.method==='POST'));assert.equal(posts.length,2);assert.deepEqual(posts[0].data.scope.member_ids,['00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000010']);assert.equal(posts[0].data.scope.team_ids,undefined);
 await page.getByText('冻结预计回款 9007199254740993.12345678 元。',{exact:true}).waitFor();assert.ok(await page.getByText(/两位成员/).count()>0);
 },{scopeV2:true,scopeV2Kind:'member'}));
test('scope revision conflict clears resumable generation and blocks another POST',()=>fixture(async page=>{
 await page.getByRole('button',{name:'生成AI 日总结',exact:true}).click();await page.getByText('所选范围已变化，请重新取材',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),1);assert.equal(await page.getByRole('button',{name:'生成AI 日总结',exact:true}).isDisabled(),true);
 },{scopeChanged:true}));
test('unknown submission retry retains payload and real idempotency key',()=>fixture(async page=>{
 await generate(page);await page.getByText('上次提交结果待确认',{exact:true}).waitFor();await page.getByRole('button',{name:'重试本次提交',exact:true}).click();await finished(page);
 const posts=await page.evaluate(()=>calls.filter(row=>row.method==='POST'));assert.equal(posts.length,3);assert.equal(posts[0].idempotencyKey,posts[1].idempotencyKey);assert.deepEqual(posts[0].data,posts[1].data);
},{uncertain:true}));
test('leaving during prepare stores late receipt; reentry adopts it and never auto-generates',()=>fixture(async page=>{
 await generate(page);await page.waitForFunction(()=>typeof releasePrepare==='function');await page.evaluate(()=>hide());await page.getByTestId('review-summary-panel').waitFor({state:'hidden'});
 // Remount before the original request finishes; its receipt must still be
 // adopted before retry, even after the original transport key is settled.
 await page.evaluate(()=>show());await page.getByRole('button',{name:'重试本次提交',exact:true}).waitFor();await page.evaluate(()=>releasePrepare());
 await page.getByRole('button',{name:'重试本次提交',exact:true}).click();await page.getByRole('button',{name:'继续生成',exact:true}).waitFor();await pause(page);
 assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),1);
 await page.getByRole('button',{name:'继续生成',exact:true}).click();await finished(page);assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),2);
},{holdPrepare:true}));
test('running operation polls without a hard deadline, prevents duplicate writes and resumes with GET',()=>fixture(async page=>{
 await generate(page);await page.waitForFunction(()=>reads.run>3);assert.equal(await page.locator('.rr-panel-actions button').first().isDisabled(),true);assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),2);
 await page.waitForFunction(()=>reads.run>20);await page.getByText('报告仍在处理中',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),2);
 await page.evaluate(()=>hide());await page.getByTestId('review-summary-panel').waitFor({state:'hidden'});const count=await page.evaluate(()=>calls.length);await pause(page);assert.equal(await page.evaluate(()=>calls.length),count);
 await page.evaluate(()=>{fixture.holdRun=false;show();});await finished(page);assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),2);
},{holdRun:true}));
test('weekly typed content keeps suggestion and contribution semantics, selecting ordinary weekly scheme',()=>fixture(async page=>{
 await generate(page,'生成增强周报');await finished(page);await page.getByText('贡献只作申报，不自动确收。',{exact:true}).waitFor();await page.getByText('贡献声明',{exact:true}).waitFor();await page.getByText('下周任务建议',{exact:true}).waitFor();
 const posts=await page.evaluate(()=>calls.filter(row=>row.method==='POST'));assert.equal(posts[0].data.review_mode,'weekly');assert.deepEqual(posts[0].data.sources,['visit']);assert.equal(posts[1].data.scheme.scheme_id,'00000000-0000-4000-8000-000000000008');
 await page.getByRole('button',{name:'查看依据',exact:true}).nth(2).click();await page.getByText('待审目标 · 不作为正式目标',{exact:true}).waitFor();assert.equal(posts.some(row=>row.path.includes('/tasks')),false);
 await page.evaluate(()=>renderSlice({groupIndex:0}));assert.equal(await page.locator('.rr-card').count(),1);await page.getByText('贡献只作申报，不自动确收。',{exact:true}).waitFor();
 await page.evaluate(()=>renderSlice({groupIndex:1}));await page.getByText('所选报告条目不存在，请重新打开',{exact:true}).waitFor();
},{mode:'weekly'}));
test('broader readable teams cannot generate, and disabled provider causes no submission',async()=>{
 await fixture(async page=>{await page.getByText('只能为实际管理的团队生成报告',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'生成AI 日总结',exact:true}).isDisabled(),true);assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),0);},{selection:{scope:'team',team_id:'visible-but-not-managed'}});
 await fixture(async page=>{await page.getByText('AI 报告生成暂不可用',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'生成AI 日总结',exact:true}).isDisabled(),true);},{options:{generation_available:false}});
});
test('failed and insufficient runs stop with no document read; basic summary is never called AI success',async()=>{
 for(const kind of ['failed','insufficient_data','basic_summary'])await fixture(async page=>{
  await generate(page);const title=kind==='failed'?'生成服务暂时不可用':kind==='insufficient_data'?'材料不足，未生成正文':'仅生成基础汇总，未完成 AI Review';await page.getByText(title,{exact:true}).waitFor();
  if(kind!=='basic_summary')assert.equal(await page.evaluate(()=>calls.some(row=>row.path.includes('/documents/'))),false);else assert.equal(await page.getByText('冻结预计回款 9007199254740993.12345678 元。',{exact:true}).count(),0);
  const count=await page.evaluate(()=>calls.length);await pause(page);assert.equal(await page.evaluate(()=>calls.length),count);
 },{result:kind});
});
test('reentry permission loss removes content; account change discards late authorized response',async()=>{
 await fixture(async page=>{await generate(page);await finished(page);await page.evaluate(()=>hide());await page.getByTestId('review-summary-panel').waitFor({state:'hidden'});await page.evaluate(()=>{fixture.deny=true;show();});await page.getByText('当前无权读取此报告',{exact:true}).first().waitFor();assert.equal(await page.getByText('冻结预计回款 9007199254740993.12345678 元。',{exact:true}).count(),0);assert.equal(await page.evaluate(()=>Object.keys(sessionStorage).some(key=>key.startsWith('sales-web:review-report:v1:'))),false);});
 await fixture(async page=>{await generate(page);await page.waitForFunction(()=>typeof releaseDocument==='function');await page.evaluate(()=>{actor={...actor,userId:'other'};rerender();});await page.evaluate(()=>releaseDocument());await pause(page);assert.equal(await page.getByText('冻结预计回款 9007199254740993.12345678 元。',{exact:true}).count(),0);assert.equal(await page.evaluate(()=>calls.filter(row=>row.method==='POST').length),2);},{holdDocument:true});
});
