const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const access=require('../miniprogram/utils/access');
const grant=(permission_code,scope_code,team_ids=[],effect='allow')=>({permission_code,scope_code,team_ids,effect});
function user(role='sales',scope='teams') {
  const reads=['customer.read','battle_map.read','opportunity.read','visit.read','task.read','risk.read'];
  return {role,userId:'u1',workspaceId:'w1',loginAt:1,permissionVersion:'v1',scope:'仅本人',teamIds:['t1','t2'],
    permissions:Object.fromEntries([...reads,'access.mini_program'].map(key=>[key,true])),
    permissionGrants:reads.map(key=>grant(key,scope,['t1','t2']))};
}
function page(relative,session,api={}) {
  const filename=path.resolve(__dirname,'../miniprogram',relative);let definition;
  const app={globalData:{session,role:session.role,roles:{[session.role]:{name:session.role}}},ensureLogin:()=>true};
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:d=>definition=d,Component:d=>definition=d,
    require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),getApp:()=>app,
    wx:{showToast(){},showNavigationBarLoading(){},hideNavigationBarLoading(){}},Date,Map,Set,setTimeout,clearTimeout});
  const p={...definition,...definition.methods,properties:{},data:JSON.parse(JSON.stringify(definition.data)),setData(values,cb){Object.assign(this.data,values);if(cb)cb();}};
  return {p,app};
}
for(const [role,scope] of [['sales','teams'],['fde','teams'],['supervisor','workspace'],['fde_lead','workspace'],['manager','workspace']]) {
  test(`${role}: server action grants determine scope without widening writes`,()=>{
    const s=user(role,scope),read=access.scopeFor(s,'opportunity.read');
    assert.equal(read.scope,scope);assert.equal(read.allowed,true);
    assert.deepEqual(read.teamIds,scope==='teams'?['t1','t2']:[]);
    assert.equal(access.canViewTeam(s,'opportunity.read'),true);
    assert.equal(access.can(s,'opportunity.update'),false);
    assert.equal(access.can(s,'task.complete'),false);
    assert.equal(access.can(s,'risk.resolve'),false);
    assert.equal(access.can(s,'visit.download'),false);
    assert.equal(access.can(s,'company.export'),false);
    s.permissionGrants=s.permissionGrants.map(g=>({...g,scope_code:'self',team_ids:[]}));
    assert.equal(access.canViewTeam(s,'opportunity.read'),false,'role must not restore a wider scope');
  });
}
test('unions only the requested action and honors deny, false, absent and empty grants',()=>{
  const s=user();s.permissions['customer.update']=true;
  s.permissionGrants=[grant('customer.read','self'),grant('customer.read','teams',['t2']),grant('customer.read','teams',['t1','t2']),grant('customer.update','self'),grant('risk.read','workspace')];
  assert.deepEqual(access.scopeFor(s,'customer.read').scopes,['self','teams']);
  assert.deepEqual(access.scopeFor(s,'customer.read').teamIds,['t1','t2']);
  assert.equal(access.scopeFor(s,'customer.update').scope,'self');
  assert.equal(access.scopeFor(s,'opportunity.read').allowed,false,'a true feature flag cannot invent a read scope');
  s.permissionGrants.push(grant('customer.read','workspace',[],'deny'));
  assert.equal(access.can(s,'customer.read'),false);
  assert.equal(access.scopeFor(s,'customer.read').scope,'none');
  assert.equal(access.canViewTeam(s,'customer.read'),false);
  s.permissions['risk.read']=false;assert.equal(access.scopeFor(s,'risk.read').allowed,false);
  assert.equal(access.scopeFor(s,'missing.read').allowed,false);
});
test('legacy capability sessions retain existing switches without role or team-id inference',()=>{
  const legacy={role:'manager',teamIds:['t1'],capabilities:{'customer.read':true}};
  assert.equal(access.canViewTeam(legacy,'customer.read'),false);
  assert.deepEqual(access.scopeFor(legacy,'customer.read').teamIds,[]);
  legacy.capabilities['team.view']=true;
  assert.equal(access.canViewTeam(legacy,'customer.read'),true);
  assert.deepEqual(access.scopeFor(legacy,'customer.read').teamIds,[]);
});
test('grant/organization/version/company changes invalidate identity, reordering does not',()=>{
  const s=user(),original=access.identity(s);
  s.permissionGrants.reverse();s.teamIds.reverse();s.permissionGrants.forEach(g=>g.team_ids.reverse());
  assert.equal(access.identity(s),original);
  for(const change of [s=>s.permissionGrants[0].team_ids.push('new-team'),s=>s.permissions['task.read']=false,s=>s.clientExportPermissions={'visit.export':true},s=>s.permissionVersion='v2',s=>s.workspaceId='other-company']) {
    const copy=JSON.parse(JSON.stringify(s));change(copy);assert.notEqual(access.identity(copy),original);
  }
});
test('FDE member and leader project scope follows opportunity.read, not a role or another action',()=>{
  for(const role of ['fde','fde_lead']){
    const s=user(role,role==='fde'?'teams':'workspace'),{p}=page('components/fde-projects/index.js',s);
    assert.equal(p.queryScope(),'team');
    s.permissionGrants=s.permissionGrants.filter(g=>g.permission_code!=='opportunity.read').concat(grant('opportunity.read','assigned'));
    assert.equal(p.queryScope(),'self');
  }
});
test('FDE project directory preserves all server-returned authorized members',async()=>{
  let options;const s=user('fde_lead','workspace'),{p}=page('components/fde-projects/index.js',s,{getDirectoryMembers:async value=>{options=value;return {items:[{id:'sales-person',name:'销售',role:'sales',team_ids:['t1']}],teams:[{id:'t1',name:'部门'}],defaults:{team_id:'t1'}};}});
  await p.loadMembers(access.identity(s),[]);
  assert.equal(p.data.membersError,'');assert.equal(p.data.members[1].id,'sales-person');
  assert.equal(options.permission,'opportunity.read');assert.equal(options.include_stats,false);
});
test('map requests keep full server-granted departments and explicit filters without first-team clipping',()=>{
  for(const role of ['sales','supervisor','fde','fde_lead']){
    const s=user(role,role.includes('lead')||role==='supervisor'?'workspace':'teams'),{p}=page('pages/customers/index.js',s);
    Object.assign(p.data,{canViewTeam:true,isFde:role.startsWith('fde'),mapScope:'team'});
    assert.equal(p.fdeMapParams().scope,'team');assert.equal(p.fdeMapParams().team_id,undefined);
    p.data.selectedTeam='t2';assert.equal(p.fdeMapParams().team_id,'t2');
    p.data.mapScope='self';assert.equal(p.fdeMapParams().scope,'self');assert.equal(p.fdeMapParams().team_id,undefined);
  }
});
test('地图选团队后选成员使用个人契约，清空成员恢复团队，切回本人不泄漏筛选',()=>{
  for(const role of ['sales','supervisor','manager']){
    const {p}=page('pages/customers/index.js',user(role));
    Object.assign(p.data,{mapScope:'team',selectedTeam:'t2',selectedMember:'u2',activityWindow:'month'});
    assert.equal(JSON.stringify(p.fdeMapParams()),JSON.stringify({scope:'person',activity_window:'month',member_id:'u2'}));
    p.data.selectedMember='all';
    assert.equal(JSON.stringify(p.fdeMapParams()),JSON.stringify({scope:'team',activity_window:'month',team_id:'t2'}));
    p.data.selectedMember='u2';p.data.mapScope='self';
    assert.equal(JSON.stringify(p.fdeMapParams()),JSON.stringify({scope:'self',activity_window:'month'}));
  }
});
test('FDE地图多人筛选不同时提交团队主体，只有本人授权时忽略残留成员',()=>{
  const s=user('fde'),{p}=page('pages/customers/index.js',s);
  Object.assign(p.data,{isFde:true,mapScope:'team',selectedTeam:'t2',fdeMapMemberIds:['u1','u2'],activityWindow:'week'});
  assert.equal(JSON.stringify(p.fdeMapParams()),JSON.stringify({scope:'team',activity_window:'week',member_ids:['u1','u2']}));
  p.data.fdeMapMemberIds=[];assert.equal(p.fdeMapParams().team_id,'t2');
  s.permissionGrants=[grant('battle_map.read','self')];p.data.fdeMapMemberIds=['u2'];
  assert.equal(JSON.stringify(p.fdeMapParams()),JSON.stringify({scope:'self',activity_window:'week'}));
});
test('旧销售会话的地图也保持团队与个人主体互斥',()=>{
  const {p}=page('pages/customers/index.js',{role:'sales',teamIds:['t1']});
  Object.assign(p.data,{mapScope:'team',selectedTeam:'t2',selectedMember:'u2'});
  assert.equal(p.fdeMapParams().scope,'person');assert.equal(p.fdeMapParams().team_id,undefined);
  assert.equal(p.fdeMapParams().member_id,'u2');
  p.data.mapScope='self';assert.equal(p.fdeMapParams().member_id,undefined);
});
test('map does not re-authorize server-filtered collaboration through sales owner directory',()=>{
  const {p}=page('pages/customers/index.js',user());
  Object.assign(p.data,{selectedTeam:'t2',selectedMember:'authorized-fde',isFde:false});
  p.visibleCustomers=[{id:'customer',name:'客户',owner_user_ref_id:'different-sales-owner',owner_team_id:'t3',potential:80,relationship:70}];
  p.filterMembers=[];p.applyFilters();assert.equal(p.data.customers.length,1);
});
test('workbench full department filter does not force the first membership',()=>{
  const {p}=page('pages/workbench/index.js',user());
  Object.assign(p.data,{canViewTeam:true,executionScope:'team',executionTeamKey:'all'});
  assert.equal(p.opportunityParams().teamId,'all');assert.equal(p.opportunityParams().scope,'team');
  p.data.executionTeamKey='t2';assert.equal(p.opportunityParams().teamId,'t2');
  p.data.canViewTeam=false;assert.equal(p.opportunityParams().scope,'self');assert.equal(p.opportunityParams().teamId,'all');
});
test('workbench scope copy follows the same request as the overview, including self and selected filters',async()=>{
  for(const [role,grantScope,label] of [['sales','teams','已授权部门'],['supervisor','workspace','公司全部部门'],['manager','workspace','公司全部部门']]){
    const calls=[];const {p}=page('pages/workbench/index.js',user(role,grantScope),{listOpportunities:async params=>{
      calls.push(JSON.parse(JSON.stringify(params)));
      return {items:[],summary:{total:0},has_more:false,next_offset:null,
        team_options:[{id:'t2',name:'第二部门'}],owner_options:[{id:'person',name:'选定成员',team_ids:['t2']}],
        ...(params.includeOverview?{overview:{definition_version:'opportunity_filtered_overview_v1',
          year:params.overviewYear,quarters:params.overviewQuarters,
          metrics:{total:0,won:0,active:0,newCount:0,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0,demo_scene_count:0}}}:{})};
    }});
    const read=async()=>{
      const start=calls.length;
      await p.loadAllOpportunities();
      if(p.opportunityOverviewFlight)await p.opportunityOverviewFlight.promise;
      const requests=calls.slice(start);assert.equal(requests.length,2,'list and filtered overview must both be read');
      const [list,overview]=requests;
      assert.equal(list.includeOverview,false);assert.equal(list.pageSize,20);
      assert.equal(overview.includeOverview,true);assert.equal(overview.pageSize,1);
      assert.equal(overview.overviewYear,p.data.summaryQuarter.year);
      assert.deepEqual(overview.overviewQuarters,Array.from(p.data.summaryQuarter.quarters));
      const {includeOverview:li,pageSize:lp,...listFilters}=list;
      const {includeOverview:oi,pageSize:op,overviewYear,overviewQuarters,...overviewFilters}=overview;
      assert.deepEqual(overviewFilters,listFilters,'statistics must preserve the full list scope and selected filters');
      assert.equal(p.data.opportunityListError,'');assert.equal(p.data.opportunityDataError,'');
      assert.equal(p.data.opportunityDataReady,true);
      return list;
    };
    Object.assign(p.data,{canViewTeam:true,executionScope:'self',executionTeamOptions:[{value:'all'},{value:'t2',label:'第二部门'}],opportunityOwnerOptions:[{value:'all'},{value:'person',label:'选定成员'}]});
    let sent=await read();assert.equal(sent.scope,'self');assert.equal(sent.teamId,'all');assert.equal(sent.ownerId,null);assert.equal(p.data.scope,'本人');
    p.data.executionScope='team';sent=await read();assert.equal(sent.scope,'team');assert.equal(sent.teamId,'all');assert.equal(sent.ownerId,null);assert.equal(p.data.scope,label);
    Object.assign(p.data,{executionTeamKey:'t2',opportunityOwnerIndex:1});
    sent=await read();assert.equal(sent.teamId,'t2');assert.equal(sent.ownerId,'person');assert.equal(p.data.scope,`${label} · 第二部门 · 选定成员`);
    p.data.canViewTeam=false;sent=await read();assert.equal(sent.scope,'self');assert.equal(sent.teamId,'all');assert.equal(sent.ownerId,null);assert.equal(p.data.scope,'本人');
  }
  const template=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/workbench/index.wxml'),'utf8');
  assert.match(template,/class="opportunity-summary-scope">\{\{scope\}\}/);
});
test('workbench explicit owner/team selection enters team scope; returning to self drops both filters',()=>{
  const {p}=page('pages/workbench/index.js',user());
  p.applyOpportunityFilters=()=>{};
  Object.assign(p.data,{canViewTeam:true,opportunityOwnerOptions:[{value:'all'},{value:'colleague'}],executionTeamOptions:[{value:'all'},{value:'t2',label:'第二部门'}]});
  p.changeOpportunityFilter({currentTarget:{dataset:{key:'Owner'}},detail:{value:1}});
  assert.equal(p.opportunityParams().scope,'team');assert.equal(p.opportunityParams().ownerId,'colleague');
  p.changeExecutionTeam({detail:{value:1}});
  assert.equal(p.opportunityParams().teamId,'t2');assert.equal(p.opportunityParams().ownerId,null);
  p.data.executionMemberName='stale-member';
  p.changeExecutionScope({detail:{value:0}});
  assert.equal(p.opportunityParams().scope,'self');assert.equal(p.opportunityParams().teamId,'all');assert.equal(p.opportunityParams().ownerId,null);
  p.changeExecutionScope({detail:{value:1}});assert.equal(p.opportunityParams().ownerId,null);
});
test('new export actions pass through without role assumptions and never inherit read access',()=>{
  const s=user('sales');
  for(const action of ['visit.export','report.export']){
    assert.equal(access.can(s,action),false);
    s.permissions[action]=true;assert.equal(access.can(s,action),true);
    s.permissionGrants.push(grant(action,'workspace',[],'deny'));assert.equal(access.can(s,action),false);
  }
});
test('workbench refresh retains authorized filters and resets them when the grant changes',async()=>{
  const s=user(),{p}=page('pages/workbench/index.js',s);
  p.loadAllOpportunities=async()=>{};
  await p.loadData();
  Object.assign(p.data,{executionScope:'team',executionScopeIndex:1,executionTeamKey:'t2',opportunityOwnerIndex:1,opportunityOwnerOptions:[{value:'all'},{value:'colleague'}]});
  await p.loadData();assert.equal(p.opportunityParams().scope,'team');assert.equal(p.opportunityParams().teamId,'t2');assert.equal(p.opportunityParams().ownerId,'colleague');
  s.permissionGrants=s.permissionGrants.map(g=>({...g,scope_code:'self',team_ids:[]}));
  await p.loadData();assert.equal(p.opportunityParams().scope,'self');assert.equal(p.opportunityParams().teamId,'all');assert.equal(p.opportunityParams().ownerId,null);
});
test('native task queries preserve legacy filtering even when the Web helper exists',async()=>{
  const calls=[];const response={items:[],summary:{total:0,pending_count:0,completed_count:0,filtered_total:0},has_more:false,next_offset:null};
  const api={listTaskManagement:async params=>{calls.push(['web',params]);return response;},listTaskPage:async params=>{calls.push(['mini',params]);return response;}};
  const {p}=page('pages/tasks/index.js',user(),api);Object.assign(p.data,{activeTab:'completed',taskView:'team',completedYear:2026,completedQuarters:[1,3],overviewFilter:'today_completed',opportunityOnly:true});
  await p.fetchTaskPage(false);assert.equal(calls[0][0],'mini');
  assert.equal(calls[0][1].view,'team');assert.equal(calls[0][1].tab,'completed');assert.equal(calls[0][1].overview,'today_completed');
  assert.deepEqual(Array.from(calls[0][1].completed_quarters),[1,3]);assert.equal(calls[0][1].opportunity_only,true);
  p.taskManagementParams=()=>({scope:'all',status:'completed'});await p.fetchTaskPage(false);
  assert.equal(calls[1][0],'web');assert.equal(calls[1][1].scope,'all');
});
test('native task badge describes the sent scope while the Web adapter keeps its own label',async()=>{
  let sent;const result={items:[],summary:{total:0,pending_count:0,completed_count:0,filtered_total:0},has_more:false,next_offset:null};
  const {p}=page('pages/tasks/index.js',user(),{listTaskPage:async params=>{sent=params;return result;},listTaskManagement:async()=>result});
  p.teamFilter=p.memberFilter='all';p.data.taskView='self';await p.fetchTaskPage(false);
  assert.equal(sent.view,'self');assert.equal(p.data.scope,'本人');
  p.data.taskView='team';await p.fetchTaskPage(false);assert.equal(sent.view,'team');assert.equal(p.data.scope,'已授权部门');
  p.data.fdeTaskMemberId='colleague';await p.fetchTaskPage(false);assert.equal(sent.member_id,'colleague');assert.equal(p.data.scope,'所选成员');
  p.data.fdeTaskMemberId='';p.teamFilter='第二部门';await p.fetchTaskPage(false);assert.equal(sent.team,'第二部门');assert.equal(p.data.scope,'所选部门');
  p.taskManagementParams=()=>({scope:'all',team_id:'t2'});p.data.scope='Web适配范围';await p.fetchTaskPage(false);assert.equal(p.data.scope,'Web适配范围');
});

