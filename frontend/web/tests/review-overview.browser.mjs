// Synthetic responses through the real Review transport. No service is reachable.
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || '/Users/zhouwei5/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const root=fileURLToPath(new URL('../',import.meta.url));
const bundle=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';
import ReviewOverview from './department-ui/review/ReviewOverview.jsx';
import ReviewSummaryPanel from './department-ui/review/report/ReviewSummaryPanel.jsx';
import {options as reportOptions} from './tests/review-report-fixtures.mjs';
import {REVIEW_METRICS,REVIEW_ACTIVITIES,REVIEW_POOL_FILTERS} from './department-ui/review/overview-model.js';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';
window.calls=[];window.actor={workspaceId:'company',userId:'leader',permissionVersion:1};window.routes=[];
const selection={scope:'team',team_id:'team-a',anchor_date:'2026-10-14',period_type:'day'};
function envelope(s){return {selection:s,scope:{kind:s.scope,team_id:s.team_id,member_id:s.member_id,label:s.scope==='team'?'南区团队':'成员甲'},period:{label:'2026-10-14'},data_as_of:'2026-10-14T06:00:00Z'};}
function overview(s){return {...envelope(s),contract_version:'review.overview.v1',quarter:{quarter:4,time_progress:'0.15217391'},members:[{id:'a',name:'成员甲'},{id:'hidden',name:'无成员页授权'}],metrics:REVIEW_METRICS.map(({code},i)=>({metric_code:code,value:i===0?'0.00':i===1?null:'300000.00',known_subtotal:i===1?'100000.00':null,coverage:i===1?'partial':'complete',missing_reasons:i===1?['source_scope_not_fully_authorized']:[],status:i===0?'behind':'no_target',target:{status:i===0?'effective':'no_target',value:i===0?'1000000.00':null},rate:i===0?'0.00':null,gap:i===0?'1000000.00':null,day_change:null,baseline:{status:'no_baseline',reason:'暂无可靠历史基线'}})),activities:REVIEW_ACTIVITIES.map(({code},i)=>({code,count:i===0?null:code==='formal_visits'?45:1,known_count:i===0?2:code==='formal_visits'?45:1,coverage:i===0?'partial':'complete',missing_reasons:i===0?['source_scope_not_fully_authorized']:[]})),pool_counts:Object.fromEntries(REVIEW_POOL_FILTERS.map(({value})=>[value,value==='high_risk'?null:1])),filter_availability:Object.fromEntries(REVIEW_POOL_FILTERS.map(({value})=>[value,{available:value!=='high_risk',coverage:value==='high_risk'?'unavailable':'complete',reason:value==='high_risk'?'风险读取权限不足':null}]))};}
const visits=Array.from({length:45},(_,i)=>({id:'visit-'+i,customer_name:'跟进客户 '+(i+1),summary:'确认实施范围和验收条件',next_action:'周五提交方案',interaction_mode_code:'phone_voice',interaction_at:'2026-10-14T03:00:00Z',recorded_at:'2026-10-14T04:00:00Z'}));
const updated=[{id:'changed',name:'重点项目回款调整',customer_name:'示例客户',event_count:2,occurred_at:'2026-10-14T03:00:00Z',events:[{id:'event-one',actor_id:'a',occurred_at:'2026-10-14T03:00:00Z',changes:[{field:'collection_amount',label:'Q4预计回款',before:'1000000.00',after:'700000.00'},{field:'sales_channel',before:'direct',after:'partner'},{label:'不变字段',before:'same',after:'same'}]},{id:'event-two',actor_id:'a',occurred_at:'2026-10-14T04:00:00Z',changes:[{field:'sales_channel',before:'partner',after:'direct'}]}]}];
window.failOverview=false;
const client={request:async options=>{
 calls.push(options);if(options.method!=='GET')throw Error('Unexpected write');
 const url=new URL(options.path,'https://review-overview.test'),q=Object.fromEntries(url.searchParams),s={scope:q.scope,anchor_date:q.anchor_date,period_type:q.period_type,...(q.team_id?{team_id:q.team_id}:{}),...(q.member_id?{member_id:q.member_id}:{})};
 if(url.pathname==='/review/overview'){if(failOverview)throw Error('概览连接中断');return overview(s);}
 const items=q.activity==='formal_visits'?visits:q.activity==='updated_opportunities'?updated:q.activity?[{id:'task-one',name:'客户验证',status_label:'待验收',due_at:'2026-11-01T09:00:00Z',overdue:false}]:[{id:'opportunity',name:q.filter==='key_collection'?'已赢单重点回款':'进行中示例商机',customer_id:'customer',customer_name:'示例客户',owner_name:'成员甲',stage_code:'solution',collection_amount:'1000000.00',recognized_amount:'300000.00',actions:{can_view:true,can_follow_up:false,can_create_task:false},metric_contributions:{forecast_collection:{eligible:true,value:'1000000.00'}}}];
 return {...q,...envelope(s),contract_version:'review.page.v1',limit:Number(q.limit),offset:Number(q.offset),items:items.slice(Number(q.offset),Number(q.offset)+Number(q.limit)),total:items.length,known_total:items.length,coverage:'complete',available:true,has_more:Number(q.offset)+Number(q.limit)<items.length};
}};
window.SalesRuntime={app:{globalData:{get session(){return actor;}}},wx:{navigateTo:({url})=>routes.push(url)},require:name=>name==='utils/access'?{identity:s=>JSON.stringify(s)}:client};
const summaryService={options:async()=>reportOptions};
function App(){const[s,setSelection]=useState(selection),[refresh,setRefresh]=useState(0),[summary,setSummary]=useState(false);window.enableSummary=()=>setSummary(true);window.refreshOverview=()=>setRefresh(x=>x+1);return <main style={{padding:24,maxWidth:1600,margin:'auto'}}><ReviewOverview selection={s} refresh={refresh} sidePanel={summary?<ReviewSummaryPanel selection={{scope:'self',anchor_date:'2026-01-14',period_type:'day'}} service={summaryService}/>:null} memberOptions={['a']} onMember={id=>setSelection({scope:'member',member_id:id,anchor_date:'2026-10-14',period_type:'day'})}/></main>;}
createRoot(document.getElementById('root')).render(<PcProvider><App/></PcProvider>);
`},bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/review-overview/browser',define:{'process.env.NODE_ENV':'"production"'}});
const js=bundle.outputFiles.find(file=>file.path.endsWith('.js')).text,css=bundle.outputFiles.find(file=>file.path.endsWith('.css')).text;
let browser;
before(async()=>{browser=await chromium.launch({channel:'chrome',headless:true});await mkdir(root+'.runtime/review-overview',{recursive:true});});
after(async()=>{await browser?.close();});
async function fixture(run){
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[],unexpected=[];
 page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',route=>{if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#f5f6f8}'+css+'</style><main class="department-ui"><div id="root"></div></main><script>'+js+'</script>'});unexpected.push(route.request().url());return route.abort();});
 try{await page.goto('https://review-overview.test');await page.getByTestId('review-overview').waitFor();await page.getByText('跟进客户 1',{exact:true}).waitFor();await run(page);assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);assert.equal(await page.evaluate(()=>calls.every(call=>call.method==='GET')),true);}finally{await context.close();}
}
test('honest metric cards, natural table height, selectable page size and inline activity switching',()=>fixture(async page=>{
 const cards=page.getByTestId('review-metric-cards').first().locator('.rv-metric');assert.equal(await cards.count(),2);
 assert.equal(await cards.nth(0).locator('.rv-metric-main strong').innerText(),'0.00');
 assert.equal(await cards.nth(1).locator('.rv-metric-main strong').innerText(),'10.00');
 assert.match(await cards.nth(1).innerText(),/已知小计/);assert.equal(await page.getByRole('heading',{name:'Q4 预测确收',exact:true}).isVisible(),false);
 await page.getByText('更多指标 · 预测确收',{exact:true}).click();await page.getByRole('heading',{name:'Q4 预测确收',exact:true}).waitFor();
 const table=page.getByTestId('review-activity-table');assert.equal(await table.locator('tbody tr.ant-table-row').count(),20);
 assert.ok((await table.boundingBox()).height>1000); // no four-row vertical scrolling box
 await table.getByRole('combobox').click();await page.getByText('50 条/页',{exact:true}).last().click();
 await page.getByText('跟进客户 45',{exact:true}).waitFor();assert.equal(await table.locator('tbody tr.ant-table-row').count(),45);
 await page.getByTestId('review-action-tiles').getByRole('button',{name:/商机变动/}).click();await page.getByText('重点项目回款调整',{exact:true}).waitFor();
 await table.locator('.ant-table-row-expand-icon').click();await page.getByText('Q4预计回款',{exact:true}).waitFor();await page.getByText('700000.00',{exact:true}).waitFor();
 const changes=table.locator('.rv-change-detail');assert.equal(await changes.locator('tbody tr.ant-table-row').count(),3);assert.equal(await changes.getByText('成员甲',{exact:true}).count(),3);assert.equal(await changes.getByText('直销',{exact:true}).count(),2);assert.equal(await changes.getByText('不变字段',{exact:true}).count(),0);
 assert.equal(await page.evaluate(()=>routes.length),0);
}));
test('pool filters and member selector respect available scope; details and meeting are reachable',()=>fixture(async page=>{
 assert.equal(await page.getByRole('button',{name:/高风险/}).isDisabled(),true);
 await page.getByRole('button',{name:/季度重点回款/}).click();await page.getByText('已赢单重点回款',{exact:true}).waitFor();
 await page.getByPlaceholder('搜索商机、客户或负责人').fill('示例');await page.getByPlaceholder('搜索商机、客户或负责人').press('Enter');
 await page.waitForFunction(()=>calls.some(row=>row.path.includes('q=%E7%A4%BA%E4%BE%8B')));
 assert.equal(await page.getByRole('button',{name:'补跟进',exact:true}).count(),0);
 await page.locator('.rv-metric').first().getByRole('button',{name:'查看明细 →'}).click();await page.getByTestId('review-metric-detail').waitFor();await page.getByRole('columnheader',{name:'纳入金额（万元）',exact:true}).waitFor();await page.locator('.ant-drawer-close').click();
 await page.getByRole('button',{name:'会议模式',exact:true}).click();await page.getByRole('heading',{name:'季度目标与当前预测',exact:true}).waitFor();await page.keyboard.press('Escape');
 await page.getByRole('combobox',{name:'展开团队成员'}).click();assert.equal(await page.getByText('无成员页授权',{exact:true}).count(),0);await page.getByText('成员甲',{exact:true}).last().click();
 await page.getByRole('heading',{name:'2026-10-14 · 成员甲',exact:true}).waitFor();assert.ok(await page.evaluate(()=>calls.some(row=>row.path.includes('member_id=a'))));
}));
test('failed refresh removes previous values and retries; desktop and narrow layout remain usable',()=>fixture(async page=>{
 await page.getByTestId('review-action-tiles').getByRole('button',{name:/当前待完成/}).click();await page.getByText('客户验证',{exact:true}).waitFor();
 await page.locator('.ant-spin-blur').waitFor({state:'hidden'});await page.waitForFunction(()=>[...document.querySelectorAll('.ant-spin-container')].every(el=>Number(getComputedStyle(el).opacity)>.99));await page.screenshot({path:root+'.runtime/review-overview/overview-desktop.png',fullPage:true});
 await page.setViewportSize({width:600,height:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:root+'.runtime/review-overview/overview-narrow.png',fullPage:true});
 await page.evaluate(()=>{failOverview=true;refreshOverview();});await page.getByText('概览连接中断',{exact:true}).waitFor();assert.equal(await page.locator('.rv-metric').count(),0);
 await page.evaluate(()=>{failOverview=false;});await page.getByRole('button',{name:'重新读取概览'}).click();await page.getByTestId('review-metric-cards').first().waitFor();
}));

test('daily AI panel sits beside activity details and stacks on narrow screens without starting generation',()=>fixture(async page=>{
 await page.evaluate(()=>enableSummary());await page.getByRole('button',{name:'生成AI 日总结',exact:true}).waitFor();
 await page.getByTestId('review-action-tiles').getByRole('button',{name:/当前待完成/}).click();await page.getByText('客户验证',{exact:true}).waitFor();
 const left=await page.locator('.rv-activities-panel').boundingBox(),right=await page.getByTestId('review-summary-panel').boundingBox();
 assert.ok(right.x>left.x+left.width-1);assert.ok(Math.abs(right.y-left.y)<2);
 await page.waitForFunction(()=>[...document.querySelectorAll('.ant-spin-container')].every(el=>Number(getComputedStyle(el).opacity)>.99));
 await page.screenshot({path:root+'.runtime/review-overview/review-with-ai-desktop.png',fullPage:true});
 await page.setViewportSize({width:600,height:1000});const stacked=await page.getByTestId('review-summary-panel').boundingBox(),above=await page.locator('.rv-activities-panel').boundingBox();assert.ok(stacked.y>=above.y+above.height);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:root+'.runtime/review-overview/review-with-ai-narrow.png',fullPage:true});
}));
