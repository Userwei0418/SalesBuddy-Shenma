const test=require('node:test'),assert=require('node:assert/strict');
const access=require('../miniprogram/utils/access');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function session(permissions, grants=[],role='fde') {return {workspaceId:'w',userId:'u',role,permissionVersion:'1',permissions:Object.fromEntries(permissions.map(p=>[p,true])),permissionGrants:grants.map(([permission_code,scope_code])=>({permission_code,scope_code,effect:'allow'}))};}

test('多个功能各用自己的授权范围，团队看板不扩大本人商机范围',()=>{
 const user=session(['dashboard.read','opportunity.read','profile.fde_read'],[['dashboard.read','teams'],['opportunity.read','self'],['profile.fde_read','assigned']]);
 assert.equal(access.flags(user,'bi').canViewTeam,true);
 assert.equal(access.flags(user,'opportunities').canViewTeam,false);
 assert.equal(access.canViewTeam(user,'profile.fde_read'),false);
 user.permissions['dashboard.read']=false;
 assert.equal(access.flags(user,'bi').canViewTeam,false,'账号禁用功能后不能沿用旧授权范围');
});
test('FDE明确获得销售看板权限后提供两个可访问视图，撤销后立即去除',()=>{
 const user=session(['dashboard.read','profile.fde_read']);
 assert.deepEqual(access.presentationOptions(user,'bi').map(x=>x.value),['sales','fde']);
 delete user.permissions['dashboard.read'];
 assert.deepEqual(access.presentationOptions(user,'bi').map(x=>x.value),['fde']);
 assert.deepEqual(access.presentationOptions(session(['dashboard.read'],[],'sales'),'profile'),[]);
});
test('只授予新增商机不能进入编辑，授予编辑也不自动获得新增',()=>{
 for(const role of ['fde','operations','sales']) {
  const user=session(['access.mini_program','opportunity.create'],[],role);
  assert.equal(access.pageAllowed(user,'opportunity-create'),true);
  assert.equal(access.pageAllowed(user,'opportunity-create',{opportunityId:'existing'}),false);
  delete user.permissions['opportunity.create'];user.permissions['opportunity.update']=true;
  assert.equal(access.pageAllowed(user,'opportunity-create'),false);
  assert.equal(access.pageAllowed(user,'opportunity-create',{opportunityId:'existing'}),true);
 }
});
test('演示方案、实绩作废和客户建议各自授权，不捆绑拜访录入或实绩新增',()=>{
 const user=session(['demo_scene.create','actual.void','advice.request','advice.opportunity']);
 const flags=access.flags(user,'customer-assets');
 assert.equal(flags.canCreateDemo,true);assert.equal(flags.canRecordVisit,false);
 assert.equal(flags.canVoidActual,true);assert.equal(flags.canCreateActual,false);
 assert.equal(flags.canOpportunityAdvice,true);assert.equal(flags.canCustomerAdvice,false);
 user.permissions['advice.customer']=true;assert.equal(access.flags(user).canCustomerAdvice,true);
});
function component(user,api={}) {
 let definition;const file=path.resolve(__dirname,'../miniprogram/components/fde-profile/index.js');
 const app={globalData:{session:user}};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Component:d=>definition=d,require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),name)),getApp:()=>app,wx:{},Date,setTimeout,clearTimeout});
 return {app,page:{...definition.methods,data:JSON.parse(JSON.stringify(definition.data)),properties:{profileScope:'self',selectedMemberId:'',selectedTeamId:''},setData(values){Object.assign(this.data,values);}}};
}
test('FDE画像撤销任务与活动读取后清除旧数据并停止发请求',async()=>{
 let reads=0;const {page}=component(session(['profile.fde_read']),{listTaskPage(){reads++;},getFdeActivity(){reads++;}});
 Object.assign(page.data,{activeTab:'tasks',tasks:[{id:'old'}],records:[{id:'old-visit'}],recordsTotal:1});
 page.syncPermissions();await page.loadTasks();await page.loadRecords();
 assert.equal(reads,0);assert.equal(page.data.tasks.length,0);assert.equal(page.data.records.length,0);assert.equal(page.data.recordsTotal,0);
 assert.deepEqual(Array.from(page.data.tabs,x=>x.key),['profile']);assert.equal(page.data.activeTab,'profile');
});
test('目标编辑服从当前功能和服务端具体范围，不再由FDE身份强制锁定团队',()=>{
 const {page,app}=component(session(['target.read','target.submit']));page.properties.profileScope='team';page.data.metricQuarter=3;
 page.buildTargetBoard({},2026,{items:[],editable:true});assert.equal(page.data.canEditOwnTargets,true);
 app.globalData.session.permissions['target.submit']=false;
 page.buildTargetBoard({},2026,{items:[],editable:true});assert.equal(page.data.canEditOwnTargets,false);
 app.globalData.session.permissions['target.submit']=true;
 page.buildTargetBoard({},2026,{items:[],editable:false});assert.equal(page.data.canEditOwnTargets,false);
});
