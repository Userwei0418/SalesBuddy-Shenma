import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'/Users/zhouwei5/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const built=await build({stdin:{resolveDir:process.cwd(),loader:'jsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';import ReviewOkr from './department-ui/review/targets/okr/ReviewOkr.jsx';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-ui.css';
window.actor={workspaceId:'workspace-a',userId:'user-a',permissionVersion:1};window.permissions=['target.read','target.submit','target.manage'];
window.SalesRuntime={app:{globalData:{get session(){return actor;}},can:p=>permissions.includes(p)},require:name=>({identity:s=>JSON.stringify(s),scopeFor:()=>({allowed:true,scopes:['workspace']})})};
window.calls=[];window.reads=[];window.fail='';window.left=0;
window.people=[{id:'user-a',name:'测试成员甲'},{id:'user-b',name:'测试成员乙'}];
window.options={people:[{...people[0],can_manage:true}],teams:[{id:'team-a',name:'测试南区',can_manage:true}],departments:[]};
window.metrics=[{code:'forecast_recognized',name:'预计确收',target_kind:'forecast_recognized',unit:'元',decimal_places:2,source_kind:'forecast',definition:'高概率商机的含税预计确收',active:true},{code:'custom_visits',name:'有效沟通',target_kind:'custom_visits',unit:'次',decimal_places:0,source_kind:'manual',definition:'经负责人确认的有效沟通',active:true}];
window.objective={id:'o1',title:'提升第四季度重点客户经营质量',period_type:'quarter',period_start:'2026-10-01',period_end:'2026-12-31',scope_type:'team',team_id:'team-a',user_ref_id:null,department_code:'sales',owner_user_id:'user-a',collaborator_user_ids:['user-b'],parent_id:null,status:'active',version_no:3,editable:true,key_result_count:2};
window.objectives=[objective];window.krs=[{id:'kr1',objective_id:'o1',title:'推进重点项目预计确收',metric_code:'forecast_recognized',metric:metrics[0],owner_user_id:'user-a',collaborator_user_ids:['user-b'],parent_id:null,status:'active',objective_status:'active',editable:true,linkable:true,version_no:5,scope:{scope_type:'team',team_id:'team-a'},period:{type:'quarter',start:'2026-10-01',end:'2026-12-31'},progress_mode:'automatic',effective_target:{id:'t1',value:'1000000.00'},progress:{value:null,status:'missing',reason:'no_actual_evidence'}},{id:'kr2',objective_id:'o1',title:'完成关键客户沟通',metric_code:'custom_visits',metric:metrics[1],owner_user_id:'user-a',collaborator_user_ids:[],parent_id:null,status:'active',objective_status:'active',editable:true,linkable:true,version_no:7,scope:{scope_type:'team',team_id:'team-a'},period:{type:'quarter',start:'2026-10-01',end:'2026-12-31'},progress_mode:'manual',effective_target:null,progress:{value:'2',status:'confirmed',evidence:'已完成两次拜访',confirmed_by_user_id:'user-a',confirmed_at:'2026-10-14T08:00:00Z'}}];
window.targets=[{...objective,id:'t1',kind:'forecast_recognized',effective_amount:'1000000.00'},{...objective,id:null,row_key:'pending',kind:'forecast_recognized',effective_amount:null,pending_change:{proposed_amount:'2000000.00'}}];
const post=(kind,body)=>{calls.push({kind,body});if(fail==='conflict')throw Object.assign(Error('stale'),{statusCode:409});if(fail==='forbidden')throw Object.assign(Error('forbidden'),{statusCode:403});};
window.client={objectives:async params=>{reads.push({kind:'objectives',...params});return{items:objectives.slice(params.offset,params.offset+params.limit),total:objectives.length};},metrics:async()=>({items:metrics}),options:async()=>options,participants:async()=>({items:people}),objectiveParents:async()=>[],keyResultParents:async()=>krs,targetsFor:async()=>targets,recipients:async()=>people,
keyResults:async params=>{reads.push({kind:'keyResults',...params});return{items:krs.slice(params.offset,params.offset+params.limit),total:krs.length};},
saveObjective:async(id,body)=>{post('objective',body);objectives=[{...objective,...body,id:id||'new-o',key_result_count:0}];return objectives[0];},
saveKeyResult:async(id,body)=>{post('keyResult',body);const item={...krs[0],...body,id:id||'new-kr',metric:metrics.find(m=>m.code===body.metric_code)||krs[0].metric};krs=[...krs.filter(row=>row.id!==id),item];return item;},
confirmProgress:async(id,body)=>{post('progress',body);krs=krs.map(row=>row.id===id?{...row,version_no:row.version_no+1,progress:{...body,status:'confirmed',confirmed_by_user_id:'user-a',confirmed_at:'2026-10-14T09:00:00Z'}}:row);return {key_result:krs.find(row=>row.id===id)};},
mention:async(kind,id,body)=>{post('mention',body);return{status:'queued',created:1,permission_changed:false};}};
const root=createRoot(document.getElementById('root'));window.editorRef=React.createRef();window.show=()=>root.render(<ReviewOkr ref={editorRef} client={client} onBack={()=>editorRef.current.leave(()=>{left++;})}/>);show();
`},bundle:true,write:false,outdir:'.runtime/review-okr/browser',loader:{'.svg':'dataurl'},define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(file=>file.path.endsWith('.js')).text,css=built.outputFiles.find(file=>file.path.endsWith('.css')).text;
let browser;before(async()=>{browser=await chromium.launch({channel:'chrome',headless:true});});after(async()=>{await browser?.close();});
async function fixture(run){const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));await context.route('**/*',route=>route.request().isNavigationRequest()?route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><meta charset="utf-8"><style>${css}body{font-family:Arial,sans-serif;background:#f5f6f8;margin:0}#root{padding:24px}</style></head><body><div id="root"></div><script>${js}</script></body></html>`}):route.abort());try{await page.goto('https://review-okr.test/');await page.getByTestId('okr-objective').waitFor();await run(page);assert.deepEqual(errors,[]);}finally{await context.close();}}
async function choose(page,name,label){await page.getByRole('combobox',{name,exact:true}).click();await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)').getByText(label,{exact:true}).click();}
const posts=page=>page.evaluate(()=>calls);
async function expand(page){await page.getByRole('button',{name:'展开目标：提升第四季度重点客户经营质量',exact:true}).click();await page.getByTestId('okr-kr-row').first().waitFor();}
async function newO(page){await page.getByRole('button',{name:'新建目标 O',exact:true}).click();await page.getByRole('textbox',{name:'目标标题',exact:true}).fill('经营复盘目标');await choose(page,'O 归属对象','测试南区');await choose(page,'O/KR 负责人','测试成员甲');}
test('inline objective creation and cancellation; no write until save',()=>fixture(async page=>{await newO(page);assert.equal((await posts(page)).length,0);await page.getByRole('button',{name:'取消编辑',exact:true}).click();assert.equal(await page.getByTestId('okr-editor').count(),0);assert.equal((await posts(page)).length,0);}));
test('objective save sends period identity and participant IDs, then reads back',()=>fixture(async page=>{await newO(page);await page.getByRole('button',{name:'保存 O/KR',exact:true}).click();await page.getByRole('heading',{name:/经营复盘目标/}).waitFor();const body=(await posts(page))[0].body;assert.equal(body.scope_type,'team');assert.equal(body.team_id,'team-a');assert.equal(body.owner_user_id,'user-a');assert.equal(Object.hasOwn(body,'amount'),false);}));
test('KR references an effective target and never writes a second target amount',()=>fixture(async page=>{await expand(page);await page.getByRole('button',{name:'新增 KR',exact:true}).click();await page.getByRole('textbox',{name:'关键结果标题',exact:true}).fill('提升季度预计确收');await choose(page,'KR 指标','预计确收 · 元');await choose(page,'KR 生效目标','1,000,000.00 元');await page.getByRole('button',{name:'保存 O/KR',exact:true}).click();await page.getByText('提升季度预计确收',{exact:true}).waitFor();const body=(await posts(page))[0].body;assert.equal(body.target_id,'t1');assert.equal(body.objective_id,'o1');assert.equal(Object.hasOwn(body,'amount'),false);}));
test('automatic missing progress stays missing; manual confirmation requires evidence and can explicitly confirm zero',()=>fixture(async page=>{await expand(page);assert.equal(await page.getByTestId('okr-progress-value').first().innerText(),'—\n元');assert.equal(await page.getByRole('button',{name:'更新进展',exact:true}).count(),1);await page.getByRole('button',{name:'更新进展',exact:true}).click();await page.getByRole('textbox',{name:'当前累计进展',exact:true}).fill('0');await page.getByRole('textbox',{name:'进展依据',exact:true}).fill('重新核实本期尚无有效沟通');await page.getByRole('button',{name:'保存 O/KR',exact:true}).click();await page.getByText('请明确确认此进展及依据',{exact:true}).waitFor();assert.equal((await posts(page)).length,0);await page.getByRole('checkbox',{name:'我已核实此累计进展及依据',exact:true}).check();await page.getByRole('button',{name:'保存 O/KR',exact:true}).click();await page.getByText('进展及依据已确认。',{exact:true}).waitFor();assert.deepEqual((await posts(page))[0].body,{version_no:7,value:'0',evidence:'重新核实本期尚无有效沟通',confirmed:true});await page.waitForFunction(()=>!document.querySelector('[aria-label="刷新 O/KR"]').disabled);if(process.env.REVIEW_OKR_SCREENSHOT)await page.screenshot({path:process.env.REVIEW_OKR_SCREENSHOT,fullPage:true});}));
test('version conflicts keep draft and prevent silent rebase; leave guard preserves or discards explicitly',()=>fixture(async page=>{await page.evaluate(()=>{fail='conflict';});await newO(page);await page.getByRole('button',{name:'保存 O/KR',exact:true}).click();await page.getByText('记录已更新，当前输入已保留。请刷新核对后重新编辑。',{exact:true}).waitFor();assert.equal(await page.getByRole('textbox',{name:'目标标题',exact:true}).inputValue(),'经营复盘目标');assert.equal(await page.getByRole('button',{name:'保存 O/KR',exact:true}).isDisabled(),true);await page.getByRole('button',{name:'返回本期概览',exact:true}).click();await page.getByRole('button',{name:'继续编辑',exact:true}).click();await page.getByRole('button',{name:'继续编辑',exact:true}).waitFor({state:'hidden'});assert.equal(await page.evaluate(()=>left),0);assert.equal((await posts(page)).length,1);}));
test('@ recipient authorization is checked by server; refusal preserves choices and cancel sends nothing further',()=>fixture(async page=>{await page.getByRole('button',{name:'@ 提醒',exact:true}).click();await choose(page,'提醒接收人','测试成员乙');await page.locator('.review-okr-editor-heading').click();await page.evaluate(()=>{fail='forbidden';});await page.getByRole('button',{name:'保存 O/KR',exact:true}).click();await page.getByText('当前账号或所选接收人不满足提醒权限，未发送。请核对后重试。',{exact:true}).waitFor();assert.equal((await posts(page))[0].body.recipient_user_ids[0],'user-b');await page.getByRole('button',{name:'取消编辑',exact:true}).click();assert.equal((await posts(page)).length,1);}));
test('read-only account can inspect O/KR and evidence without exposing mutations',()=>fixture(async page=>{await page.evaluate(()=>{permissions=['target.read'];objectives=objectives.map(row=>({...row,editable:false}));krs=krs.map(row=>({...row,editable:false}));options={people:[],teams:[],departments:[]};actor={...actor,permissionVersion:2};show();});await page.getByTestId('okr-objective').waitFor();assert.equal(await page.getByRole('button',{name:'新建目标 O',exact:true}).isDisabled(),true);await expand(page);assert.equal(await page.getByRole('button',{name:'更新进展',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'编辑 KR',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'@ 提醒',exact:true}).count(),0);assert.equal((await posts(page)).length,0);}));

