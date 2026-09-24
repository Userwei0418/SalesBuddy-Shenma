const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const file=path.resolve(__dirname,'../miniprogram/pages/task-detail/index.js');
function setup(userId='owner', status='pending_execution',creator='creator'){
 let page;const calls=[],toasts=[],modals=[],stored=[];
 const task={id:'11111111-1111-1111-1111-111111111111',status,creator_user_ref_id:creator,creator_name:'发起人',assignees:[{user_id:'owner',name:'接收人',responsibility:'owner'}],version_no:3,completion_note:'已交付',events:[]};
 const api={getTask:async()=>task,completeTask:async(...args)=>{calls.push(args);return {...task,status:creator==='owner'?'completed':'pending_review'};},respondTask:async(...args)=>{calls.push(args);return {...task,status:args[1]==='approve_completion'?'completed':'in_progress'};}};
 const session={userId,role:'sales'};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:p=>page=p,require:n=>n.includes('apiClient')?api:n.includes('/access')?{can:()=>true,identity:s=>s.userId}:require(path.resolve(path.dirname(file),n)),getApp:()=>({globalData:{session}}),setTimeout(){},wx:{showModal:m=>modals.push(m),showToast:m=>toasts.push(m),setStorageSync:(...a)=>stored.push(a),vibrateShort(){}}});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=v=>Object.assign(page.data,v);page.setData({taskId:task.id});page.loadTask();
 return {page,calls,toasts,modals,stored};
}
const tick=()=>new Promise(r=>setImmediate(r));
test('接收方说明必填，提交后待验收不能提前移除待办',async()=>{
 const s=setup();await tick();s.page.markCompleted();assert.equal(s.modals.length,0);
 s.page.setData({completionNote:'完成方案交付'});s.page.markCompleted();s.modals[0].success({confirm:true});await tick();
 assert.equal(s.page.data.task.status,'pending_review');assert.equal(s.page.data.task.canReview,false);assert.equal(s.page.data.task.canComplete,false);assert.equal(s.stored.length,0);
});
test('原发起人可验收，驳回必填且请求携带版本',async()=>{
 const s=setup('creator','pending_review');await tick();assert.equal(s.page.data.task.canReview,true);
 s.page.reviewCompletion({currentTarget:{dataset:{event:'reject_completion'}}});assert.equal(s.modals.length,0);
 s.page.setData({reviewNote:'补齐验证'});s.page.reviewCompletion({currentTarget:{dataset:{event:'reject_completion'}}});s.modals[0].success({confirm:true});await tick();
 assert.equal(s.calls[0][1],'reject_completion');assert.equal(s.calls[0][3],3);assert.equal(s.page.data.task.status,'in_progress');
});
test('旁观者不能验收；自建自领显示直接完成',async()=>{
 const other=setup('other','pending_review');await tick();assert.equal(other.page.data.task.canReview,false);
 const own=setup('owner','pending_execution','owner');await tick();assert.equal(own.page.data.task.selfAssigned,true);
 own.page.setData({completionNote:'自建已完成'});own.page.markCompleted();own.modals[0].success({confirm:true});await tick();assert.equal(own.page.data.task.status,'completed');assert.equal(own.stored.length,1);
});
