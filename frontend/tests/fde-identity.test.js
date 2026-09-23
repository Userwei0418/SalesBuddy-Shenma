require('./helpers/business-options');
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const base=path.resolve(__dirname,'../miniprogram');
const access=require('../miniprogram/utils/access');
const opportunity=require('../miniprogram/utils/opportunity');
function make(relative,{api={},app,kind='Page',wx={}}={}){
 let definition;
 const filename=path.join(base,relative);
 const context={require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),Date,Map,Set,Promise,console,setTimeout,clearTimeout,setInterval,clearInterval,getApp:()=>app,wx:{showToast(){},setNavigationBarTitle(){},...wx}};
 context[kind]=d=>{definition=d;};vm.runInNewContext(fs.readFileSync(filename,'utf8'),context);
 const instance={...definition,...definition.methods,data:JSON.parse(JSON.stringify(definition.data||{})),properties:{},setData(values,cb){for(const [key,value]of Object.entries(values)){const bits=key.split('.');let node=this.data;bits.slice(0,-1).forEach(b=>node=node[b]||(node[b]={}));node[bits[bits.length-1]]=value;}if(cb)cb();}};
 return instance;
}
const fde={role:'fde',userId:'u1',workspaceId:'w1',userName:'成员一',account:'FDE01',team:'FDE部门',capabilities:{'customer.read':true,'opportunity.read':true,'task.create':true,'task.respond':true},permissionVersion:'v1'};
const appFor=session=>({globalData:{session,role:session.role,roles:{fde:{name:'FDE',scope:'本人项目'},fde_lead:{name:'FDE主管',scope:'团队项目'}}},can:key=>access.can(session,key),ensureLogin:()=>true});

test('未下发能力时FDE不自行放行，负责人不能因非销售身份获得经理能力',()=>{
 for(const role of ['fde','fde_lead']){
  const s={...fde,role};
  for(const key of ['customer.create','customer.claim','customer.edit','opportunity.edit','visit.create','visit.supplement','actual.manage','advice.decide','risk.resolve','console.access'])assert.equal(access.can(s,key),false,`${role}:${key}`);
  for(const route of ['customer-create','customer-edit','customer-claim','opportunity-create','visit-entry','visit-confirm','customer-assign-confirm'])assert.equal(access.pageAllowed(s,route),false,`${role}:${route}`);
  assert.equal(access.pageAllowed(s,'management-task-create'),true);
 }
 assert.equal(access.can({role:'fde_lead'},'team.view'),false);
 assert.equal(access.can({...fde,capabilities:{'visit.create':true}},'visit.create'),true);
 assert.equal(access.pageAllowed({...fde,capabilities:{'visit.create':true}},'visit-confirm',{visitId:'others'}),false);
});

test('全部业务页均在入口读取集中能力，旧深链不能绕过页面守卫',()=>{
 const pages=JSON.parse(fs.readFileSync(path.join(base,'app.json'),'utf8')).pages;
 assert.ok(pages.length >= 24);
 for(const page of pages.filter(p=>!p.includes('/login/'))){const js=fs.readFileSync(path.join(base,page+'.js'),'utf8');const wxml=fs.readFileSync(path.join(base,page+'.wxml'),'utf8');assert.match(js,/guardPage/);assert.match(wxml,/accessBlocked/);}
 const detail=fs.readFileSync(path.join(base,'pages/customer-detail/index.wxml'),'utf8');assert.match(detail,/canRecordVisit[\s\S]*语音记录拜访/);
});

test('FDE看板和我的不请求销售画像或排名，负责人个人页仍是本人',()=>{
 const app=appFor({...fde,role:'fde_lead'});let forbidden=0;
 const api=new Proxy({}, {get(_,key){if(key==='getProfileScopeOptions'||key.startsWith('getFde'))return async()=>({members:[],teams:[],summary:{}});return ()=>{forbidden++;throw Error('sales endpoint called');};}});
 for(const route of ['bi','profile','workbench']){
   const page=make(`pages/${route}/index.js`,{api,app});page.onShow();assert.equal(page.data.isFde,true);
 }
 assert.equal(forbidden,0);
 const profile=fs.readFileSync(path.join(base,'pages/profile/index.wxml'),'utf8');assert.match(profile,/<fde-profile/);assert.doesNotMatch(profile,/<fde-dashboard/);
});

