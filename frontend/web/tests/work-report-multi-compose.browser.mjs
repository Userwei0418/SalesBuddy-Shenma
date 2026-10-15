import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || '/Users/zhouwei5/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const root=fileURLToPath(new URL('../',import.meta.url));
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';import {PcProvider} from './department-ui/pc/PcProvider.jsx';
import ReportWorkspace from './department-ui/work-report/ReportWorkspace.jsx';import {REVIEW_METRICS,REVIEW_ACTIVITIES} from './department-ui/review/overview-model.js';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';
const id=n=>'20000000-0000-4000-8000-'+String(n).padStart(12,'0');window.calls=[];window.legacy=[];window.saved=null;window.invalidate=false;
const revision='a'.repeat(64),options={period_kinds:['day','week','two_weeks','month','quarter'],available_scope_kinds:['self','team','member'],teams:[{id:id(1),name:'南区'},{id:id(2),name:'北区'}],members:[{id:id(3),name:'成员甲'},{id:id(4),name:'成员乙'}],contract_version:'work_report.overview_options.v1',generation:{profiles:['general'],available_scope_kinds:['self','team','member']}};
function envelope(q){const ids=(q.getAll('team_ids').length?q.getAll('team_ids'):q.getAll('member_ids')).sort(),kind=q.get('scope'),subjects=ids.map(id=>({scope_type:kind==='team'?'team':'person',id,label:(kind==='team'?options.teams:options.members).find(row=>row.id===id).name}));return {selection:{scope:kind,team_ids:kind==='team'?ids:[],member_ids:kind==='member'?ids:[],anchor_date:q.get('anchor_date'),period_type:q.get('period_type')},scope:{contract_version:'review.scope.v2',kind,label:subjects.map(s=>s.label).join('、'),subject_ids:ids,subjects,resolved_member_ids:kind==='member'?ids:[id(3),id(4)],scope_key:'review-scope:v2:'+'b'.repeat(64),scope_revision:revision,membership_basis:'current_authorized_at_data_as_of'},period:{label:'2026年第四季度'},data_as_of:'2026-10-14T12:00:00Z'};}
const client={request:async r=>{calls.push(r);const u=new URL(r.path,'https://multi.test'),q=u.searchParams,e=envelope(q);if(invalidate&&u.pathname!=='/review/overview')throw Object.assign(Error('scope changed'),{statusCode:409,serverCode:'WORK_REPORT_SCOPE_CHANGED'});if(u.pathname==='/review/overview')return {...e,contract_version:'review.overview.v2',quarter:{quarter:4,time_progress:'0.15'},members:options.members,metrics:REVIEW_METRICS.map(({code})=>({metric_code:code,value:'100000.00',known_subtotal:'100000.00',coverage:'complete',status:'no_target',rate:null,gap:null,day_change:null,baseline:{status:'no_baseline'},target:{kind:code,id:null,version_no:null,value:null,status:'no_target',known_subtotal:'0.00',coverage:'unavailable',aggregation:'sum_distinct_subject_targets',components:e.scope.subject_ids.map(subject_id=>({scope_type:e.scope.kind==='team'?'team':'person',subject_id,target_id:null,version_no:null,status:'no_target',value:null}))}})),activities:REVIEW_ACTIVITIES.map(({code})=>({code,count:0,known_count:0,coverage:'complete'})),pool_counts:{all:0},filter_availability:{},generation:{available:false,reason_code:'review_multi_scope_generation_not_ready'}};return {...e,contract_version:'review.page.v2',activity:q.get('activity'),filter:q.get('filter'),sort:q.get('sort'),metric_code:q.get('metric_code'),items:[],total:0,known_total:0,coverage:'complete',has_more:false,limit:Number(q.get('limit')),offset:Number(q.get('offset'))};}};
window.page={route:'pages/work-report/index',options:Object.fromEntries(new URLSearchParams(location.search))};
window.SalesRuntime={app:{globalData:{session:{workspaceId:'test',userId:'lead',permissionVersion:1}}},replacePageQuery:(page,patch)=>{const q=new URLSearchParams(location.search);for(const [k,v]of Object.entries(patch)){v==null?q.delete(k):q.set(k,v)}page.options=Object.fromEntries(q);history.replaceState({},'',location.pathname+'?'+q)},require:name=>name==='utils/apiClient'?client:name==='utils/access'?{identity:()=> 'owner'}:{}};

