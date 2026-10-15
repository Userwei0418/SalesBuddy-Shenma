const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
require('./helpers/business-options');
const teams=[{id:'north',name:'北区'},{id:'south',name:'南区'},{id:'empty',name:'空团队'}];
const members=[{id:'u1',name:'同名',account_code:'NORTH_A',team_ids:['north']},{id:'u2',name:'同名',account_code:'SOUTH_B',team_ids:['south']},{id:'u3',name:'兼职',account_code:'BOTH',team_ids:['north','south']}];
function setup(kind,apiOverrides={}){
 const app={globalData:{role:'manager',session:{role:'manager',workspaceId:'w',userId:'u1',permissionVersion:'one',capabilities:{'team.view':true}},roles:{manager:{name:'经理',scope:'公司'}}}};
 const calls={assets:0,tab:[],list:[]};let definition;
 const file=path.resolve(__dirname,`../miniprogram/pages/${kind}/index.js`);
 const api={listOpportunities:async options=>{calls.list.push(options);return{items:[],summary:{total:0,open_amount:0},has_more:false,team_options:teams,owner_options:members,defaults:{team_id:'north'}};},...apiOverrides};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:value=>definition=value,require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),name)),getApp:()=>app,Date,Set,Map,setTimeout,clearTimeout,wx:{showNavigationBarLoading(){},hideNavigationBarLoading(){},showToast(){},hideLoading(){},hideTabBar:()=>calls.tab.push('hide'),showTabBar:()=>calls.tab.push('show')}});
 const p={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(values,cb){Object.assign(this.data,values);if(cb)cb();}};
 p.setData({role:'manager',canViewTeam:true});
 if(kind==='customers'){p.loadAssets=()=>calls.assets++;p.applyFilters=()=>{};p.applyMapDirectory({items:members,teams,defaults:{team_id:'north'}},false);}
 return{p,app,calls};
}
test('地图选人默认团队仅候选，打开/取消不刷新业务，关闭恢复菜单',()=>{
 const {p,calls}=setup('customers');p.data.selectedTeam='south';
 p.openMemberPicker();assert.equal(p.data.memberPickerDefaultTeamId,'north');assert.equal(p.data.selectedTeam,'south');assert.equal(calls.assets,0);assert.equal(calls.tab.at(-1),'hide');
 p.closeMemberPicker();assert.equal(p.data.selectedTeam,'south');assert.equal(calls.assets,0);assert.equal(calls.tab.at(-1),'show');
});
test('地图确认跨团队同名成员按ID，解除不相容团队，拒绝越权与迟到确认',()=>{
 const {p,app,calls}=setup('customers');p.data.selectedTeam='north';p.openMemberPicker();
 p.confirmMemberPicker({detail:{ids:['u2'],teamId:'south'}});assert.equal(p.data.selectedMember,'u2');assert.equal(p.data.selectedTeam,'all');assert.equal(calls.assets,1);assert.equal(calls.tab.at(-1),'show');
 p.openMemberPicker();p.confirmMemberPicker({detail:{ids:['outside']}});assert.equal(p.data.selectedMember,'u2');assert.equal(calls.assets,1);
 app.globalData.session.permissionVersion='two';p.confirmMemberPicker({detail:{ids:['u1']}});assert.equal(calls.assets,1);
});
test('地图人员仅取目录，业务客户负责人不会被拼入选项，跨团队成员仍可筛',()=>{
 const {p}=setup('customers');p.visibleCustomers=[{owner_user_ref_id:'not-in-directory',owner:'旧负责人'}];p.applyMapDirectory({items:members,teams,defaults:{team_id:'north'}},false);
 assert.equal(p.data.memberPickerMembers.length,3);assert.ok(!p.data.memberPickerMembers.some(row=>row.id==='not-in-directory'));
 p.changeTeam({detail:{value:2}});assert.deepEqual(Array.from(p.data.memberOptions,row=>row.value),['all','u2','u3']);
});
test('FDE地图选人保留多选与空选全部语义，并在离页时关闭弹层',()=>{
 const {p,calls}=setup('customers');p.data.isFde=true;p.data.fdeMapMemberIds=['u1'];let ids;
 p.changeFdeMapMember=e=>ids=e.detail.ids;p.openMemberPicker();p.confirmMemberPicker({detail:{ids:['u1','u2']}});assert.deepEqual(Array.from(ids),['u1','u2']);
 p.openMemberPicker();p.confirmMemberPicker({detail:{ids:[]}});assert.deepEqual(Array.from(ids),[]);
 p.openMemberPicker();p.onHide();assert.equal(p.data.memberPickerOpen,false);assert.equal(calls.tab.at(-1),'show');
});
test('商机按同名人员ID过滤，取消不请求；确认跨团队与全部范围正确',async()=>{
 const {p,calls}=setup('opportunities');await p.loadPage();assert.equal(calls.list.length,1);p.setData({teamIndex:1});p.openOwnerPicker();assert.equal(p.data.ownerPickerDefaultTeamId,'north');p.closeOwnerPicker();assert.equal(calls.list.length,1);
 p.openOwnerPicker();await p.confirmOwnerPicker({detail:{ids:['u2'],teamId:'south'}});assert.equal(calls.list.at(-1).ownerId,'u2');assert.equal(calls.list.at(-1).owner,null);assert.equal(calls.list.at(-1).teamId,'all');
 p.openOwnerPicker();await p.confirmOwnerPicker({detail:{ids:[]}});assert.equal(calls.list.at(-1).ownerId,null);assert.equal(calls.list.at(-1).owner,null);
});
test('商机旧姓名链接不误选同名账号，显式确认ID后移除旧姓名筛选',async()=>{
 const {p,calls}=setup('opportunities');p.onLoad({member:encodeURIComponent('同名')});await p.loadPage();assert.equal(calls.list.at(-1).owner,'同名');assert.equal(p.data.legacyOwnerName,'同名');
 p.openOwnerPicker();await p.confirmOwnerPicker({detail:{ids:['u1']}});assert.equal(calls.list.at(-1).ownerId,'u1');assert.equal(calls.list.at(-1).owner,null);assert.equal(p.data.legacyOwnerName,'');
});
test('商机旧服务缺人员目录明确报错；换身份/关闭后确认不能查询',async()=>{
 const {p,app,calls}=setup('opportunities');p.data.canViewTeam=true;p.pageRequest={};assert.throws(()=>p.acceptPage({items:[],summary:{total:0},has_more:false,team_options:[]},false),/人员目录/);
 await p.loadPage();p.openOwnerPicker();app.globalData.session.userId='other';await p.confirmOwnerPicker({detail:{ids:['u1']}});assert.equal(calls.list.length,1);
 p.onHide();assert.equal(p.data.ownerPickerOpen,false);
});