test('成员详情不允许普通FDE查看其他成员或落入销售评分',()=>{
 const page=make('pages/member-growth/index.js',{app:appFor(fde)});page.onLoad({member_id:'u2'});assert.equal(page.data.accessBlocked,true);
 const own=make('pages/member-growth/index.js',{app:appFor(fde)});own.onLoad({member_id:'u1'});assert.equal(own.data.isFde,true);assert.equal(own.data.fdeMemberId,'u1');
});

test('页面守卫首次挂载FDE组件时已有完整字符串与成员钻取上下文',()=>{
 const cases=[
  {route:'workbench',event:'onShow',options:undefined,expected:{fdeScope:'',fdeMemberId:'',fdeCustomerId:''}},
  {route:'opportunities',event:'onLoad',options:{scope:'team',member_id:'u2',customer_id:'c1'},expected:{fdeScope:'team',fdeMemberId:'u2',fdeCustomerId:'c1'}},
  {route:'opportunities',event:'onLoad',options:{},expected:{fdeScope:'',fdeMemberId:'',fdeCustomerId:''}},
  {route:'member-growth',event:'onLoad',options:{member_id:'u2'},expected:{fdeMemberId:'u2'}},
  {route:'member-growth',event:'onLoad',options:{},expected:{fdeMemberId:'u1'}},
 ];
 for(const scenario of cases){
  const app=appFor({...fde,role:'fde_lead'});let mounted=0;
  app.guardPage=(page)=>{
   page.setData({isFde:true});mounted++;
   for(const [key,value]of Object.entries(scenario.expected))assert.equal(page.data[key],value,`${scenario.route} first mount ${key}`);
   return true;
  };
  const page=make(`pages/${scenario.route}/index.js`,{app});
  for(const key of Object.keys(scenario.expected))assert.equal(typeof page.data[key],'string',`${scenario.route} initial ${key}`);
  page[scenario.event](scenario.options);assert.equal(mounted,1);
 }
});

test('FDE多选跨搜索保留已选，取消不发变更，确认按用户ID去重',async()=>{
 const changes=[];const picker=make('components/fde-picker/index.js',{kind:'Component',api:{listFdeMembers:async()=>({items:[{id:'u2',name:'同名成员',team:'FDE二组'}],total:1})},app:appFor(fde)});
 picker.properties={selected:[{id:'u1',name:'同名成员',team:'FDE一组'}],excludedIds:[],disabled:false};picker.triggerEvent=(name,data)=>changes.push({name,data});
 picker.open();await new Promise(r=>setImmediate(r));picker.toggle({currentTarget:{dataset:{id:'u2'}}});assert.equal(picker.data.draft.length,2);
 picker.cancel();assert.equal(changes.length,0);assert.equal(picker.properties.selected.length,1);
 picker.open();await new Promise(r=>setImmediate(r));picker.toggle({currentTarget:{dataset:{id:'u2'}}});picker.confirm();assert.deepEqual(Array.from(changes[0].data.memberIds),['u1','u2']);
});

test('多选目录失败不把已有成员清空，普通协同人不能再次作为FDE选择',async()=>{
 const picker=make('components/fde-picker/index.js',{kind:'Component',api:{listFdeMembers:async()=>{throw Error('directory offline');}}});
 picker.properties={selected:[{id:'u1',name:'甲'}],excludedIds:['u2'],disabled:false};picker.triggerEvent=()=>{};
 picker.open();await new Promise(r=>setImmediate(r));assert.equal(picker.data.draft.length,1);assert.match(picker.data.error,/offline/);
 picker.setData({items:[{id:'u2',name:'乙'}]});picker.toggle({currentTarget:{dataset:{id:'u2'}}});assert.equal(picker.data.draft.length,1);
});

