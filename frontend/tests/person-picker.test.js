const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {normalizeMembers,normalizeTeams}=require('../miniprogram/utils/personPicker');
const teams=[{id:'south',name:'南区'},{id:'north',name:'北区'},{id:'empty',name:'空团队'}];
const members=[{id:'self',name:'张同名',account_code:'SALES-A',team_ids:['south']},{id:'other',name:'张同名',account_code:'SALES-B',team_ids:['north']},{id:'both',name:'跨组同事',account_code:'BOTH',team_ids:['south','north']}];
function component(relative='components/person-picker/index.js',props={},api={}){
  let definition;const events=[];const session={userId:'self',userName:'本人',workspaceId:'w',role:'fde_lead',capabilities:{'team.view':true}};
  const filename=path.resolve(__dirname,'../miniprogram',relative);
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Component:value=>definition=value,require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),getApp:()=>({globalData:{session}}),wx:{},Date,Set,Map});
  const properties=Object.fromEntries(Object.entries(definition.properties).map(([key,value])=>[key,value&&Object.prototype.hasOwnProperty.call(value,'value')?value.value:value===Array?[]:value===Boolean?false:'']));
  Object.assign(properties,props);
  const instance={...definition,...definition.methods,properties,data:JSON.parse(JSON.stringify(definition.data)),setData(data){Object.assign(this.data,data);},triggerEvent(name,detail){events.push({name,detail});}};
  return {instance,events,session};
}
function picker(props={}){const h=component(undefined,{open:true,members,teams,defaultTeamId:'south',selected:['self'],...props});h.instance.begin();return h;}
const ids=rows=>Array.from(rows,row=>row.id);
const choose=(instance,id)=>instance.toggle({currentTarget:{dataset:{id}}});
const team=(instance,id)=>instance.chooseTeam({currentTarget:{dataset:{id}}});

