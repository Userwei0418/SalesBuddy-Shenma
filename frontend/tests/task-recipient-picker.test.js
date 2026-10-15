const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const file=path.resolve(__dirname,'../miniprogram/pages/management-task-create/index.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
// Existing scenario fixtures focus on task behavior; supply the directory metadata contract.
const directoryPage=page=>({...page,teams:[{id:'north',name:'北区'},{id:'south',name:'南区'}],defaults:{team_id:'south'},
 items:page.items.map(item=>({...item,account_code:item.account_code||item.id,team_ids:item.team==='北区'?['north']:['south']}))});

function harness(overrides={}){
 let page;const modals=[],sent=[],timers=[],receipts=[],toasts=[];
 const members=[{id:'u1',name:'同名',account_code:'SALES-A',team:'南区',role:'sales'},
 {id:'u2',name:'同名',account_code:'FDE-B',team:'北区',role:'fde'}];
 const app={ensureLogin:()=>true,globalData:{session:{userId:'u1',workspaceId:'w1',role:'sales'},roles:{sales:{name:'销售',scope:'本人'}}}};
 const api={listTaskRecipients:async()=>({items:members}),createTask:async input=>{sent.push(input);return{id:'t1'};},
 createTasks:async inputs=>{sent.push(inputs);return{items:inputs.map((_,i)=>({id:'t'+i}))};},...overrides};
 const readRecipients=api.listTaskRecipients;api.listTaskRecipients=async options=>directoryPage(await readRecipients(options));
 const wx={showModal:value=>modals.push(value),showToast:value=>toasts.push(value),getStorageSync(){},removeStorageSync(){},
 setStorageSync:(key,value)=>receipts.push(value),navigateBack(){},vibrateShort(){}};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:value=>page=value,getApp:()=>app,require:name=>name.includes('apiClient')?api:require(path.resolve(path.dirname(file),name)),wx,setTimeout:callback=>timers.push(callback),clearTimeout(){},setInterval:()=>1,clearInterval(){}});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=(patch,callback)=>{Object.assign(page.data,patch);if(callback)callback();};
 page.onLoad({});page.getSelectedDueAt=()=>Date.now()+86400000;page.inputDescription({detail:{value:'请在截止时间前核对客户资料并反馈'}});