test('商机名单未载入时不清空已有关系；明确空数组才请求清空',()=>{
 const row={id:'o1',name:'项目',amount:100000,probability:10,status:'open',expected_close_date:'2026-12-01',sales_channel:'direct',version_no:2};
 const form=opportunity.formFor(row);assert.equal(Object.hasOwn(opportunity.payload(form,row),'fde_member_ids'),false);
 form.fde_member_ids=[];assert.deepEqual(opportunity.payload(form,row).fde_member_ids,[]);
 form.fde_member_ids=['u1','u1','u2'];assert.deepEqual(opportunity.payload(form,row).fde_member_ids,['u1','u2']);
});

test('FDE录入只能选择本人参与的商机，切换关联不会进入销售编辑表单',()=>{
 const page=make('pages/visit-confirm/index.js',{app:appFor(fde),wx:{setStorageSync(){}}});page.refresh=()=>{};page.persist=()=>{};
 page.setData({canEditOpportunity:false});
 page.fdeOpportunityChanged({detail:{opportunity:{id:'o1',name:'项目一'},verified:true}});
 assert.equal(page.data.opportunityId,'o1');assert.equal(page.data.fdeOpportunityVerified,true);assert.equal(page.data.opportunityEditing,false);
 page.fdeOpportunityChanged({detail:{cleared:true,verified:false}});
 assert.equal(page.data.opportunityId,'');assert.equal(page.data.fdeOpportunityVerified,false);
});

test('FDE加入/移出是协作通知，不生成待接受任务卡片',()=>{
 const page=make('pages/index/index.js',{app:appFor(fde)});
 for(const template_code of ['fde_joined','fde_removed']){
  const msg=page.buildRemoteNotificationMessage({id:'n1',template_code,title:'协作变更',payload:{membership_opportunity_id:'o1'}},fde);
  assert.equal(msg.card.eyebrow,'协作关系');assert.equal(msg.card.metrics.length,0);assert.equal(msg.card.action.code,'open_fde_membership');assert.equal(msg.card.action.taskId,undefined);
 }
});

test('待交接任务不能接受或完成，保留对象级协调入口',async()=>{
 const task={id:'11111111-1111-1111-1111-111111111111',status:'pending_execution',handover_required:true,can_coordinate:true,assignees:[{user_id:'u1',responsibility:'owner',name:'甲'}]};
 const page=make('pages/task-detail/index.js',{app:appFor(fde),api:{getTask:async()=>task}});page.data.taskId=task.id;page.loadTask();await new Promise(r=>setImmediate(r));
 assert.equal(page.data.task.canRespond,false);assert.equal(page.data.task.canComplete,false);assert.equal(page.data.task.can_coordinate,true);
});

test('协作记录失去完整资料权限后只展示摘要，不打开旧深链',()=>{
 const opened=[];const page=make('components/fde-dashboard/index.js',{kind:'Component',app:appFor(fde),wx:{navigateTo:r=>opened.push(r.url)}});
 page.setData({recentVisits:[{id:'v1',customer_id:'c1',can_read_detail:false}],activity:[]});page.openVisit({currentTarget:{dataset:{id:'v1'}}});assert.equal(opened.length,0);
 page.data.recentVisits[0].can_read_detail=true;page.openVisit({currentTarget:{dataset:{id:'v1'}}});assert.match(opened[0],/visit-detail/);
});

test('FDE看板和我的可读取伙伴历史，空客户ID不被拼为null或undefined',()=>{
 for(const name of ['fde-dashboard','fde-profile']){
  const opened=[];const page=make(`components/${name}/index.js`,{kind:'Component',app:appFor(fde),wx:{navigateTo:r=>opened.push(r.url)}});
  const rows=[{id:'partner',customer_id:null,partner_id:'p',can_read_detail:true},{id:'legacy',can_read_detail:true}];
  page.setData({records:rows,recentVisits:rows,activity:[]});
  for(const id of ['partner','legacy'])page.openVisit({currentTarget:{dataset:{id}}});
  assert.deepEqual(opened,['/pages/visit-detail/index?visit_id=partner','/pages/visit-detail/index?visit_id=legacy']);
 }
});