test('默认本人团队只缩小候选名单，打开和搜索均不提交页面范围',()=>{
  const {instance:p,events}=picker({selected:['other']});
  assert.equal(p.data.teamId,'south');assert.deepEqual(ids(p.data.rows),['self','both']);assert.deepEqual(Array.from(p.data.draft),['other']);
  p.search({detail:{value:'SALES-A'}});assert.deepEqual(ids(p.data.rows),['self']);assert.equal(events.length,0);
  p.close();assert.equal(events[0].name,'close');assert.deepEqual(Array.from(p.properties.selected),['other']);
});
test('姓名、账号和团队支持大小写不敏感的部分匹配，同名保留账号和部门',()=>{
  const {instance:p}=picker();team(p,'');
  for(const [query,expected] of [['同名',['self','other']],['sales-b',['other']],['北',['other','both']],['不存在',[]]]){
    p.search({detail:{value:query}});assert.deepEqual(ids(p.data.rows),expected);
  }
  p.search({detail:{value:'张'}});assert.equal(p.data.rows[0].account_code,'SALES-A');assert.equal(p.data.rows[0].teamLabel,'南区');assert.equal(p.data.rows[1].teamLabel,'北区');
});
test('跨团队多选保留已选人员，搜索零结果仍可确认原勾选',()=>{
  const {instance:p,events}=picker({multiple:true});team(p,'north');choose(p,'other');
  p.search({detail:{value:'不存在'}});assert.equal(p.data.rows.length,0);assert.deepEqual(Array.from(p.data.draft),['self','other']);
  p.confirm();assert.equal(events[0].name,'confirm');assert.deepEqual(Array.from(events[0].detail.ids),['self','other']);assert.equal(events[0].detail.teamId,'north');
});
test('未授权默认团队退回全部；空团队不会改变当前选人',()=>{
  const {instance:p}=picker({defaultTeamId:'forged'});assert.equal(p.data.teamId,'');assert.equal(p.data.rows.length,3);
  team(p,'empty');assert.equal(p.data.rows.length,0);assert.deepEqual(Array.from(p.data.draft),['self']);
});
test('全局全部必须允许才可选择，未选择的必选单选不能确认',()=>{
  const strict=picker({selected:[]});strict.instance.confirm();strict.instance.chooseAll();assert.equal(strict.events.length,0);assert.equal(strict.instance.data.canConfirm,false);
  const all=picker({allowAll:true});all.instance.chooseAll();all.instance.confirm();assert.deepEqual(Array.from(all.events[0].detail.ids),[]);
});
test('撤销人员权限不能把失效勾选静默变成全部，显式重选才可确认',()=>{
  const {instance:p,events}=picker({allowAll:true});p.properties.members=members.filter(row=>row.id!=='self');p.refresh();
  assert.deepEqual(Array.from(p.data.draft),[]);assert.equal(p.data.canConfirm,false);assert.match(p.data.selectionError,/已不可用/);p.confirm();assert.equal(events.length,0);
  p.search({detail:{value:'同名'}});team(p,'north');p.confirm();assert.equal(events.length,0);
  choose(p,'other');p.confirm();assert.deepEqual(Array.from(events[0].detail.ids),['other']);
  p.properties.members=[];p.refresh();p.chooseAll();p.confirm();assert.deepEqual(Array.from(events[1].detail.ids),[]);
});
test('加载中的空候选不丢勾选，异步目录返回后才校验并应用本人团队',()=>{
  const {instance:p}=picker({members:[],teams:[],defaultTeamId:'',loading:true,multiple:true,selected:['self','other']});
  assert.deepEqual(Array.from(p.data.draft),['self','other']);assert.equal(p.data.canConfirm,false);
  Object.assign(p.properties,{members,teams,defaultTeamId:'south',loading:false});p.refresh();assert.equal(p.data.teamId,'south');assert.deepEqual(Array.from(p.data.draft),['self','other']);assert.equal(p.data.canConfirm,true);
  team(p,'north');p.properties.defaultTeamId='south';p.refresh();assert.equal(p.data.teamId,'north');
});
test('最大多选、错误和伪造ID均不可绕过，重开恢复宿主提交值',()=>{
  const {instance:p,events}=picker({multiple:true,maxSelected:1});choose(p,'other');assert.match(p.data.selectionError,/最多选择 1 人/);choose(p,'forged');assert.deepEqual(Array.from(p.data.draft),['self']);
  p.properties.error='目录失效';p.refresh();p.confirm();assert.equal(events.length,0);p.retry();assert.equal(events[0].name,'retry');
  p.properties.error='';p.properties.multiple=false;choose(p,'other');p.begin();assert.deepEqual(Array.from(p.data.draft),['self']);assert.equal(p.data.teamId,'south');
});
test('标准化目录以稳定ID识别成员、去重和合成团队标签',()=>{
  assert.deepEqual(normalizeTeams([...teams,{id:'all',name:'全部'},teams[0]]).map(row=>row.id),['south','north','empty']);
  const rows=normalizeMembers([{user_id:'u',display_name:'姓名',account_code:'CODE',team_ids:['south','north']},{user_id:'u',display_name:'新名',team_id:'north'}],teams);
  assert.equal(rows.length,1);assert.equal(rows[0].id,'u');assert.equal(rows[0].name,'新名');assert.equal(rows[0].teamLabel,'北区');
});
test('共享弹层高度有上限且不控制宿主原生TabBar',()=>{
  const dir=path.resolve(__dirname,'../miniprogram/components/person-picker');
  assert.match(fs.readFileSync(path.join(dir,'index.wxml'),'utf8'),/root-portal/);
  assert.match(fs.readFileSync(path.join(dir,'index.wxml'),'utf8'),/<sb-sheet\s/);
  assert.doesNotMatch(fs.readFileSync(path.join(dir,'index.wxml'),'utf8'),/person-rows/);
  assert.doesNotMatch(fs.readFileSync(path.join(dir,'index.js'),'utf8'),/hideTabBar|showTabBar/);
});
test('我的选择器确认只选人，候选团队不会覆盖已提交的团队范围',()=>{
  const {instance:p,events}=component('components/profile-scope-picker/index.js',{members,teams,mode:'person',memberId:'self',teamId:'north',defaultTeamId:'south'});
  p.show();assert.equal(p.data.open,true);p.confirmPerson({detail:{ids:['other'],teamId:'south'}});
  assert.equal(p.properties.teamId,'north');assert.equal(events.at(-1).name,'subjectchange');assert.deepEqual({...events.at(-1).detail},{kind:'person',id:'other'});
});
test('FDE看板使用授权目录，只在确认后转换本人ID并应用成员，不改变团队汇总',async()=>{
  const result={data_source:'database',members:members.map(row=>({...row,user_id:row.id,display_name:row.name})),teams,defaults:{team_id:'south'}};
  const {instance:p,events,session}=component('components/fde-dashboard/index.js',{}, {getFdeScopeOptions:async()=>result});
  Object.assign(p.data,{canViewTeam:true,scope:'self',memberOptions:[{id:'',name:'本人'}],memberIndex:0});let loads=0;p.load=()=>loads++;
  await p.openMemberPicker();assert.equal(p.data.pickerDefaultTeamId,'south');assert.equal(loads,0);assert.equal(p.data.scope,'self');assert.deepEqual(Array.from(p.data.pickerSelected),['self']);
  p.selectMember({detail:{ids:['other'],teamId:'north'}});assert.equal(loads,1);assert.equal(p.params().member_id,'other');assert.equal(events.at(-1).detail.open,false);
  await p.openMemberPicker();p.selectMember({detail:{ids:['self'],teamId:'south'}});assert.equal(p.params().member_id,'');assert.equal(loads,2);
  await p.openMemberPicker();session.capabilities['team.view']=false;p.selectMember({detail:{ids:['other']}});assert.equal(loads,2);
});
function bi(api={}){
  let page;const ui=[];
  const session={role:'manager',userId:'self',userName:'本人',workspaceId:'w',capabilities:{'team.view':true}},app={globalData:{session,role:'manager'}};
  const filename=path.resolve(__dirname,'../miniprogram/pages/bi/index.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:value=>page=value,require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),getApp:()=>app,wx:{hideTabBar(){ui.push('hide');},showTabBar(){ui.push('show');}},Date,Set,Map});
  page.data=JSON.parse(JSON.stringify(page.data));page.setData=data=>Object.assign(page.data,data);
  Object.assign(page.data,{canViewTeam:true,viewMode:'personal'});let calls=0;page.loadFacts=()=>{calls++;return Promise.resolve();};page.loadRankingData=()=>Promise.resolve();
  return {page,session,ui,get calls(){return calls;}};
}
test('看板候选本人团队不改变团队汇总，打开取消不查询，确认仅应用成员',async()=>{
  const directory={members,teams,defaults:{team_id:'south'},team_groups:[{code:'team:south',name:'南区'},{code:'team:north',name:'北区'}]};
  const h=bi({getDashboardOptions:async()=>directory});await h.page.loadOptions(false);
  assert.equal(h.page.data.memberPickerDefaultTeamId,'south');assert.deepEqual(Array.from(h.page.data.selectedTeamGroups),['team:south','team:north']);
  h.page.openMemberPicker();assert.equal(h.calls,0);h.page.closeMemberPicker();assert.equal(h.calls,0);h.page.confirmMember({detail:{ids:['other'],teamId:'north'}});assert.equal(h.calls,0);
  h.page.openMemberPicker();await h.page.confirmMember({detail:{ids:['other'],teamId:'north'}});
  assert.equal(h.calls,1);assert.equal(h.page.data.selectedMemberId,'other');assert.deepEqual(Array.from(h.page.data.selectedTeamGroups),['team:south','team:north']);assert.equal(h.ui.at(-1),'show');
});
test('看板确认拒绝旧账号、失权和离页事件，隐藏后恢复TabBar',async()=>{
  const h=bi({getDashboardOptions:async()=>({members,teams,defaults:{team_id:'south'},team_groups:[]})});await h.page.loadOptions(false);
  h.page.openMemberPicker();h.session.workspaceId='other-workspace';h.page.confirmMember({detail:{ids:['other']}});assert.equal(h.calls,0);
  h.page.closeMemberPicker();h.page.openMemberPicker();h.session.capabilities['team.view']=false;h.page.confirmMember({detail:{ids:['other']}});assert.equal(h.calls,0);
  h.page.onHide();assert.equal(h.page.data.memberPickerOpen,false);assert.equal(h.ui.at(-1),'show');h.page.confirmMember({detail:{ids:['other']}});assert.equal(h.calls,0);
});
test('我的共享确认拒绝打开后切换身份或关闭后的旧事件',()=>{
  const {instance:p,events,session}=component('components/profile-scope-picker/index.js',{members,teams,mode:'person',memberId:'self',defaultTeamId:'south'});
  p.show();session.workspaceId='other';p.confirmPerson({detail:{ids:['other']}});assert.equal(events.filter(row=>row.name==='subjectchange').length,0);
  p.close();p.confirmPerson({detail:{ids:['other']}});assert.equal(events.filter(row=>row.name==='subjectchange').length,0);
});
test('我的团队选择与人员选择相同，关闭、换身份和目录异常均拒绝旧点击',()=>{
 const {instance:p,events,session}=component('components/profile-scope-picker/index.js',{members,teams,mode:'team',teamId:'north'});
 const select=()=>p.select({currentTarget:{dataset:{id:'south'}}}),count=()=>events.filter(row=>row.name==='subjectchange').length;
 p.show();session.permissionVersion='new';select();assert.equal(count(),0);
 p.close();p.show();p.close();select();assert.equal(count(),0);
 p.show();p.properties.loading=true;select();assert.equal(count(),0);p.properties.loading=false;p.properties.error='目录加载失败';select();assert.equal(count(),0);
 p.properties.error='';select();assert.equal(count(),1);assert.deepEqual({...events.at(-1).detail},{kind:'team',id:'south'});
});
test('人员目录契约拒绝旧字段、非法团队和重复ID；未知本人主团队必须显式null',()=>{
  const {assertPersonDirectory}=require('../miniprogram/utils/personPicker');
  assert.equal(assertPersonDirectory(members,teams,{team_id:'south'}),true);
  assert.equal(assertPersonDirectory([{user_id:'self',display_name:'本人',team_ids:[]}],[],{team_id:null}),true);
  const cases=[
    [members,undefined,{team_id:null}],[members,teams,{}],
    [[{id:'self',name:'本人'}],teams,{team_id:'south'}],
    [members,teams,{team_id:'not-authorized'}],
    [[{id:'self',name:'本人',team_ids:['not-authorized']}],teams,{team_id:'south'}],
    [[...members,members[0]],teams,{team_id:'south'}],
    [members,[...teams,teams[0]],{team_id:'south'}],
    [[{id:'self',name:'本人',team_ids:[],account_code:7}],teams,{team_id:'south'}],
  ];
  for(const args of cases)assert.throws(()=>assertPersonDirectory(...args),/人员目录缺少团队字段或范围不完整/);
});
test('BI和FDE旧目录缺团队字段时显式失败且不能确认旧选项',async()=>{
  const h=bi({getDashboardOptions:async()=>({members:[{id:'self',name:'本人'}],team_groups:[]})});await h.page.loadOptions(false);
  assert.match(h.page.data.optionsError,/配套后端/);h.page.openMemberPicker();h.page.confirmMember({detail:{ids:['self']}});assert.equal(h.calls,0);
  const f=component('components/fde-dashboard/index.js',{}, {getFdeScopeOptions:async()=>({data_source:'database',members:[{user_id:'self',display_name:'本人'}],teams:[],defaults:{team_id:null}})});
  Object.assign(f.instance.data,{canViewTeam:true,scope:'self',memberOptions:[{id:'',name:'本人'}]});let loads=0;f.instance.load=()=>loads++;
  await f.instance.openMemberPicker();assert.match(f.instance.data.pickerError,/配套后端/);f.instance.selectMember({detail:{ids:['self']}});assert.equal(loads,0);
});
