const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../miniprogram/components/task-execution-panel');
function panel(){let d;vm.runInNewContext(fs.readFileSync(path.join(root,'index.js'),'utf8'),{Component:x=>d=x,require:n=>require(path.resolve(root,n))});
 const events=[],c={...d.methods,data:structuredClone(d.data),properties:{enabled:true,contextKey:'company:user:task',taskVersions:{version_no:1,requirements_version:1,responsibility_version:1},allowFeedback:true,checklist:[]},setData(x){Object.assign(this.data,x);},triggerEvent(name,detail){events.push({name,detail});}};
 d.observers.contextKey.call(c,c.properties.contextKey);
 d.observers.taskVersions.call(c,c.properties.taskVersions);
 return {c,d,events};}
const input=(c,value)=>c.inputFeedback({detail:{value}});
function poll(c,d,{versions=c.properties.taskVersions,order=['contextKey','taskVersions']}={}){
 Object.assign(c.properties,{contextKey:c.properties.contextKey,taskVersions:{...versions}});
 for(const key of order)d.observers[key].call(c,c.properties[key]);
}
test('同上下文轮询重复赋值不清反馈草稿，随后仍可提交原文',()=>{
 const {c,d,events}=panel();input(c,'写到一半的过程反馈');c.toggleFeedback();
 for(let i=0;i<3;i++)poll(c,d);
 assert.equal(c.data.note,'写到一半的过程反馈');assert.equal(c.data.showAllFeedback,true);
 assert.equal(c.data.stale,false);c.submitFeedback();assert.equal(events.length,1);
 assert.equal(events[0].detail.command.text,'写到一半的过程反馈');
});
test('同上下文轮询保留在途提交锁及回执关联，不重复提交，失败保留原稿',()=>{
 const {c,d,events}=panel();input(c,'正在保存的反馈');c.submitFeedback();const receipt=events[0].detail.context;
 poll(c,d);assert.equal(c.data.pending,true);assert.equal(c._pendingContext,receipt);
 c.submitFeedback();assert.equal(events.length,1);
 c.settleFeedback({context:receipt,success:false,error:'网络失败'});poll(c,d);
 assert.equal(c.data.pending,false);assert.equal(c.data.note,'正在保存的反馈');assert.equal(c.data.error,'网络失败');
 c.submitFeedback();assert.equal(events.length,2);poll(c,d);
 c.settleFeedback({context:receipt,success:true});assert.equal(c.data.pending,true);assert.equal(c.data.note,'正在保存的反馈');
 c.settleFeedback({context:events[1].detail.context,success:true});assert.equal(c.data.pending,false);assert.equal(c.data.note,'');
});
test('同身份轮询遇版本变化保留草稿并标记过期，不受两个observer执行顺序影响',()=>{
 for(const order of [['contextKey','taskVersions'],['taskVersions','contextKey']]){
  for(const changed of ['version_no','requirements_version','responsibility_version']){
   const {c,d,events}=panel();input(c,'并发变更前的反馈');c.submitFeedback();const receipt=events[0].detail.context;
   poll(c,d,{versions:{...c.properties.taskVersions,[changed]:2},order});
   assert.equal(c.data.note,'并发变更前的反馈',changed);assert.equal(c.data.stale,true,changed);assert.equal(c.data.pending,false);
   poll(c,d,{order});c.settleFeedback({context:receipt,success:true});c.submitFeedback();assert.equal(events.length,1);assert.equal(c.data.note,'并发变更前的反馈');
   c.confirmLatest();c.submitFeedback();assert.equal(events.length,2);assert.equal(events[1].detail.command[changed],2);
  }
 }
});
test('旧后端未启用/权限缺失/离线不产生写意图',()=>{
 for(const patch of [{enabled:false},{allowFeedback:false},{offline:true},{contextKey:''}]){const {c,events}=panel();Object.assign(c.properties,patch);input(c,'进展');c.submitFeedback();assert.equal(events.length,0);}
 const template=fs.readFileSync(path.join(root,'index.wxml'),'utf8');assert.match(template,/wx:if="{{enabled}}"/);
});
test('反馈携带三版本、重复点击抑制、失败保留草稿、成功才清除',()=>{
 const {c,events}=panel();input(c,'  完成方案  ');c.submitFeedback();c.submitFeedback();assert.equal(events.length,1);
 const e=events[0];assert.equal(e.name,'feedbackintent');assert.equal(e.detail.command.text,'完成方案');assert.equal(e.detail.command.responsibility_version,1);
 c.settleFeedback({context:e.detail.context,success:false,error:'网络失败'});assert.equal(c.data.note,'  完成方案  ');
 c.submitFeedback();assert.equal(events.length,2);c.settleFeedback({context:e.detail.context,success:true});assert.equal(c.data.pending,true);assert.equal(c.data.note,'  完成方案  ');c.settleFeedback({context:events[1].detail.context,success:true});assert.equal(c.data.note,'');
});
test('要求版本变化保留草稿，必须明确核对，旧回执不清除新草稿',()=>{
 const {c,d,events}=panel();input(c,'反馈草稿');c.submitFeedback();const old=events[0].detail.context;
 c.properties.taskVersions={version_no:2,requirements_version:2,responsibility_version:1};d.observers.taskVersions.call(c);
 assert.equal(c.data.pending,false);assert.equal(c.data.stale,true);c.submitFeedback();assert.equal(events.length,1);
 c.settleFeedback({context:old,success:true});assert.equal(c.data.note,'反馈草稿');c.confirmLatest();c.submitFeedback();assert.equal(events.length,2);assert.equal(events[1].detail.command.requirements_version,2);
});
test('切用户/任务清空组件草稿和在途上下文，不泄露另一任务文字',()=>{
 for(const key of ['other-company:user:task','company:other-user:task','company:user:other-task','']){
  const {c,d,events}=panel();input(c,'任务甲');c.submitFeedback();c.toggleFeedback();const old=events[0].detail.context;
  c.properties.contextKey=key;d.observers.contextKey.call(c,key);c.settleFeedback({context:old,success:true});
  assert.equal(c.data.note,'');assert.equal(c.data.pending,false);assert.equal(c.data.stale,false);assert.equal(c.data.error,'');assert.equal(c.data.showAllFeedback,false);
  assert.equal(c._draftContext,null);assert.equal(c._pendingContext,null);
  if(key){input(c,'新上下文反馈');poll(c,d);c.settleFeedback({context:old,success:true});assert.equal(c.data.note,'新上下文反馈');}
 }
});
test('推进/暂停/恢复只发受控意图，不改百分比、任务状态或截止日期',()=>{
 const {c,events}=panel();Object.assign(c.properties,{allowProgress:true,allowPause:true,allowResume:true,checklist:[{id:'i',editable:true}],pauseView:{active:true}});
 const action=(name,id)=>c.requestAction({currentTarget:{dataset:{action:name,id}}});action('progress','missing');action('pause');assert.equal(events.length,0);
 action('progress','i');action('resume');assert.equal(events.length,2);assert.equal(events[1].detail.action,'resume');assert.equal(c.properties.pauseView.active,true);
});
test('四类进度映射进入组件，100%无完成按钮；反馈列表可展开',()=>{
 const {c,d}=panel();for(const value of [{basis:'missing_checklist',total:0,completed:0,percent:null},{basis:'requires_revalidation',total:2,completed:1,percent:null},{basis:'checklist',total:2,completed:0,percent:0},{basis:'checklist',total:2,completed:2,percent:100}]){
 d.observers.progressValue.call(c,{requirements_version:1,...value});assert.equal(c.data.progressView.percent,value.percent);}
 assert.match(c.data.progressView.hint,/提交与验收/);c.toggleFeedback();assert.equal(c.data.showAllFeedback,true);
 const json=JSON.parse(fs.readFileSync(path.join(root,'index.json'),'utf8'));assert.equal(json.component,true);
 const wxml=fs.readFileSync(path.join(root,'index.wxml'),'utf8');for(const binding of wxml.matchAll(/bind(?:tap|:change)="(\w+)"/g))assert.equal(typeof c[binding[1]],'function',binding[1]);
});