test('协作接口失败与合法零数据区分，团队汇总使用服务端去重结果',async()=>{
 const app=appFor({...fde,role:'fde_lead'});const page=make('components/fde-dashboard/index.js',{kind:'Component',app,api:{getFdeDashboard:async()=>({data_source:'database',summary:{open_acv:1000000,period_visits:1,period_visit_people:2,recognized_amount:null,collection_amount:0},ranking:[{user_id:'u1',open_acv:1000000},{user_id:'u2',open_acv:1000000}],recent_visits:[],members:[]})}});
 page.properties={};page.data.year=2026;await page.load();assert.equal(page.data.summary.openAcvText,'100');assert.equal(page.data.summary.period_visits,1);assert.equal(page.data.summary.period_visit_people,2);assert.equal(page.data.summary.recognizedText,'未登记');assert.equal(page.data.summary.collectionText,'0');
 const failed=make('components/fde-dashboard/index.js',{kind:'Component',app,api:{getFdeDashboard:async()=>{throw Error('offline');}}});failed.properties={};await failed.load();assert.equal(failed.data.ready,false);assert.equal(failed.data.summary,null);assert.match(failed.data.error,/offline/);
});

test('负责人只能增删本部门名单，跨部门已有成员作为只读保留',()=>{
 const picker=make('components/fde-picker/index.js',{kind:'Component'});
 picker.properties={selected:[{id:'u2',name:'乙',team_id:'team-b'}],restrictToTeams:true,allowedTeamIds:['team-a'],excludedIds:[],disabled:false};
 picker.setData({draft:picker.properties.selected,items:[{id:'u1',name:'甲',team_id:'team-a'},{id:'u2',name:'乙',team_id:'team-b'},{id:'u3',name:'丙',team_id:'team-b'}]});picker.decorate();
 picker.toggle({currentTarget:{dataset:{id:'u2'}}});picker.toggle({currentTarget:{dataset:{id:'u3'}}});assert.deepEqual(Array.from(picker.data.draft,r=>r.id),['u2']);assert.equal(picker.data.selectedRows[0].locked,true);
 picker.toggle({currentTarget:{dataset:{id:'u1'}}});assert.deepEqual(Array.from(picker.data.draft,r=>r.id),['u2','u1']);
 picker.properties.restrictToTeams=false;picker.toggle({currentTarget:{dataset:{id:'u3'}}});assert.equal(picker.data.draft.length,3);
});

test('主动取消和拒绝在任务详情与消息中区分，取消不提供拒绝重发',async()=>{
 const task={id:'11111111-1111-1111-1111-111111111111',status:'cancelled',last_event_type:'cancel',last_event_note:'项目结束，终止执行',creator_user_ref_id:'u1',assignees:[{user_id:'u1',responsibility:'owner'}]};
 const page=make('pages/task-detail/index.js',{app:appFor(fde),api:{getTask:async()=>task}});page.data.taskId=task.id;page.loadTask();await new Promise(r=>setImmediate(r));
 assert.equal(page.data.task.statusLabel,'已取消');assert.equal(page.data.task.cancellationNote,task.last_event_note);assert.equal(page.data.task.canRetry,false);
 const home=make('pages/index/index.js',{app:appFor(fde)});const msg=home.buildRemoteNotificationMessage({id:'n',template_code:'task_assigned',object_id:task.id,payload:{}},fde);assert.equal(home.reconcileTaskCard(msg,[task]).card.metrics[0].value,'已取消');
 const noCapabilities=make('pages/task-detail/index.js',{app:appFor({...fde,capabilities:null}),api:{getTask:async()=>({...task,status:'pending_confirm',last_event_type:'created'})}});noCapabilities.data.taskId=task.id;noCapabilities.loadTask();await new Promise(r=>setImmediate(r));assert.equal(noCapabilities.data.task.canRespond,false);
});