const pick=id=>page.toggleRecipient({currentTarget:{dataset:{id}}});
 return{page,app,api,wx,modals,sent,timers,receipts,toasts,pick};
}
test('任务创建和负责人目录使用服务端岗位名称，保留原岗位代码',async()=>{
 const h=harness({listTaskRecipients:async()=>({items:[
  {id:'u1',name:'客户经理样例',account_code:'SALE',role:'sales',role_name:'客户经理'},
  {id:'u2',name:'产品销售样例',account_code:'PRODUCT',role:'sales',role_name:'产品销售'},
 ]})});
 h.app.globalData.session.roleName='客户经理';h.page.onLoad({});await tick();
 assert.equal(h.page.data.roleName,'客户经理');
 assert.deepEqual(Array.from(h.page.data.members,item=>item.roleLabel),['客户经理','产品销售']);
 assert.ok(h.page.data.members.every(item=>item.role==='sales'));
});
test('恢复草稿或建议填入的超长任务描述不发送，保留内容供修改，长度与接口字符口径一致',async()=>{
 const h=harness();await tick();h.pick('u1');
 h.page.data.description='字'.repeat(501);h.page.submitTask();
 assert.equal(h.modals.length,0);assert.equal(h.sent.length,0);assert.equal(h.page.data.description.length,501);assert.match(h.toasts.at(-1).title,/500/);
 h.page.data.description='😀😀😀';h.page.submitTask();assert.equal(h.modals.length,0);
 h.page.data.description='字'.repeat(500);h.page.submitTask();assert.equal(h.modals.length,1);
});
test('不自动选择，搜索保留勾选并能用账号区分同名同事',async()=>{
 const h=harness();await tick();h.page.openRecipients();h.page.closeRecipients();h.page.submitTask();assert.equal(h.modals.length,0);
 h.pick('u1');h.page.searchRecipients({detail:{value:'fde-b'}});assert.equal(h.page.data.recipientRows.length,1);assert.equal(h.page.data.recipientRows[0].id,'u2');
 h.pick('u2');assert.equal(h.page.data.selectedMembers.length,2);
 h.page.searchRecipients({detail:{value:'南区'}});assert.equal(h.page.data.recipientRows[0].selected,true);
 h.page.searchRecipients({detail:{value:'不存在'}});assert.equal(h.page.data.recipientRows.length,0);assert.equal(h.page.data.selectedMembers.length,2);
 h.pick('u1');h.page.closeRecipients();h.page.openRecipients();assert.equal(h.page.data.selectedMembers[0].id,'u2');assert.equal(h.sent.length,0);
});
test('取消确认不提交，双击只提交整批一次，每个人均显式指定',async()=>{
 const h=harness();await tick();h.pick('u2');h.pick('u1');h.page.submitTask();h.page.submitTask();assert.equal(h.modals.length,1);
 h.modals[0].success({confirm:false});assert.equal(h.sent.length,0);h.page.submitTask();h.modals[1].success({confirm:true});h.modals[1].success({confirm:true});await tick();
 assert.equal(h.sent.length,1);assert.deepEqual(Array.from(h.sent[0],i=>i.assigneeAccount),['SALES-A','FDE-B']);
 assert.ok(h.sent[0].every(i=>i.targetPosition===null));assert.equal(h.receipts[0].ids.length,2);
 h.page.submitTask();assert.equal(h.sent.length,1);
});
test('响应丢失后重试原批次，未确认前不允许变更负责人和正文',async()=>{
 let n=0;const batches=[];const h=harness({createTasks:async input=>{batches.push(input);if(!n++)throw Error('timeout');return{items:[{id:'a'},{id:'b'}]};}});await tick();h.pick('u1');h.pick('u2');h.page.submitTask();h.modals[0].success({confirm:true});await tick();
 assert.equal(h.page.data.submissionPending,true);h.pick('u1');h.page.inputDescription({detail:{value:'修改后的其他描述'}});assert.equal(h.page.data.selectedMembers.length,2);assert.notEqual(h.page.data.description,'修改后的其他描述');
 h.page.submitTask();await tick();assert.equal(batches[0],batches[1]);assert.equal(h.modals.length,1);assert.equal(h.receipts.length,1);
});
test('确定拒绝允许重新选择，已成功后本地回执写入失败不会重发',async()=>{
 let n=0;const h=harness({createTask:async()=>{if(!n++)throw Object.assign(Error('无权派发'),{statusCode:403});return{id:'ok'};}});await tick();h.pick('u1');h.page.submitTask();h.modals[0].success({confirm:true});await tick();
 assert.equal(h.page.data.submissionPending,false);h.wx.setStorageSync=()=>{throw Error('disk full');};h.page.submitTask();h.modals[1].success({confirm:true});await tick();h.page.submitTask();assert.equal(n,2);assert.equal(h.page._submitted,true);
});
test('确认弹窗和迟到响应均不能越过账号切换',async()=>{
 const h=harness();await tick();h.pick('u1');h.page.submitTask();h.app.globalData.session.userId='other';h.modals[0].success({confirm:true});await tick();assert.equal(h.sent.length,0);
 let resolve;const late=harness({createTask:()=>new Promise(r=>resolve=r)});await tick();late.pick('u1');late.page.submitTask();late.modals[0].success({confirm:true});late.app.globalData.session.userId='other';resolve({id:'t'});await tick();assert.equal(late.receipts.length,0);assert.equal(late.timers.length,0);
});
test('AI多选通过同一次建议采纳提交，保留建议版本及每位负责人',async()=>{
 let payload;const h=harness({decideSuggestion:async(id,body)=>{payload={id,body};return{tasks:[{id:'a'},{id:'b'}]};}});await tick();h.pick('u1');h.pick('u2');h.page.setData({adviceSource:{id:'s',version:3},taskType:'daily'});
 h.page.submitTask();h.modals[0].success({confirm:true});await tick();assert.equal(payload.id,'s');assert.equal(payload.body.version_no,3);assert.equal(payload.body.tasks.length,2);assert.ok(payload.body.tasks.every(t=>t.association_kind==='daily'&&t.customer_id===null));assert.equal(h.sent.length,0);
});
test('已成功后的延迟返回在页面退出或账号切换后不再导航',async()=>{
 const h=harness();let navigated=0;h.wx.navigateBack=()=>navigated++;await tick();h.pick('u1');h.page.submitTask();h.modals[0].success({confirm:true});await tick();h.app.globalData.session.userId='other';h.timers[0]();assert.equal(navigated,0);
});

