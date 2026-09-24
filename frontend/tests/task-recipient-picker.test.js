const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const file=path.resolve(__dirname,'../miniprogram/pages/management-task-create/index.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(overrides={}){
 let page;const modals=[],sent=[],timers=[],receipts=[],toasts=[];
 const members=[{id:'u1',name:'同名',account_code:'SALES-A',team:'南区',role:'sales'},
 {id:'u2',name:'同名',account_code:'FDE-B',team:'北区',role:'fde'}];
 const app={ensureLogin:()=>true,globalData:{session:{userId:'u1',workspaceId:'w1',role:'sales'},roles:{sales:{name:'销售',scope:'本人'}}}};
 const api={listTaskRecipients:async()=>({items:members}),createTask:async input=>{sent.push(input);return{id:'t1'};},
 createTasks:async inputs=>{sent.push(inputs);return{items:inputs.map((_,i)=>({id:'t'+i}))};},...overrides};
 const wx={showModal:value=>modals.push(value),showToast:value=>toasts.push(value),getStorageSync(){},removeStorageSync(){},
 setStorageSync:(key,value)=>receipts.push(value),navigateBack(){},vibrateShort(){}};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:value=>page=value,getApp:()=>app,require:name=>name.includes('apiClient')?api:require(path.resolve(path.dirname(file),name)),wx,setTimeout:callback=>timers.push(callback),clearTimeout(){}});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=(patch,callback)=>{Object.assign(page.data,patch);if(callback)callback();};
 page.onLoad({});page.getSelectedDueAt=()=>Date.now()+86400000;page.inputDescription({detail:{value:'请在截止时间前核对客户资料并反馈'}});
 const pick=id=>page.toggleRecipient({currentTarget:{dataset:{id}}});
 return{page,app,api,wx,modals,sent,timers,receipts,toasts,pick};
}
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