test('O and KR page sizes persist independently and reset the current page when changed',()=>fixture(async page=>{
  assert.equal(await page.evaluate(()=>reads.find(row=>row.kind==='objectives').limit),20);
  await page.evaluate(()=>{objectives=Array.from({length:61},(_,i)=>({...objective,id:'o'+(i+1),title:i?`目标 ${i+1}`:objective.title}));krs=Array.from({length:61},(_,i)=>({...krs[0],id:'kr'+(i+1),title:`关键结果 ${i+1}`}));});
  await page.getByRole('button',{name:'刷新 O/KR',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-testid="okr-objective"]').length===20);
  const objectivePager=page.getByTestId('okr-objective-pagination');
  await objectivePager.locator('.ant-pagination-item-2').click();
  await page.getByRole('button',{name:'展开目标：目标 21',exact:true}).waitFor();
  await objectivePager.getByRole('combobox').click();
  const objectiveSizes=objectivePager.locator('.ant-select-item-option-content');
  assert.deepEqual(await objectiveSizes.allTextContents(),['10 / page','20 / page','50 / page','100 / page']);
  await objectivePager.getByRole('option',{name:'50 / page',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-testid="okr-objective"]').length===50);
  assert.deepEqual(await page.evaluate(()=>{const {limit,offset}=reads.filter(row=>row.kind==='objectives').at(-1);return {limit,offset};}),{limit:50,offset:0});
  await expand(page);
  assert.equal(await page.evaluate(()=>reads.filter(row=>row.kind==='keyResults').at(-1).limit),20);
  const krPager=page.getByTestId('okr-kr-pagination');
  await krPager.locator('.ant-pagination-item-2').click();
  await page.getByText('关键结果 21',{exact:true}).waitFor();
  await krPager.getByRole('combobox').click();
  const krSizes=krPager.locator('.ant-select-item-option-content');
  assert.deepEqual(await krSizes.allTextContents(),['10 / page','20 / page','50 / page','100 / page']);
  await krPager.getByRole('option',{name:'10 / page',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-testid="okr-kr-row"]').length===10);
  assert.deepEqual(await page.evaluate(()=>{const {limit,offset}=reads.filter(row=>row.kind==='keyResults').at(-1);return {limit,offset};}),{limit:10,offset:0});
  assert.match(await objectivePager.innerText(),/50 \/ page/);
  assert.equal(await page.locator('.review-okr-results').evaluate(element=>element.scrollHeight<=element.clientHeight+1),true);
  assert.equal((await posts(page)).length,0);
  await page.reload();await page.getByTestId('okr-objective').waitFor();
  assert.equal(await page.evaluate(()=>reads.find(row=>row.kind==='objectives').limit),50);
  await expand(page);
  assert.equal(await page.evaluate(()=>reads.find(row=>row.kind==='keyResults').limit),10);
  assert.equal(await krPager.getByRole('combobox').isVisible(),true);
  assert.equal((await posts(page)).length,0);
}));