test('额外获团队查看权限的一线销售刷新不会丢失已选人员',()=>{
 const {p}=setup('customers');p.data.role='sales';p.data.selectedMember='u2';
 p.applyMapDirectory({items:members,teams,defaults:{team_id:'north'}},false);
 assert.equal(p.data.selectedMember,'u2');assert.equal(p.data.memberOptions.find(row=>row.value==='u2').label,'同名');
});
test('地图权限版本变化后，迟到的目录和地图不能回填选项或启动补评',async()=>{
 let directoryResolve,mapResolve;
 const {p,app}=setup('customers',{getDirectoryMembers:()=>new Promise(resolve=>directoryResolve=resolve),getCustomerMap:()=>new Promise(resolve=>mapResolve=resolve)});
 p.consumePendingCustomer=()=>{};let refreshes=0;p.refreshMapScores=()=>refreshes++;
 const request=p.loadData();app.globalData.session.permissionVersion='new';
 directoryResolve({items:members,teams,defaults:{team_id:'north'}});
 mapResolve({items:[{id:'c',name:'旧客户'}],activity_since:'2026-01-01',as_of:'2026-09-27'});
 await request;assert.equal(p.data.memberPickerMembers.length,0);assert.equal(p.visibleCustomers,null);assert.equal(refreshes,0);
});