function transport(){
  const storage=new Map(),requests=[],filename=path.resolve(__dirname,'../miniprogram/utils/apiClient.js'),module={exports:{}};
  const wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>requests.push(r)};
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{module,wx,Map,Set,Date,require:name=>require(path.resolve(path.dirname(filename),name))});
  const api=module.exports;api.saveAuth({access_token:'test-only',actor:{user_id:'u1',workspace_id:'w1',role:'sales',permission_version:'v1',permission_grants:[grant('customer.read','teams',['t1'])]}});
  return {api,requests,reply:(index,data={})=>requests[index].success({statusCode:200,data})};
}
test('directory supports action-scoped basic reads while no-argument requests stay unchanged',async()=>{
  const h=transport();let pending=h.api.getDirectoryMembers();assert.equal(new URL(h.requests[0].url).search,'');h.reply(0,{items:[]});await pending;
  pending=h.api.getDirectoryMembers({permission:'visit.read',include_stats:false});let query=new URL(h.requests[1].url).searchParams;
  assert.equal(query.get('permission'),'visit.read');assert.equal(query.get('include_stats'),'false');h.reply(1);await pending;
  pending=h.api.getTeamDirectory();assert.equal(new URL(h.requests[2].url).search,'?purpose=browse');h.reply(2);await pending;
  pending=h.api.getTeamDirectory('browse',{permission:'opportunity.read'});query=new URL(h.requests[3].url).searchParams;
  assert.equal(query.get('purpose'),'browse');assert.equal(query.get('permission'),'opportunity.read');h.reply(3);await pending;
});
test('customer assignment directory sends the exact create or request action to the server',async()=>{
  const h=transport();
  for(const permission of ['customer.create_request','customer.create']){
    const pending=h.api.getTeamDirectory('assignment',{permission});
    const index=h.requests.length-1,query=new URL(h.requests[index].url).searchParams;
    assert.equal(query.get('purpose'),'assignment');assert.equal(query.get('permission'),permission);
    h.reply(index,{teams:[]});await pending;
  }
});
test('task management list and owner counts preserve the same team/member intersection',async()=>{
  const h=transport();
  for(const method of ['listTaskManagement','listTaskManagementOwners']){
    let pending=h.api[method]({scope:'all',team_id:'team-2',member_id:'member-2',status:'pending',offset:20});
    let index=h.requests.length-1,query=new URL(h.requests[index].url).searchParams;
    assert.equal(query.get('team_id'),'team-2');assert.equal(query.get('member_id'),'member-2');
    assert.equal(query.get('scope'),'all');assert.equal(query.get('status'),'pending');assert.equal(query.get('offset'),'20');
    h.reply(index,{items:[]});await pending;
    pending=h.api[method]();index=h.requests.length-1;query=new URL(h.requests[index].url).searchParams;
    assert.equal(query.has('team_id'),false);assert.equal(query.has('member_id'),false);assert.equal(query.get('scope'),'mine');
    h.reply(index,{items:[]});await pending;
  }
});
test('same-version grant change discards an in-flight read; unchanged actor does not',async()=>{
  const h=transport();let pending=h.api.request({path:'/customers'});
  const actor=h.api.getAuth().actor;h.api.updateActor({...actor});h.reply(0,{items:[]});await pending;
  pending=h.api.request({path:'/customers'});const rejected=assert.rejects(pending,{code:'SESSION_CHANGED'});
  h.api.updateActor({...actor,permission_grants:[grant('customer.read','self')]});h.reply(1,{items:[{id:'old-department'}]});await rejected;
});
test('same-version client export decision change invalidates the actor snapshot',async()=>{
  const h=transport(),actor=h.api.getAuth().actor;
  const pending=h.api.request({path:'/visits'}),rejected=assert.rejects(pending,{code:'SESSION_CHANGED'});
  h.api.updateActor({...actor,client_export_permissions:{'visit.export':true}});h.reply(0,{items:[]});await rejected;
});
test('object write flags never inherit the read scope or override an action deny',()=>{
  const s=user('manager','workspace');s.permissions['risk.resolve']=true;s.permissionGrants.push(grant('risk.resolve','self'));
  assert.equal(access.canObject(s,'risk.resolve',{can_resolve:false},'can_resolve'),false);
  assert.equal(access.canObject(s,'risk.resolve',{},'can_resolve'),false);
  assert.equal(access.canObject(s,'risk.resolve',{can_resolve:true},'can_resolve'),true);
  assert.equal(access.canObject(s,'risk.resolve',{can_resolve:true,action_permissions:{'risk.resolve':false}},'can_resolve'),false);
  s.permissionGrants.push(grant('risk.resolve','workspace',[],'deny'));
  assert.equal(access.canObject(s,'risk.resolve',{can_resolve:true},'can_resolve'),false);
  const legacy={capabilities:{'risk.resolve':true}};
  assert.equal(access.canObject(legacy,'risk.resolve',{},'can_resolve'),true);
  assert.equal(access.canObject(legacy,'risk.resolve',{can_resolve:false},'can_resolve'),false);
});
test('risk detail cannot open a write confirmation for a readable colleague object',()=>{
  const s=user('supervisor','workspace');s.permissions['risk.resolve']=true;
  s.permissionGrants.push(grant('risk.resolve','self'));
  const {p}=page('pages/risk-detail/index.js',s);
  p.data.risk={id:'colleague-risk',status:'open',can_resolve:false};p.data.resolutionNote='同事的风险记录';
  // The harness has no showModal: opening confirmation without object permission fails.
  assert.doesNotThrow(()=>p.confirmResolve());assert.equal(p.data.submitting,false);
});
test('changed identities clear visible rows in each page loader first synchronous render',async()=>{
  const cases=[
    ['tasks','taskIdentity',['tasks','filteredTasks'],'onShow'],
    ['risks','_riskIdentity',['risks','filteredRisks'],'loadRisks'],
    ['opportunities','identity',['opportunities','filtered','opportunityGroups'],'loadPage'],
    ['workbench','workbenchContext',['opportunityItems','filteredOpportunities','opportunityGroups'],'loadData'],
    ['customers','mapIdentity',['customers','plotCustomers'],'loadData'],
  ];
  const unavailable=async()=>{throw Error('read still unavailable');};
  for(const [route,identityField,fields,method] of cases){
    const s=user(),{p}=page(`pages/${route}/index.js`,s,{listRisks:unavailable,listOpportunities:unavailable,getDirectoryMembers:unavailable,getCustomerMap:unavailable});
    p[identityField]=access.identity(s);s.permissionVersion='v2';
    p.loadTasks=async()=>{};p.loadAllOpportunities=async()=>{};p.loadAssets=()=>{};
    for(const field of fields)p.data[field]=[{id:'old-scope-row'}];
    let first;
    p.setData=(patch,callback)=>{Object.assign(p.data,patch);if(!first)first=JSON.parse(JSON.stringify(p.data));if(callback)callback();};
    await p[method]();
    for(const field of fields)assert.equal(first[field].length,0,`${route}.${field} first frame`);
  }
});
test('native guard clears detail/list state atomically with flags and preserves same-identity refresh',()=>{
  let definition;const filename=path.resolve(__dirname,'../miniprogram/app.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{App:d=>definition=d,Date,wx:{},require:name=>require(path.resolve(path.dirname(filename),name))});
  definition.globalData.session=user();definition.refreshCapabilities=async()=>{};
  const current=access.identity(definition.globalData.session);
  for(const [route,field,oldValue] of [['tasks','filteredTasks',[{id:'old'}]],['risks','filteredRisks',[{id:'old'}]],['workbench','opportunityItems',[{id:'old'}]],['customers','selectedCustomer',{id:'old'}],['customer-detail','customer',{id:'old'}],['risk-detail','risk',{id:'old'}]]){
    const frames=[],p={_accessIdentity:'old-identity',data:{[field]:oldValue},setData(patch){Object.assign(this.data,patch);frames.push(JSON.parse(JSON.stringify(this.data)));}};
    definition.guardPage(p,route);
    assert.ok(Array.isArray(oldValue)?frames[0][field].length===0:frames[0][field]===null,route);
    assert.equal(p._accessIdentity,current);
    p.data[field]=oldValue;frames.length=0;definition.guardPage(p,route);assert.deepEqual(frames[0][field],oldValue,'same identity preserves current data');
  }
});
test('app refresh replaces same-version scopes, clears old objects, and reloads the current page',async()=>{
  const s={...user(),capabilities:{}},actor={user_id:s.userId,workspace_id:s.workspaceId,role:s.role,
    permissions:s.permissions,permission_grants:s.permissionGrants,permission_version:s.permissionVersion,
    capabilities:{},team_ids:s.teamIds,scope_name:'旧总体范围'};
  let definition,reloads=0,updates=0;
  const active={_accessRoute:'customers',data:{customers:[{id:'old-team-customer'}]},setData(patch){Object.assign(this.data,patch);},onShow(){reloads++;}};
  const api={getCurrentActor:async()=>({actor}),getAuth:()=>({}),updateActor:()=>updates++};
  const filename=path.resolve(__dirname,'../miniprogram/app.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{App:d=>definition=d,Date,getCurrentPages:()=>[active],
    require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),
    wx:{setStorageSync(){},showToast(){},removeStorageSync(){},reLaunch(){}}});
  definition.globalData.session=s;
  const oldIdentity=access.identity(s);
  actor.permission_grants=actor.permission_grants.map(g=>({...g,scope_code:'self',team_ids:[]}));
  await definition.refreshCapabilities(true);
  assert.notEqual(access.identity(definition.globalData.session),oldIdentity);
  assert.equal(access.canViewTeam(definition.globalData.session,'battle_map.read'),false);
  assert.equal(active.data.customers.length,0);assert.equal(active.data.canViewTeam,false);
  assert.equal(reloads,1);assert.equal(updates,1);assert.equal(definition.globalData.session.scope,'按功能授权');
  await definition.refreshCapabilities(true);assert.equal(reloads,1,'an unchanged snapshot does not restart the page');
  actor.client_export_permissions={'visit.export':true,'report.export':false};
  await definition.refreshCapabilities(true);assert.equal(reloads,2);assert.equal(definition.globalData.session.clientExportPermissions['visit.export'],true);
  actor.client_export_permissions={};
  await definition.refreshCapabilities(true);assert.equal(reloads,3);assert.equal(definition.globalData.session.clientExportPermissions['visit.export'],undefined);
  actor.workspace_id='another-workspace';
  await assert.rejects(definition.refreshCapabilities(true),/账号身份已变化/);
  assert.equal(definition.globalData.session.workspaceId,'w1');
});