test('已采纳建议可分别打开每个人的待办，拒绝不在该建议中的目标',()=>{
 let component;const urls=[];
 const source=path.resolve(__dirname,'../miniprogram/components/advice-actions/index.js');
 vm.runInNewContext(fs.readFileSync(source,'utf8'),{Component:value=>component=value,require:()=>({}),wx:{navigateTo:value=>urls.push(value.url)}});
 const instance={...component.methods,data:{suggestion:{task_id:'old',tasks:[{id:'a'},{id:'b'}]}}};
 instance.openTask({currentTarget:{dataset:{id:'b'}}});instance.openTask({currentTarget:{dataset:{id:'foreign'}}});
 assert.deepEqual(urls,['/pages/task-detail/index?id=b']);
 instance.data.suggestion={task_id:'old'};instance.openTask();assert.equal(urls[1],'/pages/task-detail/index?id=old');
});

test('共享选人确认保留跨团队人员，重读目录后稳定 ID 不丢失',async()=>{
 const h=harness();await tick();assert.equal(h.page.data.recipientDefaultTeamId,'south');
 assert.deepEqual(Array.from(h.page.data.recipientTeams,t=>t.id),['north','south']);
 h.page.openRecipients();h.page.confirmRecipients({detail:{ids:['u1','u2'],teamId:'north'}});
 assert.deepEqual(Array.from(h.page.data.selectedRecipientIds),['u1','u2']);assert.equal(h.page.data.recipientOpen,false);
 await h.page.retryRecipients();assert.deepEqual(Array.from(h.page.data.selectedRecipientIds),['u1','u2']);
 h.page.submitTask();h.modals[0].success({confirm:true});await tick();assert.equal(h.sent[0].length,2);
});
test('目录缺少团队元数据时明确失败、保留已选展示并禁止提交',async()=>{
 const h=harness();await tick();h.pick('u1');
 h.api.listTaskRecipients=async()=>({items:[{id:'u1',name:'同名',account_code:'SALES-A',team:'南区'}]});
 await h.page.retryRecipients();assert.match(h.page.data.recipientError,/团队目录/);
 assert.equal(h.page.data.selectedMembers[0].id,'u1');h.page.submitTask();assert.equal(h.modals.length,0);
 h.page.confirmRecipients({detail:{ids:['u1']}});assert.equal(h.page.data.recipientOpen,false);
});
test('创建负责人确认拒绝目录外 ID、超过上限和权限更新后的旧目录',async()=>{
 const h=harness();await tick();h.page.openRecipients();h.page.confirmRecipients({detail:{ids:['foreign']}});
 assert.equal(h.page.data.selectedMembers.length,0);assert.match(h.page.data.recipientError,/已变化/);
 await h.page.retryRecipients();h.page.confirmRecipients({detail:{ids:Array.from({length:101},(_,i)=>String(i))}});assert.equal(h.page.data.selectedMembers.length,0);
 await h.page.retryRecipients();h.app.globalData.session.permissionVersion='changed';h.page.confirmRecipients({detail:{ids:['u1']}});assert.equal(h.page.data.selectedMembers.length,0);
});
test('权限版本变化使正在读取的任务人员目录失效',async()=>{
 let finish;const h=harness({listTaskRecipients:()=>new Promise(resolve=>finish=resolve)});
 h.app.globalData.session.permissionVersion='new';finish({items:[{id:'u1',name:'旧授权人员',account_code:'OLD'}]});await tick();assert.equal(h.page.data.members.length,0);
});

