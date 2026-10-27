const test=require('node:test'),assert=require('node:assert/strict');
const {create,project}=require('../miniprogram/pages/task-detail/taskExecutionPage');
const req=require('../miniprogram/utils/requestIdentity');
const id='11111111-1111-4111-8111-111111111111';
function raw(){return {task_id:id,protocol_version:1,status:'in_progress',version_no:1,requirements_version:1,responsibility_version:1,
 items:[{item_id:'item',definition_version:1,title:'推进',state:'not_started'}],events:[],progress:{requirements_version:1,total:1,completed:0,percent:0,basis:'checklist'},capabilities:{append_feedback:true,set_item_progress:true,pause:true,resume:false},executor:{name:'甲'},reviewer:{name:'乙'}};}
function setup(){const session={userId:'u',workspaceId:'w',capabilities:{task_execution:{available:true,protocol_version:1}}},calls=[];
 const api={getTaskExecution:async()=>raw(),taskExecutionCommand:async(...args)=>{calls.push(args);return {changed:true,execution:raw()};}};
 const p={...create(api,()=>session),taskPageVisible:true,data:{taskId:id,task:{id,execution_mode:'execution_v1'}},setData(x){Object.assign(this.data,x);}};return {p,api,calls,session};}
test('任务详情控制器先加载同包依赖，创建页面时不再延迟解析模块',()=>{
 const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
 const file=path.resolve(__dirname,'../miniprogram/pages/task-detail/taskExecutionPage.js');
 const module={exports:{}};let loading=true;
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{module,require(name){
  if(!loading)throw Error(`module ${name} is not defined during page creation`);
  return require(path.resolve(path.dirname(file),name));
 }},{filename:file});
 loading=false;
 let page;
 assert.doesNotThrow(()=>{page=module.exports.create({},()=>({}));});
 assert.equal(typeof page.loadExecution,'function');
 assert.equal(typeof page.applyLightweight,'function');
 assert.equal(typeof page.submitLightweight,'function');
});
test('缺bootstrap声明只读，不把对象404当不支持；旧16/42已移除',async()=>{
 const {p,api,session}=setup();delete session.capabilities.task_execution;let calls=0;api.getTaskExecution=async()=>{calls++;return raw();};await p.loadExecution();assert.equal(calls,0);assert.match(p.data.executionError,/仅可查看/);
 session.capabilities.task_execution={available:true,protocol_version:1};api.getTaskExecution=async()=>{throw Object.assign(Error('对象不可见'),{statusCode:404});};await p.loadExecution();assert.match(p.data.executionError,/对象不可见/);
 const fs=require('node:fs');assert.doesNotMatch(fs.readFileSync(require('node:path').resolve(__dirname,'../miniprogram/pages/task-detail/index.wxml'),'utf8'),/\? 16|: 42/);
});
test('读取真实投影，反馈带三版本且命中执行幂等白名单',async()=>{
 const {p,calls}=setup();await p.loadExecution();assert.equal(p.data.execution.progress.percent,0);assert.equal(p.data.execution.reviewerLabel,'乙');
 assert.equal(await p.sendExecution('append_feedback',{...p.data.execution.versions,text:'进展'}),true);assert.equal(calls[0][1],'append_feedback');
 assert.equal(req.supports({method:'POST',path:`/tasks/${id}/execution/append_feedback`}),true);
});
test('409重读但不自动重发或提升草稿版本；403关闭动作',async()=>{
 const {p,api,calls}=setup();await p.loadExecution();const body={...p.data.execution.versions,text:'草稿'};
 api.taskExecutionCommand=async()=>{calls.push('write');throw Object.assign(Error('冲突'),{statusCode:409});};
 api.getTaskExecution=async()=>({...raw(),version_no:2});assert.equal(await p.sendExecution('append_feedback',body),false);assert.equal(calls.length,1);assert.equal(body.version_no,1);assert.equal(p.data.execution.versions.version_no,2);
 await p.loadExecution();api.taskExecutionCommand=async()=>{throw Object.assign(Error('撤权'),{statusCode:403});};await p.sendExecution('append_feedback',{...p.data.execution.versions,text:'草稿'});assert.deepEqual(p.data.execution.caps,{});
});
test('身份切换丢弃迟到读取，错误对象与不匹配要求版本拒绝',async()=>{
 const {p,api,session}=setup();let resolve;api.getTaskExecution=()=>new Promise(r=>resolve=r);const pending=p.loadExecution();session.userId='other';resolve(raw());await pending;assert.equal(p.data.execution,undefined);
 assert.throws(()=>project({...raw(),task_id:'other'},id));assert.throws(()=>project({...raw(),requirements_version:2},id));
});
test('创建清单必须人填标题/标准，每任务独立UUID，0项拒绝',()=>{
 const checklist=require('../miniprogram/utils/taskChecklist');assert.throws(()=>checklist.definitions([]));assert.throws(()=>checklist.definitions([{title:'目标'}]));
 const input=[{title:' 提交方案 ',acceptance_criteria:' 提供评审意见 '}],a=checklist.definitions(input),b=checklist.definitions(input);assert.notEqual(a[0].item_id,b[0].item_id);assert.equal(a[0].definition_version,1);assert.equal(a[0].title,'提交方案');
});

