const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = path.resolve(__dirname, '../miniprogram');
const tick = () => new Promise(resolve => setImmediate(resolve));
const lead = {role:'fde_lead',userId:'lead',userName:'负责人',workspaceId:'w',permissionVersion:'v1',capabilities:{'team.view':true}};
const directory = [{id:'lead',name:'负责人',role:'fde_lead'},{id:'member',name:'成员',role:'fde'}];

function setup(kind, session = lead) {
  const calls = {list:[],overview:[],map:[],assets:[]};
  const app = {globalData:{role:session.role,session:{...session},roles:{fde:{name:'FDE',scope:'本人项目'},fde_lead:{name:'负责人',scope:'授权团队'}}}};
  const api = {
    getDirectoryMembers:async () => ({teams:[],items:directory}),
    listOpportunities:async params => {calls.list.push(params);return {items:[],summary:{total:0,open_amount:0},has_more:false,next_offset:null,facets:{product_lines:[]}};},
    getOpportunityOverview:async params => {calls.overview.push(params);return {metrics:{won:0,total:0,active:0,newCount:0,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0,demo_scene_count:0}};},
    getCustomerMap:async params => {calls.map.push(params);return {items:[],activity_since:'2026-03-22',as_of:'2026-09-22'};},
    getCustomerAssets:async params => {calls.assets.push(params);return {summary:{recognized_amount:0,collection_amount:0,acv_amount:0,portfolio_customer_count:0,unknown_acv_count:0}};},
  };
  let definition;
  const filename = path.join(base,kind==='map'?'pages/customers/index.js':'components/fde-projects/index.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{
    Page:value=>definition=value,Component:value=>definition=value,getApp:()=>app,
    require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),
    Date,Map,Set,setTimeout,clearTimeout,
    wx:{showNavigationBarLoading(){},hideNavigationBarLoading(){},showToast(){},getStorageSync(){return '';}}
  });
  const instance = {...definition,...definition.methods,properties:{initialScope:'',memberId:'',customerId:''},
    data:JSON.parse(JSON.stringify(definition.data)),setData(values,callback){Object.assign(this.data,values);if(callback)callback();}};
  return {instance,app,calls};
}

test('负责人商机忽略旧个人范围，人员筛选在完整团队中选择本人或多名成员',async()=>{
  const {instance:p,calls} = setup('projects');
  p.data.scope='self';
  p.lifetimes.attached.call(p);await tick();
  assert.equal(calls.list[0].scope,'team');assert.deepEqual(Array.from(calls.list[0].memberIds),[]);
  assert.equal(calls.overview[0].scope,'team');assert.ok(p.data.members.some(row=>row.id==='lead'));
  await p.member({detail:{ids:['lead']}});
  assert.equal(calls.list.at(-1).scope,'team');assert.deepEqual(Array.from(calls.list.at(-1).memberIds),['lead']);
  await p.member({detail:{ids:['lead','member']}});
  assert.deepEqual(Array.from(calls.overview.at(-1).memberIds),['lead','member']);
  await p.resetFilters();assert.deepEqual(Array.from(calls.list.at(-1).memberIds),[]);
  p.data.scope='self';await p.filter();assert.equal(calls.list.at(-1).scope,'team');
  assert.equal(typeof p.scope,'undefined');
});

test('看板本人钻取转为人员选中本人，清空后仍能返回授权团队列表',async()=>{
  const {instance:p,calls} = setup('projects');
  p.properties.initialScope='self';p.lifetimes.attached.call(p);await tick();
  assert.equal(calls.list[0].scope,'team');assert.deepEqual(Array.from(calls.list[0].memberIds),['lead']);
  assert.deepEqual(Array.from(calls.overview[0].memberIds),['lead']);
  await p.member({detail:{ids:[]}});assert.equal(calls.list.at(-1).scope,'team');assert.deepEqual(Array.from(calls.list.at(-1).memberIds),[]);
});

test('商机换账号清除旧成员；普通FDE和失去团队能力的负责人固定本人',async()=>{
  const {instance:p,app,calls} = setup('projects');
  await p.load();await p.member({detail:{ids:['member']}});
  app.globalData.session={...lead,userId:'new-lead'};await p.load();
  assert.deepEqual(Array.from(calls.list.at(-1).memberIds),[]);assert.equal(calls.list.at(-1).scope,'team');
  for(const session of [{...lead,role:'fde'},{...lead,capabilities:{'team.view':false}}]) {
    app.globalData.session=session;p.data.scope='team';p.data.memberIds=['member'];await p.load();
    assert.equal(calls.list.at(-1).scope,'self');assert.deepEqual(Array.from(calls.list.at(-1).memberIds),[]);assert.equal(p.data.canViewTeam,false);
  }
});

test('显式成员商机钻取保留ID且不混入主列表的多选成员',async()=>{
  const {instance:p,calls} = setup('projects');
  p.properties.memberId='member';p.data.memberIds=['lead'];await p.load();
  assert.equal(calls.list[0].scope,'team');assert.equal(calls.list[0].memberId,'member');assert.deepEqual(Array.from(calls.list[0].memberIds),[]);
  assert.equal(calls.overview[0].memberId,'member');assert.deepEqual(Array.from(calls.overview[0].memberIds),[]);
});

test('负责人地图始终请求授权团队，选本人/多人同步地图实绩，重置恢复完整范围',async()=>{
  const {instance:p,calls} = setup('map');
  p.data.fdeScope='self';await p.loadData();await tick();
  assert.equal(calls.map[0].scope,'team');assert.deepEqual(Array.from(calls.map[0].member_ids),[]);
  assert.ok(p.data.fdeMapMembers.some(row=>row.id==='lead'));
  await p.changeFdeMapMember({detail:{ids:['lead']}});await tick();
  assert.deepEqual(Array.from(calls.map.at(-1).member_ids),['lead']);assert.deepEqual(Array.from(calls.assets.at(-1).member_ids),['lead']);
  await p.changeFdeMapMember({detail:{ids:['lead','member']}});assert.equal(p.data.mapFilterActive,true);
  p.resetAllFilters();await tick();
  assert.deepEqual(Array.from(calls.map.at(-1).member_ids),[]);assert.equal(calls.map.at(-1).scope,'team');assert.equal(p.data.mapFilterActive,false);
  assert.equal(typeof p.changeFdeScope,'undefined');
});

test('地图同角色换账号丢弃旧人员筛选；普通FDE不会发送团队成员过滤',async()=>{
  const {instance:p,app,calls} = setup('map');
  await p.loadData();await p.changeFdeMapMember({detail:{ids:['member']}});
  app.globalData.session={...lead,userId:'new-lead'};await p.loadData();
  assert.equal(calls.map.at(-1).scope,'team');assert.deepEqual(Array.from(calls.map.at(-1).member_ids),[]);
  app.globalData.role='fde';app.globalData.session={...lead,role:'fde',capabilities:{...lead.capabilities,'team.view':false}};p.data.fdeScope='team';p.data.fdeMapMemberIds=['member'];await p.loadData();
  assert.equal(calls.map.at(-1).scope,'self');assert.deepEqual(Array.from(calls.map.at(-1).member_ids),[]);assert.equal(p.data.isFdeLead,false);
});
