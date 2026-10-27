const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=path.resolve(__dirname,'../miniprogram/pages/task-detail/index.js');
const directory=(id='permitted')=>({items:[{id,name:'同名同事',account_code:id.toUpperCase(),team_ids:['north']}],teams:[{id:'north',name:'北区'}],defaults:{team_id:'north'}});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness(overrides={}){
 let page;const reads=[],writes=[],modals=[],session={userId:'sender',workspaceId:'company',role:'sales',permissionVersion:'p1'};
 const task={id:'11111111-1111-1111-1111-111111111111',version_no:3,status:'pending_execution',can_coordinate:true,canCancel:true};
 const api={listTaskRecipients:async()=>{throw Error('must not read company recipient directory');},getTaskReassignmentOptions:async(id,options)=>{reads.push({id,options});return directory();},coordinateTask:async(id,body)=>{writes.push({id,body});return {...task,version_no:4};},...overrides};
 vm.runInNewContext(fs.readFileSync(source,'utf8'),{Page:p=>page=p,getApp:()=>({globalData:{session}}),require:n=>n.includes('apiClient')?api:n.includes('/access')?{can:()=>true,identity:s=>[s.workspaceId,s.userId,s.permissionVersion].join(':')}:require(path.resolve(path.dirname(source),n)),wx:{showModal:m=>modals.push(m),showToast(){}},setTimeout(){}});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=patch=>Object.assign(page.data,patch);page.setData({task,taskId:task.id});
 return {page,session,task,api,reads,writes,modals};
}
const select=(page,ids)=>page.confirmCoordinateMember({detail:{ids,teamId:'north'}});
const reassign=page=>page.coordinate({currentTarget:{dataset:{event:'reassign'}}});
test('任务转交只读取当前任务允许的人员，单选按稳定 ID 提交对应账号',async()=>{
 const h=harness();await h.page.openCoordination();assert.equal(h.reads[0].id,h.task.id);assert.equal(h.page.data.coordinateDefaultTeamId,'north');
 h.page.openCoordinatePicker();select(h.page,['foreign']);assert.equal(h.page.data.coordinateSelectedId,'');assert.match(h.page.data.coordinateError,/当前任务允许/);
 select(h.page,['permitted','foreign']);assert.equal(h.page.data.coordinateSelectedId,'');
 select(h.page,['permitted']);assert.equal(h.page.data.coordinatePickerOpen,false);
 h.page.coordinateNote({detail:{value:'原负责人休假，请继续跟进'}});reassign(h.page);h.modals[0].success({confirm:true});await tick();
 assert.equal(h.writes.length,1);assert.equal(h.writes[0].body.assignee_account_code,'PERMITTED');assert.equal(h.writes[0].body.version_no,3);
});
test('关闭后重新打开，旧目录及旧失败均不能覆盖新候选',async()=>{
 const pending=[];const h=harness({getTaskReassignmentOptions:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))});
 const old=h.page.openCoordination();h.page.closeCoordination();const fresh=h.page.openCoordination();pending[1].resolve(directory('new'));await fresh;
 pending[0].reject(Error('old request failed'));await old;assert.equal(h.page.data.coordinateMembers[0].id,'new');assert.equal(h.page.data.coordinateDirectoryError,'');
 const hidden=h.page.loadCoordinationOptions();h.page.onHide();pending[2].resolve(directory('late'));await hidden;assert.equal(h.page.data.coordinateOpen,false);assert.equal(h.page.data.coordinateMembers.length,0);
});
test('目录缺失元数据不能转交；转交面板不再承载取消写入',async()=>{
 const h=harness({getTaskReassignmentOptions:async()=>({items:[{id:'permitted',name:'同事',account_code:'P'}]})});await h.page.openCoordination();assert.match(h.page.data.coordinateDirectoryError,/团队目录/);
 select(h.page,['permitted']);h.page.coordinateNote({detail:{value:'记录有误'}});reassign(h.page);assert.equal(h.modals.length,0);
 h.page.coordinate({currentTarget:{dataset:{event:'cancel'}}});await tick();assert.equal(h.modals.length,0);assert.equal(h.writes.length,0);
});
test('账号权限变化或任务版本变化时不使用旧候选和确认弹窗',async()=>{
 const h=harness();await h.page.openCoordination();select(h.page,['permitted']);h.page.coordinateNote({detail:{value:'正常交接'}});
 h.session.permissionVersion='p2';reassign(h.page);assert.equal(h.modals.length,0);
 await h.page.loadCoordinationOptions();select(h.page,['permitted']);reassign(h.page);h.page.data.task={...h.task,version_no:4};h.modals[0].success({confirm:true});await tick();assert.equal(h.writes.length,0);
});
test('未确认转交前关闭协调面板，旧确认不能发起写入',async()=>{
 const h=harness();await h.page.openCoordination();select(h.page,['permitted']);h.page.coordinateNote({detail:{value:'正常交接'}});reassign(h.page);
 h.page.closeCoordination();h.modals[0].success({confirm:true});await tick();assert.equal(h.writes.length,0);
});