test('实际API创建序列化携带协议与人工清单，批量各任务保留独立清单',async()=>{
 const storage=new Map(),requests=[];global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{requests.push(o);o.success({statusCode:200,data:{id,items:[]}});}};
 delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
 const definitions=require('../miniprogram/utils/taskChecklist').definitions;
 const input={executionMode:'execution_v1',description:'人工确认任务',dueAt:Date.now()+60000,priority:'中',checklist:definitions([{title:'提交方案',acceptance_criteria:'含评审结论'}])};
 await api.createTask(input);assert.equal(requests[0].data.execution_protocol_version,1);assert.deepEqual(requests[0].data.checklist,input.checklist);
 const second={...input,checklist:definitions([{title:'提交方案',acceptance_criteria:'含评审结论'}])};await api.createTasks([input,second]);assert.notEqual(requests[1].data.tasks[0].checklist[0].item_id,requests[1].data.tasks[1].checklist[0].item_id);
});

test('复查改期绑定pause与未来日期，撤回提交绑定当前submission和三版本',async()=>{
 for(const intent of ['recheck','withdrawCompletion']){const {p,api,calls}=setup();api.getTaskExecution=async()=>({...raw(),pause:{id:'pause-1'},current_submission:'submission-1',capabilities:{recheck_pause:true,withdraw_completion:true}});await p.loadExecution();p.executionAction({detail:{action:intent}});p.data.executionNote='明确原因';p.data.executionDate='2099-12-01';p.data.executionTime='09:00';await p.submitExecutionDialog();const [,action,body]=calls[0];assert.equal(action,intent==='recheck'?'recheck_pause':'withdraw_completion');assert.equal(body.version_no,1);assert.equal(body.requirements_version,1);assert.equal(body.responsibility_version,1);assert.equal(body.reason,'明确原因');if(intent==='recheck'){assert.equal(body.pause_id,'pause-1');assert.ok(Date.parse(body.recheck_at)>Date.now());}else assert.equal(body.submission_id,'submission-1');}
});
test('缺暂停/提交轮次无弹窗，过期日期/空原因/切身份后旧草稿不发请求',async()=>{
 const {p,api,calls,session}=setup();api.getTaskExecution=async()=>({...raw(),capabilities:{recheck_pause:true,withdraw_completion:true}});await p.loadExecution();p.executionAction({detail:{action:'recheck'}});assert.equal(p.data.executionDialog,undefined);p.executionAction({detail:{action:'withdrawCompletion'}});assert.equal(p.data.executionDialog,undefined);
 api.getTaskExecution=async()=>({...raw(),pause:{id:'p'},current_submission:'s',capabilities:{recheck_pause:true,withdraw_completion:true}});await p.loadExecution();p.executionAction({detail:{action:'recheck'}});p.data.executionNote='原因';p.data.executionDate='2000-01-01';await p.submitExecutionDialog();assert.equal(calls.length,0);
 p.executionAction({detail:{action:'withdrawCompletion'}});await p.submitExecutionDialog();assert.equal(calls.length,0);p.data.executionNote='保留草稿';session.userId='other';await p.loadExecution();await p.submitExecutionDialog();assert.equal(calls.length,0);assert.equal(p.data.executionNote,'保留草稿');
});

test('完成提交贡献可省略或null，严格数字串/说明和字段白名单；不转换精度',async()=>{
 for(const value of [undefined,null,{metric_code:'acv',value:'1234567890123456.12',note:' 说明 '}]){const {p,api,calls}=setup();api.getTaskExecution=async()=>({...raw(),capabilities:{submit_completion:true}});await p.loadExecution();p.executionAction({detail:{action:'complete'}});p.data.executionNote='成果';await p.submitExecutionDialog({detail:{actual_contribution:value}});const body=calls[0][2];if(value===undefined)assert.equal(Object.hasOwn(body,'actual_contribution'),false);else if(value===null)assert.equal(body.actual_contribution,null);else assert.deepEqual(body.actual_contribution,{...value,note:'说明'});}
 for(const bad of [{metric_code:'acv',value:3,note:'说明'},{metric_code:'acv',value:'1e3',note:'说明'},{metric_code:'acv',value:'0.001',note:'说明'},{metric_code:'acv',value:'1',note:''},{metric_code:'acv',value:'1',note:'说明',approved:true}]){const {p,api,calls}=setup();api.getTaskExecution=async()=>({...raw(),capabilities:{submit_completion:true}});await p.loadExecution();p.executionAction({detail:{action:'complete'}});p.data.executionNote='成果';await p.submitExecutionDialog({detail:{actual_contribution:bad}});assert.equal(calls.length,0);assert.ok(p.data.executionDialogError);}
});
test('已建v1在新建开关关闭时仍读取执行协议，不能降级旧任务',async()=>{const {p,session}=setup();session.capabilities.task_execution.creation_enabled=false;await p.loadExecution();assert.equal(p.data.execution.raw.task_id,id);assert.equal(p.data.executionError,'');});