test('协同归档消息不是待办，打开完整拜访前重新验证权限',async()=>{
 const opened=[],modals=[];let reads=0;
 const page=make('pages/index/index.js',{app:appFor(fde),api:{getVisit:async()=>{reads++;throw Error('已无完整资料权限');}},wx:{navigateTo:r=>opened.push(r.url),showModal:r=>modals.push(r)}});
 const msg=page.buildRemoteNotificationMessage({id:'n',template_code:'visit_archived',object_id:'v1',title:'协同归档',payload:{}},fde);assert.equal(msg.card.action.code,'open_fde_visit');assert.equal(msg.card.metrics.length,0);assert.equal(msg.card.action.taskId,undefined);
 page.handleCardAction({currentTarget:{dataset:{action:msg.card.action.code,visitId:'v1'}}});await new Promise(r=>setImmediate(r));assert.equal(reads,1);assert.equal(opened.length,0);assert.match(modals[0].content,/权限/);
});

test('协同历史分页响应在权限版本改变后不回填旧数据',async()=>{
 const app=appFor({...fde});let deliver;
 const page=make('components/fde-dashboard/index.js',{kind:'Component',app,api:{getFdeActivity:()=>new Promise(r=>deliver=r)}});page.properties={};
 const pending=page.loadActivity();app.globalData.session={...app.globalData.session,permissionVersion:'v2'};deliver({items:[{id:'v1'}],total:1,has_more:false,next_offset:null});await pending;assert.equal(page.data.activity.length,0);
});

test('FDE接口使用重复季度参数，地图与资产保留scope/member_id，名单更新独立带版本',async()=>{
 const storage=new Map(),requests=[];
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{if(o.url.endsWith('/metadata/business-options')){o.success({statusCode:200,data:require('./helpers/business-options.json')});return;}requests.push(o);o.success({statusCode:200,data:{}});}};
 delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');api.saveAuth({access_token:'test-only-fde',actor:{workspace_id:'w',user_id:'u'}});
 const params={scope:'team',member_id:'person-id',year:2026,quarters:[1,3]};await api.getFdeDashboard(params);await api.getFdeActivity({...params,offset:50,limit:50});
 for(const req of requests){const url=new URL(req.url);assert.deepEqual(url.searchParams.getAll('quarters'),['1','3']);assert.equal(url.searchParams.get('scope'),'team');assert.equal(url.searchParams.get('member_id'),'person-id');}
 await api.getCustomerMap({scope:'self',member_id:'u'});await api.getCustomerAssets({scope:'self',member_id:'u'});for(const req of requests.slice(2)){const url=new URL(req.url);assert.equal(url.searchParams.get('scope'),'self');assert.equal(url.searchParams.get('member_id'),'u');}
 await api.updateFdeMembers('op1',['u'],7);assert.equal(requests[4].method,'PUT');assert.deepEqual(requests[4].data,{member_ids:['u'],version_no:7});assert.ok(requests[4].header['Idempotency-Key']);
 delete global.wx;
});

test('负责人钻取其他成员的商机固定成员上下文，主列表通过人员筛选收窄',async()=>{
 const queries=[];
 const app=appFor({...fde,role:'fde_lead',capabilities:{...fde.capabilities,'team.view':true}});
 const page=make('components/fde-projects/index.js',{kind:'Component',app,api:{listOpportunities:async params=>{queries.push(params);return require('./helpers/opportunity-pages')([],params);},getDirectoryMembers:async()=>({items:[{id:'u2',name:'成员乙',role:'fde'}]})}});
 page.properties={memberId:'u2',customerId:'',initialScope:'self'};page.setData({scope:'self',year:2026});
 await page.load();assert.equal(queries.length,1);assert.equal(queries[0].scope,'team');assert.equal(queries[0].memberId,'u2');assert.equal(page.data.memberContextLabel,'成员乙');
 assert.equal(typeof page.scope,'undefined');assert.equal(page.data.scope,'team');
 page.properties.memberId='';await page.member({detail:{ids:['u2']}});assert.equal(queries[1].scope,'team');assert.ok(!queries[1].memberId);assert.deepEqual(Array.from(queries[1].memberIds),['u2']);
});
