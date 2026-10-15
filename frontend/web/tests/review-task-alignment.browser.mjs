// Isolated browser only. Synthetic transport cannot reach local or production services.
import {before, after, test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';
import Fields,{ContributionFields,TaskAlignmentSummary} from './department-ui/review/tasks/TaskAlignmentFields.jsx';
import {alignmentPayload,alignmentError,emptyAlignment} from './department-ui/review/tasks/model.js';
const receipts=(()=>{const module={exports:{}};${readFileSync(new URL('../source/miniprogram/utils/requestIdentity.js',import.meta.url),'utf8')};return module.exports;})();
import TaskCreate from './department-ui/TaskCreate.jsx';
import TaskDetail from './department-ui/TaskDetail.jsx';
import TaskAlignmentDetail from './department-ui/review/tasks/TaskAlignmentDetail.jsx';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';
window.metrics=[{code:'daily_routine',name:'日常事务',active:true,source_kind:'category',decimal_places:0,unit:'项'},{code:'visit_count',name:'跟进次数',active:true,source_kind:'actual',decimal_places:0,unit:'次',goal_selectable:true}];
window.krs=[{id:'kr-one',title:'团队客户沟通',metric_code:'visit_count',status:'active',linkable:true,period:{type:'quarter',start:'2026-10-01',end:'2026-12-31'},scope:{scope_type:'team',team_id:'team-a'}},{id:'kr-two',title:'重点客户沟通',metric_code:'visit_count',status:'active',linkable:true,period:{type:'quarter',start:'2026-10-01',end:'2026-12-31'},scope:{scope_type:'team',team_id:'team-a'}}];
window.targets=[{id:'target-one',kind:'visit_count',metric:metrics[1],state:'pending_change',period_type:'quarter',period_start:'2026-10-01',period_end:'2026-12-31',scope_type:'person',user_ref_id:'person-one',user_name:'成员甲',version_no:3},{id:null,row_key:'pending',kind:'visit_count',metric:metrics[1],state:'pending_new',period_type:'quarter',period_start:'2026-10-01',period_end:'2026-12-31',scope_type:'team',team_id:'team-a',team_name:'测试团队'}];
window.calls=[];window.actor={workspaceId:'workspace-a',userId:'actor-a'};
const storage=new Map();window.wx={getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value)};
const client={getAuth:()=>({actor:{workspace_id:'workspace-a',user_id:'actor-a'}}),getBaseUrl:()=>'https://review-task.test/api/v1',request:async options=>{calls.push(options);if(window.directoryFails)throw Object.assign(Error('指标目录暂不可用'),{statusCode:503});if(options.method==='PATCH'&&window.writeConflict)throw Object.assign(Error('任务版本已更新'),{statusCode:409});if(options.path==='/tasks/alignment-capabilities')return {supported_versions:window.mode==='unsupported'?[1]:[1,2],preferred_version:2};if(options.path.startsWith('/review/targets')){const annual=options.path.includes('period_type=year');return {items:annual?[{...targets[0],id:'target-year',period_type:'year',period_start:'2026-01-01',period_end:'2026-12-31',state:'effective'}]:targets,total:annual?1:2};}return options.method==='PATCH'?{...window.detailTask,version_no:4,alignment:{...options.data.alignment,contract_version:2,basis:options.data.alignment.other_reason?'other':'objects',metric_codes:options.data.alignment.target_ids.length||options.data.alignment.key_result_ids.length?['visit_count']:[]}}:options.path.startsWith('/review/key-results')?{items:krs,total:2}:{items:metrics};}};
window.SalesRuntime={app:{globalData:{get session(){return actor;}}},wx:{showToast:()=>{}},require:name=>name==='utils/requestIdentity'?receipts:name==='utils/access'?{identity:s=>s.workspaceId+':'+s.userId}:client};
window.payload=null;window.mode=new URL(location.href).searchParams.get('mode')||'fields';
const originalData={taskType:'daily',description:'完成客户需求确认',priorities:['普通','中','高'],selectedPriority:'中',members:[],selectedMembers:[{id:'member',name:'成员甲'}],selectedNames:'成员甲',customDueDate:'2099-10-14',customDueTime:'17:00'};
const fakePage={data:originalData,submitTask(){calls.push({action:'submit'});}};
function App(){const[draft,setDraft]=useState(emptyAlignment()),[attempted,setAttempted]=useState(false),[detailTask,setDetailTask]=useState({id:'task-a',version_no:3,status:'in_progress',can_coordinate:true,alignment:{metric_codes:['daily_routine'],direct_metric_codes:['daily_routine'],key_result_ids:[],...(window.mode==='hidden'?{contract_version:2,basis:'objects',restricted_target_count:1}: {})}});window.currentDraft=draft;window.detailTask=detailTask;
 if(mode==='full-detail'){const task={...detailTask,canComplete:true,selfAssigned:true,title:'跟进需求',description:'对齐客户需求并记录下一步',history:Array.from({length:8},(_,i)=>({id:i,label:'历史记录',note:'保留原有任务处理记录'}))};return <div style={{height:700}}><TaskDetail page={{data:{task},confirmTaskMutation:()=>{}}} data={{task,completionNote:'已完成'}} invoke={()=>{}} invokeOn={()=>{}} select={()=>null}/></div>;}
 if(mode==='detail'||mode==='hidden'){const page={data:{task:detailTask},confirmTaskMutation:options=>{window.confirmation=options;window.executeConfirmation=async()=>{try{const result=await options.submit();setDetailTask(result);options.onSuccess(result);}catch{}};}};return <div style={{padding:24,maxWidth:760}}><TaskAlignmentDetail task={detailTask} page={page} directory={{owner:'workspace-a:actor-a',status:'ready',metrics,keyResults:krs,targets,targetRows:targets,krStatus:'ready',targetStatus:'ready'}} busy={false}/></div>;}
 if(mode==='create'||mode==='unsupported')return <TaskCreate page={fakePage} data={originalData} invoke={()=>{}} select={()=>null} invokeOn={()=>{}}/>;
 return <div style={{maxWidth:760,padding:24}}><Fields directory={{metrics,keyResults:krs,targets,targetRows:targets}} draft={draft} onChange={setDraft} error={attempted?alignmentError(draft,metrics,krs,targets):''}/><button onClick={()=>{setAttempted(true);try{window.payload=alignmentPayload(draft,metrics,krs,targets);}catch{}}}>测试保存</button><TaskAlignmentSummary alignment={payload} metrics={metrics} keyResults={krs}/></div>;
}createRoot(document.getElementById('root')).render(<PcProvider><App/></PcProvider>);
`},bundle:true,write:false,loader:{'.svg':'dataurl'},outdir:'.runtime/review-task-alignment/browser',define:{'process.env.NODE_ENV':'"production"'}});
const js=bundle.outputFiles.find(file=>file.path.endsWith('.js')).text,css=bundle.outputFiles.find(file=>file.path.endsWith('.css')).text;
let browser;
before(async()=>{browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();});
async function fixture(run,mode='fields',directoryFails=false){
 const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage(),errors=[],unexpected=[];
 await page.addInitScript(value=>{window.directoryFails=value;},directoryFails);
 page.setDefaultTimeout(7000);page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',route=>{if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:'<!doctype html><meta charset="utf-8"><style>'+css+'</style><main class="department-ui"><div id="root"></div></main><script>'+js+'</script>'});unexpected.push(route.request().url());return route.abort();});
 try{await page.goto('https://review-task.test/?mode='+mode);await run(page);assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);}finally{await context.close();}
}
async function choose(page,label,name){await page.getByRole('combobox',{name:label,exact:true}).click();await page.getByText(name,{exact:true}).last().click();await page.keyboard.press('Escape');}
test('effective target plus multi-KR derive one metric; pending new target is disabled and server IDs persist',()=>fixture(async page=>{
 await page.getByRole('button',{name:'测试保存'}).click();await page.getByText('请选择至少一个业绩目标或 KR，或填写其他原因',{exact:true}).waitFor();
 await page.getByRole('combobox',{name:'任务关联业绩目标',exact:true}).click();assert.equal(await page.locator('.ant-select-item-option-disabled').count(),1);await page.keyboard.press('Escape');
 await choose(page,'任务关联业绩目标',/跟进次数 · 成员甲/);await choose(page,'任务关联 KR',/团队客户沟通/);await choose(page,'任务关联 KR',/重点客户沟通/);
 assert.equal(await page.locator('.review-task-derived .ant-tag').count(),1);
 await page.getByRole('button',{name:'测试保存'}).click();assert.deepEqual(await page.evaluate(()=>payload),{target_ids:['target-one'],key_result_ids:['kr-one','kr-two'],other_reason:null,expected_contribution:null});
}));
test('other reason explicitly replaces objects and contribution; Unicode whitespace is not a valid reason',()=>fixture(async page=>{
 await choose(page,'任务关联业绩目标',/跟进次数 · 成员甲/);await page.getByRole('button',{name:'填写期望贡献（选填）'}).click();
 await page.getByRole('radio',{name:'其他原因',exact:true}).check();assert.equal(await page.getByTestId('task-expected-contribution').count(),0);
 await page.getByRole('textbox',{name:'任务其他原因',exact:true}).fill('\uFEFF\u00A0');await page.getByRole('button',{name:'测试保存'}).click();await page.getByText('请说明此任务暂不关联目标或 KR 的原因',{exact:true}).waitFor();
 await page.getByRole('textbox',{name:'任务其他原因',exact:true}).fill('\uFEFF  开展团队培训  \u00A0');await page.getByRole('button',{name:'测试保存'}).click();assert.deepEqual(await page.evaluate(()=>payload),{target_ids:[],key_result_ids:[],other_reason:'开展团队培训',expected_contribution:null});
}));
test('optional contribution validates count precision and retains declared zero',()=>fixture(async page=>{
 await choose(page,'任务关联 KR',/团队客户沟通/);await page.getByRole('button',{name:'填写期望贡献（选填）'}).click();
 await choose(page,'期望贡献指标','跟进次数（次）');await page.getByRole('textbox',{name:'期望贡献量',exact:true}).fill('1.5');await page.getByRole('button',{name:'测试保存'}).click();await page.getByText('计数指标请填写整数',{exact:true}).waitFor();
 await page.getByRole('textbox',{name:'期望贡献量',exact:true}).fill('0');await page.getByRole('button',{name:'测试保存'}).click();assert.equal(await page.evaluate(()=>payload.expected_contribution.value),'0');
 await page.getByRole('button',{name:'移除',exact:true}).click();await page.getByRole('button',{name:'测试保存'}).click();assert.equal(await page.evaluate(()=>payload.expected_contribution),null);
}));
test('real create page retries capability failure and preserves explicit target selections across periods',()=>fixture(async page=>{
 await page.getByText('指标目录暂不可用',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'下发任务',exact:true}).isDisabled(),true);
 await page.evaluate(()=>{window.directoryFails=false;});await page.getByRole('button',{name:'重试指标目录',exact:true}).click();await page.getByTestId('task-alignment-fields').waitFor();
 await page.getByRole('button',{name:'下发任务',exact:true}).click();await page.getByText('请选择至少一个业绩目标或 KR，或填写其他原因',{exact:true}).waitFor();
 await choose(page,'任务关联业绩目标',/跟进次数 · 成员甲/);await choose(page,'目标周期类型','年目标');
 await page.getByRole('combobox',{name:'任务关联业绩目标',exact:true}).waitFor();await choose(page,'任务关联业绩目标',/年 2026-01-01/);
 const selections=await page.locator('.ant-select-selection-item').allTextContents();assert.ok(selections.some(value=>value.includes('季度 2026-10-01')));assert.ok(selections.some(value=>value.includes('年 2026-01-01')));
 const calls=await page.evaluate(()=>window.calls);assert.equal(calls.some(row=>row.method==='POST'),false);assert.ok(calls.some(row=>row.path.startsWith('/review/targets?period_type=year')));assert.equal(calls.some(row=>row.path.includes('2099-10-14')),false);
},'create',true));
test('V1-only service blocks new creation without category or alignment-free fallback',()=>fixture(async page=>{
 await page.getByText('当前服务尚不支持任务目标关联，请完成配套升级后再下发任务。',{exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'下发任务',exact:true}).isDisabled(),true);assert.equal(await page.getByTestId('task-alignment-fields').count(),0);assert.equal(await page.evaluate(()=>calls.some(row=>row.method==='POST')),false);
},'unsupported'));
test('legacy task upgrades only on confirmed edit; conflict preserves reason and successful save uses V2',()=>fixture(async page=>{
 await page.getByRole('button',{name:'编辑指标与目标',exact:true}).click();await page.getByRole('radio',{name:'其他原因',exact:true}).check();await page.getByRole('textbox',{name:'任务其他原因',exact:true}).fill('保留取消前内容');await page.getByRole('button',{name:/取\s*消/}).click();assert.equal(await page.evaluate(()=>calls.length),0);
 await page.getByRole('button',{name:'编辑指标与目标',exact:true}).click();assert.equal(await page.getByRole('radio',{name:'关联目标 / KR',exact:true}).isChecked(),true);
 await page.getByRole('radio',{name:'其他原因',exact:true}).check();await page.getByRole('textbox',{name:'任务其他原因',exact:true}).fill('开展团队培训');await page.getByRole('button',{name:'保存对齐关系',exact:true}).click();assert.equal(await page.evaluate(()=>calls.length),0);
 await page.evaluate(async()=>{window.writeConflict=true;await window.executeConfirmation();});await page.getByText('任务或指标已更新。当前编辑内容已保留，请核对最新任务后重新编辑。',{exact:true}).waitFor();assert.equal(await page.getByRole('textbox',{name:'任务其他原因',exact:true}).inputValue(),'开展团队培训');
 await page.getByRole('button',{name:'保存对齐关系',exact:true}).click();await page.evaluate(async()=>{window.writeConflict=false;await window.executeConfirmation();});await page.getByRole('button',{name:'编辑指标与目标',exact:true}).waitFor();
 const writes=await page.evaluate(()=>calls);assert.equal(writes.length,2);assert.equal(writes[0].data.version_no,3);assert.equal(writes[1].data.alignment_contract_version,2);assert.deepEqual(writes[1].data.alignment,{target_ids:[],key_result_ids:[],other_reason:'开展团队培训',expected_contribution:null});await page.getByText('开展团队培训',{exact:true}).waitFor();
},'detail'));
test('restricted targets keep their counts and prohibit relationship replacement',()=>fixture(async page=>{
 await page.getByText('另有 1 项业绩目标受查看权限限制。',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'编辑指标与目标',exact:true}).count(),0);assert.equal(await page.evaluate(()=>calls.length),0);
},'hidden'));
test('full task detail keeps expanded alignment and original completion action reachable',()=>fixture(async page=>{
 await page.getByRole('button',{name:'编辑指标与目标',exact:true}).click();await page.getByTestId('task-alignment-fields').waitFor();
 const region=page.locator('.ds-td-main');assert.equal(await region.evaluate(node=>getComputedStyle(node).overflowY),'auto');assert.equal(await region.evaluate(node=>node.scrollHeight>node.clientHeight),true);
 const complete=page.getByRole('button',{name:'确认完成',exact:true});await complete.scrollIntoViewIfNeeded();assert.equal(await complete.isVisible(),true);assert.equal(await region.evaluate(node=>node.scrollTop>0),true);
},'full-detail'));