test('workbench scope picker exposes only self and authorized scope, with no duplicate placeholder',()=>{
 const template=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/workbench/index.wxml'),'utf8');
 const tag=template.match(/<sb-labeled-select[^>]*bindchange="changeExecutionScope"[^>]*\/>/)[0];
 const allOption=tag.includes('allOption="{{false}}"')?false:true;
 const wxs={exports:{}};vm.runInNewContext(template.match(/<wxs module="opt">([\s\S]*?)<\/wxs>/)[1],{module:wxs});
 for(const [role,scope] of [['sales','teams'],['supervisor','workspace'],['fde','teams'],['fde_lead','workspace']]){
  const {p}=page('pages/workbench/index.js',user(role,scope));
  const {p:select}=page('ui/sb/sb-labeled-select/index.js',user(role,scope));
  Object.assign(select.data,{value:0,options:wxs.exports.idx(p.data.executionScopeOptions,'label'),placeholder:'我的',allOption});
  select.observers['value, options, placeholder, allOption, defaultValue'].call(select,0,select.data.options,'我的',allOption,null);
  assert.deepEqual(Array.from(select.data.columns,x=>[x.value,x.label]),[[0,'我的'],[1,'授权范围']]);
  let requested;p.data.canViewTeam=true;p.applyOpportunityFilters=()=>{requested=p.opportunityParams();};
  select.triggerEvent=(name,detail)=>p.changeExecutionScope({detail});
  for(const index of [1,0]){
   select.onOpen();select.onConfirm({detail:{value:[index]},currentTarget:{dataset:{pickerEpoch:select.data.pickerEpoch}}});
   assert.equal(requested.scope,index===0?'self':'team');
   assert.equal(requested.ownerId,null);assert.equal(requested.teamId,'all');
  }
  for(const oldClearValue of [null,'',0]){p.changeExecutionScope({detail:{value:oldClearValue}});assert.equal(requested.scope,'self');}
 }
 const {p}=page('pages/workbench/index.js',user('sales','self'));
 p.applyOpportunityFilters=()=>{throw Error('cannot widen scope');};
 p.changeExecutionScope({detail:{value:1}});assert.equal(p.opportunityParams().scope,'self');
});