const config={sections:[{id:'progress',title:'工作进展',enabled:true,instructions:''}],group_by:'topic',style:{audience:'直属负责人',tone:'concise',length:'medium',voice:'first_person',language:'zh-CN',focus:[]},reference_texts:[],default_period:'month',default_sources:['visit']};
const reportOptions={can_create:true,generation_available:true,default_scheme:config,periods:options.period_kinds,available_scope_kinds:options.available_scope_kinds,teams:options.teams,members:options.members,sources:[{id:'visit',label:'跟进记录',available:true}],builtin_schemes:options.period_kinds.map((period,i)=>({scheme_id:id(i+10),version_no:1,name:period+'默认报告',config:{...config,default_period:period}}))};
window.prepared=[];window.generated=[];window.runReads=[];
const service={options:async()=>reportOptions,overviewOptions:async()=>options,overview:async q=>{legacy.push(q);throw Error('不可调用旧范围')},schemes:async()=>({items:[],has_more:false}),prepare:async selection=>{prepared.push(selection);return {id:id(20)}},snapshot:async()=>({id:id(20),status:'ready',selection:prepared.at(-1),period:{label:'所选周期'},scope:{label:'所选两个主体'},data_as_of:'2026-10-14T12:00:00Z',snapshot_hash:'a'.repeat(64),coverage:[{source_type:'visit',selected:true,status:'complete',included:2,total:2,missing_reasons:[]}],metrics:[]}),generate:async payload=>{generated.push(payload);return {id:id(21)}},run:async runId=>{runReads.push(runId);return {id:runId,status:'running',version_no:1}}};
createRoot(document.getElementById('root')).render(<PcProvider><ReportWorkspace page={page} service={service}/></PcProvider>);
`},bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/review-multi-scope/browser',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).text,css=built.outputFiles.find(f=>f.path.endsWith('.css')).text;
let browser;before(async()=>{browser=await chromium.launch({channel:'chrome',headless:true});});after(async()=>{await browser?.close();});
const id=n=>'20000000-0000-4000-8000-'+String(n).padStart(12,'0');
async function fixture(kind,period,run){
 const context=await browser.newContext({viewport:{width:1600,height:1100}}),page=await context.newPage(),errors=[];
 page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><style>'+css+'</style><main class="department-ui"><div id="root"></div></main><script>'+js+'</script>'}));
 const ids=kind==='team'?[id(1),id(2)]:[id(3),id(4)];
 try{await page.goto('https://multi-compose.test/?'+new URLSearchParams({period,review_scope:kind,review_subjects:ids.join(','),review_anchor:'2026-10-14',review_profile:'general'}));await page.getByTestId('review-overview').waitFor();await run(page,ids);assert.deepEqual(errors,[]);}finally{await context.close();}
}
for(const [kind,period,entry]of [['team','month','button'],['member','biweek','tab'],['team','day','button'],['member','week','tab'],['team','quarter','button']])test(kind+' '+period+' overview to compose and refresh preserves all IDs and revision',()=>fixture(kind,period,async(page,ids)=>{
 const buttons=page.getByRole('button',{name:'生成报告',exact:true});
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).filter(b=>b.textContent==='生成报告').every(b=>!b.disabled));
 await (entry==='tab'?buttons.first():buttons.last()).click();
 await page.getByRole('button',{name:'准备材料',exact:true}).waitFor();
 await page.getByRole('button',{name:'准备材料',exact:true}).click();
 await page.waitForFunction(()=>prepared.length===1);
 let selection=await page.evaluate(()=>prepared[0]);
 assert.equal(selection.period.kind,period==='biweek'?'two_weeks':period);
 assert.deepEqual(selection.scope[kind+'_ids'],ids);assert.equal(selection.scope.contract_version,'work_report.scope_selection.v2');assert.equal(selection.scope.expected_scope_revision,'a'.repeat(64));assert.equal(selection.review_mode,undefined);assert.equal(selection.overview_binding,undefined);
 assert.equal(await page.evaluate(()=>legacy.length),0);
 await page.reload();await page.getByTestId('review-overview').waitFor();
 await page.getByRole('button',{name:'生成报告',exact:true}).last().click();
 await page.getByRole('button',{name:'准备材料',exact:true}).click();await page.waitForFunction(()=>prepared.length===1);
 selection=await page.evaluate(()=>prepared[0]);assert.deepEqual(selection.scope[kind+'_ids'],ids);assert.equal(selection.scope.expected_scope_revision,'a'.repeat(64));
 assert.equal(selection.period.kind,period==='biweek'?'two_weeks':period);
 await page.getByRole('button',{name:'生成报告草稿',exact:true}).click();await page.waitForFunction(()=>generated.length===1 && runReads.length>0);
 assert.equal(await page.evaluate(()=>generated[0].snapshot_id),id(20));
 assert.equal(new URL(page.url()).searchParams.get('run_id'),id(21));
 await page.reload();await page.waitForFunction(()=>runReads.length>0);
 assert.deepEqual(await page.evaluate(()=>prepared),[]);assert.deepEqual(await page.evaluate(()=>generated),[]);
 assert.equal(await page.evaluate(()=>runReads[0]),id(21));
 assert.deepEqual(new URL(page.url()).searchParams.get('review_subjects').split(','),ids);
}));
