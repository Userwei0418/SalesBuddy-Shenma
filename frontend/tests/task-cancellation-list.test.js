const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const filename=path.resolve(__dirname,'../miniprogram/pages/tasks/index.js');
const row=(id='task-a',extra={})=>({id,title:id,status:'pending_execution',can_cancel:true,version_no:3,...extra});
const packet=(items,pending=items.length,total=items.length)=>({items,summary:{total,pending_count:pending,completed_count:0,filtered_total:items.length},has_more:false,next_offset:null});
function harness(handler=async()=>packet([row()])){
 let page;const reads=[],navigations=[],toasts=[];
 const app={ensureLogin:()=>true,globalData:{session:{workspaceId:'local-a',userId:'creator',role:'sales',permissionVersion:1,permissions:{'task.read':true,'task.cancel':true}}}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,getApp:()=>app,require:n=>n.endsWith('apiClient')?{listTaskPage:async q=>{reads.push(q);return handler(q);}}:require(path.resolve(path.dirname(filename),n)),wx:{showToast:v=>toasts.push(v),setNavigationBarTitle(){},navigateTo:v=>navigations.push(v),switchTab(){},setStorageSync(){}}});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=(patch,cb)=>{Object.assign(page.data,patch);if(cb)cb();};page.onLoad();
 return {page,app,reads,navigations,toasts};
}
const click=id=>({currentTarget:{dataset:{id}}});
test('取消快捷入口只按当前后端明确许可与会话权限显示，终态及待验收不出现',async()=>{
 const h=harness(async()=>packet([row(),row('denied',{can_cancel:false}),row('old',{can_cancel:undefined}),row('review',{status:'pending_review'}),row('ended',{status:'cancelled'}),row('done',{status:'completed'})]));
 await h.page.onShow();assert.deepEqual(Array.from(h.page.data.tasks,t=>t.canCancel),[true,false,false,false,false,false]);
 h.page.openCancellation(click('denied'));assert.equal(h.page.data.cancelOpen,false);
 h.page.openCancellation(click('task-a'));assert.equal(h.page.data.cancelOpen,true);assert.equal(h.page.data.cancelTaskId,'task-a');assert.equal(h.page.data.cancelTaskVersion,3);assert.equal(h.navigations.length,0);
 h.page.closeCancellation();h.app.globalData.session.permissions['task.cancel']=false;
 h.page.openCancellation(click('task-a'));assert.equal(h.page.data.cancelOpen,false);
});
test('取消回执后重新读取当前筛选与计数，不把取消当完成且保留全部任务总数',async()=>{
 let cancelled=false;const h=harness(async q=>cancelled?packet(q.tab==='all'?[row('task-a',{status:'cancelled',can_cancel:false,last_event_type:'cancel'})]:[],0,1):packet([row()]));
 await h.page.onShow();h.page.openCancellation(click('task-a'));cancelled=true;
 await h.page.taskCancelled({detail:row('task-a',{status:'cancelled',version_no:4})});
 assert.equal(h.reads.length,2);assert.equal(h.page.data.cancelOpen,false);assert.equal(h.page.data.filteredTasks.length,0);assert.equal(h.page.data.pendingCount,0);assert.equal(h.page.data.completedCount,0);assert.equal(h.page.data.totalCount,1);
 await h.page.selectTab({currentTarget:{dataset:{key:'all'}}});assert.equal(h.page.data.tasks[0].statusLabel,'已取消');assert.equal(h.page.data.tasks[0].canCancel,false);
});
test('取消已提交但列表刷新失败时清除旧卡片并显示错误，不伪造新计数',async()=>{
 let fail=false;const h=harness(async()=>{if(fail)throw Error('刷新失败');return packet([row()]);});
 await h.page.onShow();h.page.openCancellation(click('task-a'));fail=true;
 await h.page.taskCancelled({detail:row('task-a',{status:'cancelled'})});
 assert.equal(h.page.data.filteredTasks.length,0);assert.equal(h.page.data.loadError,'刷新失败');assert.equal(h.page.data.cancelOpen,false);
});
test('关闭或取消弹层不修改任务、不重新加载列表',async()=>{
 const h=harness();await h.page.onShow();h.page.openCancellation(click('task-a'));h.page.closeCancellation();
 assert.equal(h.reads.length,1);assert.equal(h.page.data.tasks[0].sourceStatus,'pending_execution');assert.equal(h.page.data.pendingCount,1);
 await h.page.taskCancelled({detail:row('task-a',{status:'cancelled'})});assert.equal(h.reads.length,1);
});
for(const action of ['hide','identity','permission','deny','task'])test(`旧取消结果不覆盖当前列表: ${action}`,async()=>{
 const h=harness();await h.page.onShow();h.page.openCancellation(click('task-a'));
 if(action==='hide')h.page.onHide();else if(action==='identity')h.app.globalData.session.userId='other';else if(action==='permission')h.app.globalData.session.permissionVersion=2;else if(action==='deny')h.app.globalData.session.permissions['task.cancel']=false;
 await h.page.taskCancelled({detail:row(action==='task'?'other':'task-a',{status:'cancelled'})});assert.equal(h.reads.length,1);
});
test('切换列表筛选会关闭旧取消弹层，快捷按钮不冒泡打开详情',async()=>{
 const h=harness();await h.page.onShow();h.page.openCancellation(click('task-a'));
 await h.page.selectTab({currentTarget:{dataset:{key:'completed'}}});assert.equal(h.page.data.cancelOpen,false);assert.equal(h.page.data.cancelTaskId,'');
 const wxml=fs.readFileSync(filename.replace(/\.js$/,'.wxml'),'utf8');assert.match(wxml,/class="upgrade-cancel-action"[^>]*>[\s\S]*catchtap="openCancellation"/);
});
for(const status of ['cancelled','completed','pending_review','pending_execution'])test(`重新读取发现任务不可取消时收敛列表和计数: ${status}`,async()=>{
 let changed=false;const h=harness(async()=>changed?packet(status==='pending_execution'?[row('task-a',{status,can_cancel:false})]:[],status==='pending_execution'?1:0,1):packet([row()]));
 await h.page.onShow();h.page.openCancellation(click('task-a'));changed=true;
 await h.page.taskCancellationRefreshed({detail:row('task-a',{status,last_event_type:status==='cancelled'?'cancel':undefined,can_cancel:false,version_no:4})});
 assert.equal(h.reads.length,2);assert.equal(h.page.data.cancelOpen,false);assert.equal(h.page.data.tasks.some(t=>t.canCancel),false);
 assert.equal(h.toasts.at(-1).title,status==='cancelled'?'该任务已取消，记录已保留':'任务状态已更新');
});
test('读回接收人拒绝状态不提示已取消',async()=>{
 const h=harness();await h.page.onShow();h.page.openCancellation(click('task-a'));
 await h.page.taskCancellationRefreshed({detail:row('task-a',{status:'cancelled',last_event_type:'reject',can_cancel:false,version_no:4})});
 assert.equal(h.toasts.at(-1).title,'任务状态已更新');assert.equal(h.reads.length,2);
});
for(const action of ['hide','identity','permission','deny','task','close'])test(`迟到读回不刷新当前列表: ${action}`,async()=>{
 const h=harness();await h.page.onShow();h.page.openCancellation(click('task-a'));
 if(action==='hide')h.page.onHide();else if(action==='identity')h.app.globalData.session.userId='other';else if(action==='permission')h.app.globalData.session.permissionVersion=2;else if(action==='deny')h.app.globalData.session.permissions['task.cancel']=false;else if(action==='close')h.page.closeCancellation();
 await h.page.taskCancellationRefreshed({detail:row(action==='task'?'other':'task-a',{status:'cancelled'})});assert.equal(h.reads.length,1);assert.equal(h.toasts.length,0);
});
