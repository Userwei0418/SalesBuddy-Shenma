// Actual presentation shell and Review transport; all HTTP is intercepted.
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || '/Users/zhouwei5/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const root=fileURLToPath(new URL('../',import.meta.url));
const bundle=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';
import ReviewMeetingDeck from './department-ui/review/ReviewMeetingDeck.jsx';
import {REVIEW_METRICS,REVIEW_ACTIVITIES} from './department-ui/review/overview-model.js';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';
window.calls=[];window.actor={workspaceId:'company',userId:'member',permissionVersion:1};
window.selection={scope:'self',anchor_date:'2026-10-14',period_type:'day'};
window.base={selection,scope:{kind:'self',label:'本人'},period:{label:'2026-10-14'},data_as_of:'2026-10-14T01:00:00Z'};
window.value={...base,contract_version:'review.overview.v1',quarter:{quarter:4,time_progress:'0.15'},members:[{id:'member',name:'成员甲'}],metrics:REVIEW_METRICS.map(({code},i)=>({metric_code:code,value:i===0?'0.00':null,known_subtotal:i===1?'300000.00':null,coverage:i===0?'complete':i===1?'partial':'unavailable',status:'no_target',target:{status:'no_target',value:null},rate:null,gap:null,baseline:{status:'no_baseline'}})),activities:REVIEW_ACTIVITIES.map(({code},i)=>({code,count:i===1?null:0,known_count:i===1?2:0,coverage:i===1?'partial':'complete'}))};
window.records={
new_claimed_customers:Array.from({length:window.largeClaims?107:7},(_,i)=>({id:'customer-'+i,customer_name:'认领客户 '+(i+1),owner_id:'member',occurred_at:'2026-10-14T00:00:00Z',source_code:'自主拓展'})),
formal_visits:[{id:'visit-1',customer_name:'跟进客户甲',interaction_at:'2026-10-14T02:00:00Z',recorded_at:'2026-10-14T04:00:00Z',summary:'确认实施边界',next_action:'周五提交方案',interaction_mode_code:'phone_voice'},{id:'visit-2',customer_name:'跟进客户乙',summary:'验证性能指标'}],
new_opportunities:[],lost_opportunities:[{id:'lost-1',name:'丢单商机甲',lost_reason:'预算取消',amount:'500000.00'}],
updated_opportunities:[{id:'updated-1',name:'商机金额调整',event_count:1,events:[{id:'event-1',occurred_at:'2026-10-14T02:00:00Z',changes:{before:{collection_amount:'1000000.00'},after:{collection_amount:'700000.00'}}}]}],
completed_tasks:[{id:'task-1',name:'已完成任务甲',completion_note:'完成验证',status_label:'已完成'}],pending_tasks:[],
all:[{id:'opp-1',name:'当前商机甲',owner_name:'成员甲',collection_amount:'1000000.00',recognized_amount:'0.00'}],key_collection:[]};
window.failKey='';window.holdKey='';
const client={request:async options=>{
 calls.push(options);if(options.method!=='GET')throw Error('Mutation forbidden');
 const u=new URL(options.path,'https://review-meeting.test'),query=Object.fromEntries(u.searchParams),key=query.activity || query.filter;
 if(window.failKey===key){window.failKey='';throw Error('本次读取中断');}
 const items=records[key] || [],unavailable=key==='pending_tasks'||key==='key_collection',partial=key==='formal_visits';
 const response={...query,...base,selection:{scope:query.scope,anchor_date:query.anchor_date,period_type:query.period_type},contract_version:'review.page.v1',limit:Number(query.limit),offset:Number(query.offset),items:items.slice(0,Number(query.limit)),coverage:unavailable?'unavailable':partial?'partial':'complete',available:!unavailable,total:unavailable||partial?null:items.length,known_total:items.length,has_more:items.length>Number(query.limit),missing_reasons:partial?['source_scope_not_fully_authorized']:unavailable?['当前筛选暂不可用']:[]};
 if(window.holdKey===key)return new Promise(resolve=>{window.releaseRequest=()=>resolve(response);});return response;
}};
window.SalesRuntime={app:{globalData:{get session(){return actor;}}},require:name=>name==='utils/access'?{identity:s=>JSON.stringify(s)}:client};
function App(){const[open,setOpen]=useState(true),[current,setCurrent]=useState(value),[tick,setTick]=useState(0);window.closeMeeting=()=>setOpen(false);window.enterMeeting=()=>setOpen(true);window.refreshProps=()=>{value={...value,period:{label:'新周期，不应覆盖正在开的会议'}};setCurrent(value);};window.rerender=()=>setTick(tick+1);return <ReviewMeetingDeck open={open} onClose={()=>setOpen(false)} value={current} selection={selection}/>;}
createRoot(document.getElementById('root')).render(<PcProvider><App/></PcProvider>);
`},bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/review-meeting/browser',define:{'process.env.NODE_ENV':'"production"'}});
const js=bundle.outputFiles.find(file=>file.path.endsWith('.js')).text,css=bundle.outputFiles.find(file=>file.path.endsWith('.css')).text;
let browser;
before(async()=>{browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();});
async function fixture(run,largeClaims=false){
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[],unexpected=[];
 await page.addInitScript(flag=>{window.largeClaims=flag;},largeClaims);page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',route=>{if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><style>'+css+'</style><div id="root"></div><script>'+js+'</script>'});unexpected.push(route.request().url());return route.abort();});
 try{await page.goto('https://review-meeting.test/');await page.getByRole('heading',{name:'季度目标与当前预测',exact:true}).waitFor();await run(page);assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);assert.equal(await page.evaluate(()=>calls.every(call=>call.method==='GET')),true);}finally{await context.close();}
}
async function next(page){await page.getByRole('button',{name:'下一页',exact:true}).click();}
async function go(page,title){
 for(let i=0;i<36;i++){
  if((await page.locator('.wr-presentation-header h2').innerText()).startsWith(title)){await page.locator('.rv-meeting-loading').waitFor({state:'hidden'});return;}
  await next(page);await page.locator('.rv-meeting-loading').waitFor({state:'hidden'});
 }
 throw Error('Chapter not reached: '+title);
}
test('opening keeps two primary metrics and seven action summaries without detail requests',()=>fixture(async page=>{
 assert.equal(await page.evaluate(()=>calls.length),0);
 const cards=page.locator('.rv-meeting-card');assert.equal(await cards.count(),2);assert.equal(await cards.nth(0).locator('strong').innerText(),'0.00万元');assert.equal(await cards.nth(1).locator('strong').innerText(),'30.00万元');
 await next(page);assert.equal(await page.locator('.rv-meeting-card').count(),7);await page.getByText('已知 2 条，完整数量暂不可确认',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>calls.length),0);
}));
test('detail is split into five-row pages, cached on revisit and exposes true changes',()=>fixture(async page=>{
 await go(page,'新认领客户');assert.equal(await page.getByTestId('review-meeting-record').count(),5);await page.getByText('已展示 1–5 / 本次读取 7 条 · 总量 7 条',{exact:true}).waitFor();
 await next(page);assert.equal(await page.getByTestId('review-meeting-record').count(),2);await page.getByRole('button',{name:'上一页',exact:true}).click();assert.equal(await page.evaluate(()=>calls.filter(row=>row.path.includes('activity=new_claimed_customers')).length),1);
 await go(page,'参与跟进');await page.getByText('已展示 1–2 / 本次读取 2 条 · 已知量 2 条 · 完整总量未知',{exact:true}).waitFor();await page.getByText('确认实施边界',{exact:true}).waitFor();await page.getByText('周五提交方案',{exact:true}).waitFor();
 await go(page,'商机变动');await page.getByText(/1000000.00 → 700000.00/).waitFor();await page.getByText(/未记录或不可查看 · 预计回款/).waitFor();
 await go(page,'更多指标 · 预测确收');assert.equal(await page.locator('.rv-meeting-card').count(),1);await page.getByRole('heading',{name:'Q4 预测确收',exact:true}).waitFor();
}));
test('chapter cap is explicit and performs only one limit-100 request',()=>fixture(async page=>{
 await go(page,'新认领客户');await page.getByText('已展示 1–5 / 本次读取 100 条 · 总量 107 条',{exact:true}).waitFor();await page.getByText(/本次会议最多读取前 100 条，本章节已读取 100 条/).waitFor();
 assert.equal(await page.getByTestId('review-meeting-record').count(),5);
 const calls=await page.evaluate(()=>window.calls);assert.equal(calls.length,1);assert.equal(new URL(calls[0].path,'https://test/').searchParams.get('limit'),'100');
} ,true));
test('current task stock and quarter collection unavailability never become empty zero',()=>fixture(async page=>{
 await go(page,'当前待完成');await page.getByText('当前任务存量，包含待验收及未来到期；不是所选历史日期的待办快照。',{exact:true}).waitFor();await page.getByText('本章节暂不可用',{exact:true}).waitFor();assert.equal(await page.getByTestId('review-meeting-record').count(),0);assert.equal(await page.getByText(/已展示 0/).count(),0);
 await go(page,'当前商机池');await page.getByText('当前商机甲',{exact:true}).waitFor();await page.getByText('100.00 万元',{exact:true}).waitFor();
 await go(page,'季度重点回款');await page.getByText('本章节暂不可用',{exact:true}).waitFor();await page.getByText(/不代表实际到账/).waitFor();
}));
test('failed chapter supports explicit retry; cached chapter survives parent refresh until reopen',()=>fixture(async page=>{
 await page.evaluate(()=>{failKey='new_claimed_customers';});await go(page,'新认领客户');await page.getByText('本章节读取失败',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>calls.length),1);
 await page.getByRole('button',{name:'重试本章节',exact:true}).click();await page.getByTestId('review-meeting-record').first().waitFor();assert.equal(await page.evaluate(()=>calls.length),2);
 await page.evaluate(()=>refreshProps());assert.equal(await page.getByText(/新周期，不应覆盖/).count(),0);assert.equal(await page.evaluate(()=>calls.length),2);
 await page.getByRole('button',{name:'退出会议模式 · Esc',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});await page.evaluate(()=>enterMeeting());await page.getByRole('heading',{name:'季度目标与当前预测',exact:true}).waitFor();await page.getByText(/选定周期：新周期/).waitFor();
 await go(page,'新认领客户');assert.equal(await page.evaluate(()=>calls.length),3);
}));
test('closing a pending chapter ignores its late response and account changes dismiss the deck',()=>fixture(async page=>{
 await page.evaluate(()=>{holdKey='new_claimed_customers';});await next(page);await next(page);await page.locator('.rv-meeting-loading').waitFor();
 await page.getByRole('button',{name:'退出会议模式 · Esc',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});await page.evaluate(()=>releaseRequest());assert.equal(await page.getByRole('dialog').count(),0);
 await page.evaluate(()=>{holdKey='';enterMeeting();});await page.getByRole('heading',{name:'季度目标与当前预测',exact:true}).waitFor();await page.evaluate(()=>{actor={...actor,userId:'other'};rerender();});await page.getByRole('dialog').waitFor({state:'hidden'});assert.equal(await page.evaluate(()=>calls.length),1);
}));
test('a late earlier chapter cannot move the reader away from the current chapter',()=>fixture(async page=>{
 await page.evaluate(()=>{holdKey='new_claimed_customers';});await next(page);await next(page);await page.locator('.rv-meeting-loading').waitFor();
 await next(page);await page.getByText('确认实施边界',{exact:true}).waitFor();await page.evaluate(()=>releaseRequest());
 await page.getByRole('heading',{name:'参与跟进',exact:true}).waitFor();assert.equal(await page.getByText('确认实施边界',{exact:true}).isVisible(),true);
 await page.getByRole('button',{name:'上一页',exact:true}).click();await page.getByRole('heading',{name:'新认领客户 · 1/2',exact:true}).waitFor();assert.equal(await page.getByTestId('review-meeting-record').count(),5);assert.equal(await page.evaluate(()=>calls.length),2);
}));