test('隐藏确认不发送任务，重新显示后可重新确认',async()=>{
 const h=harness();await tick();h.pick('u1');h.page.submitTask();h.page.onHide();h.modals[0].success({confirm:true});await tick();
 assert.equal(h.sent.length,0);assert.equal(h.page._confirming,false);h.page.onShow();h.page.submitTask();h.modals[1].success({confirm:true});await tick();assert.equal(h.sent.length,1);
});
test('权限版本或同账号重新登录使旧确认失效，新目录加载后可继续创建',async()=>{
 for(const key of ['permissionVersion','loginAt']){
  const h=harness();await tick();h.pick('u1');h.page.submitTask();h.app.globalData.session[key]='changed';h.modals[0].success({confirm:true});await tick();assert.equal(h.sent.length,0);
  h.page.onShow();await tick();assert.equal(h.page.data.submitting,false);assert.equal(h.page.data.submissionPending,false);assert.equal(h.page.data.selectedMembers.length,0);
  h.page.inputDescription({detail:{value:'重新填写当前账号的任务描述'}});h.pick('u1');h.page.submitTask();h.modals[1].success({confirm:true});await tick();assert.equal(h.sent.length,1);
 }
});
test('已提交任务切后台保留锁，隐藏成功不导航或提示，回到本页只返回一次',async()=>{
 let resolve,posts=0,navigation=0;const h=harness({createTask:()=>{posts++;return new Promise(r=>resolve=r);}});h.wx.navigateBack=()=>navigation++;
 await tick();h.pick('u1');h.page.submitTask();h.modals[0].success({confirm:true});h.page.onHide();h.page.onShow();h.page.submitTask();assert.equal(posts,1);assert.equal(h.page.data.submitting,true);
 h.page.onHide();resolve({id:'created'});await tick();assert.equal(navigation,0);assert.equal(h.toasts.length,0);h.timers[0]();assert.equal(navigation,0);assert.equal(h.page.data.submitting,false);
 h.page.onShow();h.timers[0]();h.page.onShow();assert.equal(navigation,1);h.page.submitTask();assert.equal(posts,1);
});
test('旧账号失败回执不能解锁新账号正在发送的待办',async()=>{
 const pending=[];const h=harness({createTask:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))});await tick();h.pick('u1');h.page.submitTask();h.modals[0].success({confirm:true});
 h.app.globalData.session={...h.app.globalData.session,userId:'other',loginAt:'new-login'};h.page.onShow();await tick();h.page.inputDescription({detail:{value:'新账号当前要发送的任务内容'}});h.pick('u2');h.page.submitTask();h.modals[1].success({confirm:true});assert.equal(pending.length,2);
 pending[0].reject(Error('旧请求网络中断'));await tick();assert.equal(h.page.data.submitting,true);assert.equal(h.page.data.submissionPending,true);assert.equal(h.toasts.length,0);
 pending[1].resolve({id:'new-account-task'});await tick();assert.equal(h.page.data.submitting,false);assert.equal(h.receipts[0].ownerUserId,'other');
});
test('确认期间表单变动不会发送旧快照，必须重新确认',async()=>{
 const h=harness();await tick();h.pick('u1');h.page.submitTask();h.page.inputDescription({detail:{value:'在确认期间已经变更后的任务内容'}});h.modals[0].success({confirm:true});await tick();assert.equal(h.sent.length,0);
 h.page.submitTask();h.modals[1].success({confirm:true});await tick();assert.equal(h.sent.length,1);assert.equal(h.sent[0].description,'在确认期间已经变更后的任务内容');
});
test('旧建议失败不能清除新身份的加载状态',async()=>{
 const reads=[];const h=harness({getBusinessAdvice:()=>new Promise((resolve,reject)=>reads.push({resolve,reject}))});await tick();h.page.onLoad({adviceId:'a',suggestionId:'s'});
 h.app.globalData.session.loginAt='new-login';h.page.onShow();assert.equal(reads.length,2);reads[0].reject(Error('旧建议读取失败'));await tick();assert.equal(h.page.data.adviceLoading,true);assert.equal(h.page.data.adviceError,'');
 reads[1].resolve({status:'succeeded',subject_kind:'visit',suggestions:[{id:'s',decision:'pending',version_no:1,action:'新身份建议的任务内容'}]});await tick();assert.equal(h.page.data.adviceLoading,false);assert.equal(h.page.data.description,'新身份建议的任务内容');
});
test('旧身份录音转写不能覆盖新身份表单或结束新转写状态',async()=>{
 const reads=[];const h=harness({transcribeAudio:()=>new Promise((resolve,reject)=>reads.push({resolve,reject}))});await tick();const callbacks={};
 h.wx.getRecorderManager=()=>({onStart:fn=>callbacks.start=fn,onStop:fn=>callbacks.stop=fn,onError:fn=>callbacks.error=fn,start(){},stop(){}});h.wx.getSetting=options=>options.success({authSetting:{'scope.record':true}});
 h.page.toggleTaskVoice();callbacks.start();callbacks.stop({duration:1000,tempFilePath:'old.mp3'});assert.equal(reads.length,1);
 h.app.globalData.session.loginAt='new-login';h.page.onShow();await tick();h.page.toggleTaskVoice();callbacks.start();callbacks.stop({duration:1000,tempFilePath:'new.mp3'});
 reads[0].reject(Error('旧转写失败'));await tick();assert.equal(h.page.data.isParsing,true);assert.equal(h.toasts.length,0);
 reads[1].resolve({text:'新身份的录音内容'});await tick();assert.equal(h.page.data.isParsing,false);assert.equal(h.page.data.description,'新身份的录音内容');
});
test('账号切换等待旧录音停止，旧 onStop 不启动新身份转写',async()=>{
 let starts=0,stops=0,transcribes=0;const h=harness({transcribeAudio:()=>{transcribes++;return Promise.resolve({text:'不应填入'});}});await tick();const callbacks={};
 h.wx.getRecorderManager=()=>({onStart:fn=>callbacks.start=fn,onStop:fn=>callbacks.stop=fn,onError:fn=>callbacks.error=fn,start(){starts++;},stop(){stops++;}});h.wx.getSetting=options=>options.success({authSetting:{'scope.record':true}});
 h.page.toggleTaskVoice();callbacks.start();h.app.globalData.session.loginAt='new-login';h.page.onShow();await tick();assert.equal(stops,1);
 h.page.toggleTaskVoice();assert.equal(starts,1);callbacks.stop({duration:1000,tempFilePath:'old.mp3'});await tick();assert.equal(transcribes,0);assert.equal(h.page.data.description,'');
 h.page.toggleTaskVoice();assert.equal(starts,2);callbacks.start();callbacks.stop({duration:1000,tempFilePath:'new.mp3'});await tick();assert.equal(transcribes,1);
});
test('隐藏页晚到的录音授权不能弹窗或开始录音，返回后可以重试',async()=>{
 for(const permission of [true,false]){
  const h=harness();await tick();let settings,starts=0;h.wx.getSetting=options=>settings=options;
  h.wx.getRecorderManager=()=>({onStart(){},onStop(){},onError(){},start(){starts++;}});
  h.page.toggleTaskVoice();h.page.onHide();settings.success({authSetting:{'scope.record':permission}});assert.equal(starts,0);assert.equal(h.modals.length,0);assert.equal(h.page.data.isStarting,false);
  h.page.onShow();h.page.toggleTaskVoice();settings.success({authSetting:{'scope.record':true}});assert.equal(starts,1);
 }
});
test('已成功任务在卸载后不会被 onShow 或残留定时器返回其他页面',async()=>{
 const h=harness();let navigation=0;h.wx.navigateBack=()=>navigation++;await tick();h.pick('u1');h.page.submitTask();h.modals[0].success({confirm:true});await tick();
 h.page.onUnload();h.page.onShow();h.timers[0]();assert.equal(navigation,0);
});
