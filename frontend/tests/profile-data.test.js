const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const currentDate=new Date(Date.now()+28800000);
const year=currentDate.getUTCFullYear(),quarter=Math.floor(currentDate.getUTCMonth()/3)+1;
function harness(relative,api={}){
 let definition;const session={userId:'self-id',role:'supervisor',account:'SELF',userName:'本人',workspaceId:'workspace',permissionVersion:'1'},app={globalData:{session}},toasts=[];
 const filename=path.resolve(__dirname,'../miniprogram',relative);
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:value=>definition=value,Component:value=>definition=value,require:name=>name.endsWith('/apiClient')?api:require(path.resolve(path.dirname(filename),name)),getApp:()=>app,Date,setTimeout,clearTimeout,wx:{showToast:value=>toasts.push(value),nextTick:fn=>fn()}});
 const instance={...definition,...definition.methods,data:JSON.parse(JSON.stringify(definition.data)),properties:{},visible:true,active:true,setData(data,done){Object.assign(this.data,data);if(done)done();},triggerEvent(){}};
 return {instance,app,toasts};
}
function performance(extra={}){return {data_source:'database',editable:true,targets:{collection:1000000,recognized:2000000},actuals:{collection:250000,recognized:800000},target_period:{type:'quarter',year,quarter},supplementals:{customers:60,opportunities:40,followup:88},scores:{maturity:{value:35,text:'35'},efficiency:{value:70,text:'70'}},efficiency:{customers:{numerator:12,denominator:20,evaluated_count:15,pending_count:5,rate:60,status:'partial'},opportunities:{numerator:4,denominator:10,evaluated_count:10,pending_count:0,rate:40,status:'ready'},followup:{count:7,customer_count:3,average_score:88}},windows:{structure:{period:'current'},followup:{start:'2026-09-14',end:'2026-09-20'}},...extra};}
function page(api={}){const h=harness('pages/profile/index.js',api);Object.assign(h.instance.data,{role:'sales',targetYear:year,targetQuarter:quarter,profileScopeLabel:'本人'});return h;}

test('团队完全遵循后端目录，名称不触发隐藏，失效ID不能继续查询',async()=>{
 const teams=[{id:'leaf',name:'生态渠道'},{id:'north',name:'北区'},{id:'empty',name:'新业务组'}];
 for(const [current,visible,expected] of [['leaf',teams,'leaf'],['gone',teams,'north'],['leaf',[], '']]){
  const {instance:p}=page({getProfileScopeOptions:async()=>({data_source:'database',teams:visible,members:[{id:'self-id',name:'本人',team_ids:['leaf','north']}],allowed_scopes:['self','team','department'],defaults:{scope:'team',team_id:'north',member_id:'self-id'}})});
  Object.assign(p.data,{role:'manager',profileTeamId:current,profileScopeMode:'team'});let selected;
  p.refreshProfileScope=()=>selected=p.data.profileTeamId;
  await p.loadProfileDirectory();assert.equal(p.data.directoryError,'');assert.equal(selected,expected);
  assert.deepEqual(Array.from(p.data.scopeTeams,row=>row.id),visible.map(row=>row.id));
 }
});

test('季度实绩除以生效目标，金额来自服务端，超过目标只封顶进度条',async()=>{
 const {instance:p}=page({getProfilePerformance:async()=>performance()});await p.loadProfilePerformance();
 assert.equal(p.data.performanceBoard[0].completedText,'¥250,000');assert.equal(p.data.performanceBoard[0].rateText,'完成率 25.0%');assert.equal(p.data.performanceBoard[0].progress,25);assert.match(p.data.targetScopeLabel,/Q[1-4]/);
 const metric=require('../miniprogram/utils/profileMetrics').performanceMetric({targets:{collection:100}},'collection',120);assert.equal(metric.rateText,'完成率 120.0%');assert.equal(metric.progress,100);
});

test('比例主卡显示分子分母及待评估，跟进使用服务端总量',async()=>{
 const {instance:p}=page({getProfilePerformance:async()=>performance()});await p.loadProfilePerformance();
 assert.equal(p.data.efficiencyRatio.value,'60.0%');assert.equal(p.data.efficiencyRatio.detail,'第一、二象限 12 / 20 家客户');assert.equal(p.data.efficiencyRatio.coverage,'待评估 5 家客户');
 p.selectEfficiencyMetric({currentTarget:{dataset:{metric:'opportunities'}}});assert.equal(p.data.efficiencyRatio.value,'40.0%');assert.equal(p.data.efficiencyRanking.length,0);
 p.selectEfficiencyMetric({currentTarget:{dataset:{metric:'followup'}}});assert.equal(p.data.efficiencyTotal,7);assert.equal(p.data.efficiencySecondaryValue,'88.0分');
});

test('空分母和全待评估不显示0%，不从列表推算比例',async()=>{
 const {instance:p}=page({getProfilePerformance:async()=>performance({efficiency:{customers:{status:'empty',rate:null,numerator:0,denominator:0}}})});await p.loadProfilePerformance();assert.equal(p.data.efficiencyRatio.value,'暂无数据');
 p.profilePerformance.efficiency.customers={status:'pending',rate:null,denominator:3,numerator:0,pending_count:3};p.applyEfficiencySupplemental();assert.equal(p.data.efficiencyRatio.value,'待评估');
});

test('结构与跟进时间独立，切换页签保留之前的筛选',async()=>{
 const queries=[];const {instance:p}=page({getProfilePerformance:async query=>{queries.push(query);return performance();}});
 await p.loadProfilePerformance();await p.selectEfficiencyPeriod({currentTarget:{dataset:{period:'year'}}});
 p.selectEfficiencyMetric({currentTarget:{dataset:{metric:'followup'}}});assert.equal(p.data.efficiencyPeriod,'week');await p.selectEfficiencyPeriod({currentTarget:{dataset:{period:'quarter'}}});
 assert.equal(queries.at(-1).structure_period,'year');assert.equal(queries.at(-1).period,'quarter');assert.equal(queries.at(-1).quarter,quarter);
 p.selectEfficiencyMetric({currentTarget:{dataset:{metric:'customers'}}});assert.equal(p.data.efficiencyPeriod,'year');
});

test('成员和团队使用真实ID，选择他人后即使editable错误也不显示编辑',async()=>{
 const queries=[];const {instance:p}=page({getProfilePerformance:async query=>{queries.push(query);return performance();}});
 Object.assign(p.data,{role:'supervisor',profileScopeMode:'person',profileSelectedMemberId:'member-2'});await p.loadProfilePerformance();assert.equal(queries.at(-1).member_id,'member-2');assert.equal(queries.at(-1).account_code,undefined);assert.equal(p.data.canEditSalesTarget,false);
 p.data.profileSelectedMemberId='self-id';await p.loadProfilePerformance();assert.equal(p.data.canEditSalesTarget,true);
 Object.assign(p.data,{profileScopeMode:'team',profileTeamId:'team-1'});await p.loadProfilePerformance();assert.equal(queries.at(-1).team_id,'team-1');assert.equal(p.data.canEditSalesTarget,false);
});

test('慢响应在切人、切账号和隐藏页面后都不能污染新状态',async()=>{
 const pending=[];const {instance:p,app}=page({getProfilePerformance:()=>new Promise(resolve=>pending.push(resolve))});
 Object.assign(p.data,{role:'supervisor',profileScopeMode:'person',profileSelectedMemberId:'member-a'});const old=p.loadProfilePerformance();p.data.profileSelectedMemberId='member-b';const latest=p.loadProfilePerformance();pending[1](performance({actuals:{collection:222,recognized:333}}));await latest;pending[0](performance());await old;assert.equal(p.data.performanceBoard[0].completedText,'¥222');
 const swapped=p.loadProfilePerformance();app.globalData.session.userId='other-login';pending[2](performance());await swapped;assert.equal(p.data.performanceBoard[0].completedText,'¥222');
 const hidden=p.loadProfilePerformance();p.onHide();pending[3](performance());await hidden;assert.equal(p.data.performanceBoard[0].completedText,'¥222');
});

test('失败与无数据显示区分，旧结果不能继续伪装有效',async()=>{
 let fail=false;const {instance:p}=page({getProfilePerformance:async()=>{if(fail)throw Error('网络异常');return performance();}});await p.loadProfilePerformance();fail=true;await p.loadProfilePerformance();assert.equal(p.data.organizationReady,false);assert.equal(p.data.performanceError,'网络异常');assert.equal(p.data.performanceBoard[0].targetText,'未设置');assert.equal(p.data.maturityScore,'--');
});

test('画像读取与经营数据采用同一稳定主体，总经理本人不触发复盘',async()=>{
 const queries=[];const {instance:p}=page({getScopedSalesGrowth:async query=>{queries.push(query);return {data_source:'database',latest:null};}});Object.assign(p.data,{role:'manager',profileScopeMode:'person',profileSelectedMemberId:'member-2'});await p.loadScopedGrowth();assert.equal(queries[0].member_id,'member-2');
 p.data.profileNotApplicable=true;await p.loadScopedGrowth();assert.equal(queries.length,1);assert.match(p.data.growthStatusText,/不适用/);
});

function target(api={}){const h=harness('components/quarter-target/index.js',api);h.instance.properties={scope:'self',year,quarter,editable:true,subjectLabel:'本人',subjectId:'',contextKey:'self'};return h;}
const rows=[{kind:'collection',amount:1000000,version_no:2},{kind:'recognized',amount:2000000,version_no:3}];

test('首次双目标一次提交，万元转换人民币元并携带原因',async()=>{
 let sent;const {instance:c}=target({getTargets:async()=>({items:[],editable:true}),saveTargetBatch:async body=>{sent=body;return {status:'effective'};}});await c.load();c.show();Object.assign(c.data,{collection:'100',recognized:'200.000001',reason:'与负责人确认季度计划'});await c.save();assert.equal(sent.items[0].amount,'1000000');assert.equal(sent.items[1].amount,'2000000.01');assert.equal(sent.items[0].version_no,null);assert.equal(sent.period_type,'quarter');assert.equal(sent.reason,'与负责人确认季度计划');assert.equal(c.data.open,false);
});

test('已有目标携带基准版本，审批提交不写成本地生效值',async()=>{
 let body;const {instance:c,toasts}=target({getTargets:async()=>({items:rows,editable:true}),saveTargetBatch:async value=>{body=value;return {status:'pending'};}});await c.load();c.show();assert.equal(c.data.collection,'100');Object.assign(c.data,{collection:'80',recognized:'200',reason:'客户延期，已重新确认'});await c.save();assert.equal(body.items[0].version_no,2);assert.equal(body.items[1].version_no,3);assert.equal(c.data.rows[0].amount,1000000);assert.equal(toasts[0].title,'已提交运营审批');
});

test('原因必填且非法金额不发请求，失败保留表单可修改',async()=>{
 let calls=0;const {instance:c}=target({getTargets:async()=>({items:rows,editable:true}),saveTargetBatch:async()=>{calls++;throw Error('版本已更新，请重新读取');}});await c.load();c.show();await c.save();assert.equal(calls,0);assert.match(c.data.error,/依据|原因/);Object.assign(c.data,{collection:'0',reason:'调整'});await c.save();assert.equal(calls,0);c.data.collection='90';await c.save();assert.equal(calls,1);assert.equal(c.data.open,true);assert.match(c.data.error,/版本已更新/);
});

test('待审显示当前及申请值并阻止覆盖；他人和历史季度只读',async()=>{
 const {instance:c}=target({getTargets:async()=>({items:rows,editable:true,pending_batches:[{items:[{kind:'collection',previous_amount:1000000,proposed_amount:800000}]}]})});await c.load();c.show();assert.equal(c.data.open,false);assert.equal(c.data.rows[0].proposedText,'¥800,000');
 c.properties.subjectId='someone';c.properties.scope='person';await c.load();assert.equal(c.data.canEdit,false);c.properties.scope='self';c.properties.year=year-1;await c.load();assert.equal(c.data.canEdit,false);
});

test('表单打开后切账号或主体，旧表单不能提交',async()=>{
 let calls=0;const {instance:c,app}=target({getTargets:async()=>({items:rows,editable:true}),saveTargetBatch:async()=>{calls++;}});await c.load();c.show();c.data.reason='说明';app.globalData.session.userId='other';await c.save();assert.equal(calls,0);app.globalData.session.userId='self-id';c.properties.contextKey='new-subject';await c.save();assert.equal(calls,0);
});

test('主体选择器只返回目录中的ID，搜索与团队分组不产生全员个人选项',()=>{
 const {instance:c}=harness('components/profile-scope-picker/index.js');const events=[];c.triggerEvent=(name,event)=>events.push(event);c.properties={mode:'person',modes:[{value:'person'}],members:[{id:'1',name:'张三',team_ids:['a']},{id:'2',name:'李四',team_ids:['b']}],teams:[]};c.setData({selecting:'person'});c.select({currentTarget:{dataset:{id:'forged'}}});assert.equal(events.length,0);c.teamFilter({currentTarget:{dataset:{id:'a'}}});assert.equal(c.data.visibleMembers.length,1);c.search({detail:{value:'李'}});assert.equal(c.data.visibleMembers.length,0);c.select({currentTarget:{dataset:{id:'1'}}});assert.equal(events[0].id,'1');
});

test('主管从团队切个人，经选择器选择成员后经营数据与画像同时查询该成员UUID',async()=>{
 const memberId='bc98f57b-16f9-4bcc-813c-b4511d41f8fd',teamId='a993c0be-06ab-474c-9d22-11d054583b10',queries=[],growth=[];
 const {instance:p}=page({getProfileScopeOptions:async()=>({data_source:'database',allowed_scopes:['self','person','team'],teams:[{id:teamId,name:'南区团队'}],members:[{user_id:'self-id',display_name:'郭强健',role:'supervisor',team_ids:[teamId]},{user_id:memberId,account_code:'XS002',display_name:'王新源',role:'sales',team_ids:[teamId]}],defaults:{scope:'team',member_id:'self-id',team_id:teamId}}),getProfilePerformance:async query=>{queries.push(query);return performance();},getScopedSalesGrowth:async query=>{growth.push(query);return {data_source:'database',latest:null};}});
 p.sync();await p.loadProfileDirectory();await tick();assert.equal(p.data.profileScopeMode,'team');assert.equal(queries.at(-1).team_id,teamId);
 const {instance:c}=harness('components/profile-scope-picker/index.js');
 c.properties={modes:p.data.profileScopeOptions,mode:p.data.profileScopeMode,members:p.data.scopeMembers,teams:p.data.scopeTeams};
 c.triggerEvent=(name,detail)=>name==='modechange'?p.selectProfileScope({detail}):p.changeProfileSubject({detail});
 c.switchMode({currentTarget:{dataset:{mode:'person'}}});await tick();assert.equal(p.data.profileScopeMode,'person');assert.equal(queries.at(-1).member_id,'self-id');
 c.properties.mode=p.data.profileScopeMode;c.show();assert.equal(c.data.open,true);assert.equal(c.data.selecting,'person');assert.equal(c.data.visibleMembers.length,2);
 c.select({currentTarget:{dataset:{id:memberId}}});await tick();assert.equal(c.data.open,false);assert.equal(p.data.profileSelectedMemberId,memberId);assert.equal(p.data.profileScopeLabel,'王新源');assert.equal(queries.at(-1).member_id,memberId);assert.equal(growth.at(-1).member_id,memberId);assert.equal(queries.at(-1).account_code,undefined);assert.equal(p.data.canEditSalesTarget,false);
 for(const tab of ['maturity','efficiency','profile']){p.selectProfileTab({currentTarget:{dataset:{tab}}});assert.equal(p.data.profileSelectedMemberId,memberId);assert.equal(p.data.targetQueryMemberId,memberId);}
 const before=queries.length;c.switchMode({currentTarget:{dataset:{mode:'department'}}});c.select({currentTarget:{dataset:{id:'not-in-directory'}}});assert.equal(queries.length,before);assert.equal(p.data.profileScopeMode,'person');assert.equal(p.data.profileSelectedMemberId,memberId);
});

test('FDE主管共用选择器事件，成员UUID和本人转换正确且失去团队权限不能切他人',async()=>{
 const memberId='cfa33c15-e7ab-458a-8750-3e8887a0a763',teamId='2916fc3c-45f3-42bb-a33b-5b0f1688d547',queries=[];
 const {instance:p,app}=page({getFdeScopeOptions:async()=>({data_source:'database',teams:[{id:teamId,name:'FDE 南区'}],members:[{user_id:'self-id',display_name:'负责人',team_ids:[teamId]},{user_id:memberId,display_name:'成员乙',team_ids:[teamId]}],defaults:{scope:'self',member_id:'self-id',team_id:teamId}}),getFdeDashboard:async query=>{queries.push(query);return {data_source:'database',summary:{period_opportunities:2},company_rankings:{data_source:'database',items:[]}};}});
 Object.assign(app.globalData.session,{role:'fde_lead',capabilities:{'team.view':true}});p.sync();Object.assign(p.data,{isFde:true,isFdeLead:true,efficiencyMetric:'opportunities',efficiencyPeriod:'week'});await p.loadFdeMembers();
 const {instance:c}=harness('components/profile-scope-picker/index.js');c.properties={modes:p.data.fdeScopeModes,mode:'person',members:p.data.fdeScopeMembers,teams:p.data.fdeScopeTeams};c.triggerEvent=(name,detail)=>name==='modechange'?p.changeFdeScopeMode({detail}):p.changeFdeScopeSubject({detail});
 c.switchMode({currentTarget:{dataset:{mode:'team'}}});await tick();assert.equal(p.data.fdeProfileScope,'team');assert.equal(queries.at(-1).team_id,teamId);assert.equal(queries.at(-1).member_id,undefined);
 c.switchMode({currentTarget:{dataset:{mode:'person'}}});await tick();c.show();assert.equal(c.data.selecting,'person');c.select({currentTarget:{dataset:{id:memberId}}});await tick();assert.equal(p.data.fdeProfileScope,'self');assert.equal(p.data.fdeMemberId,memberId);assert.equal(p.data.fdeMemberName,'成员乙');assert.equal(queries.at(-1).scope,'team');assert.equal(queries.at(-1).member_id,memberId);assert.equal(queries.at(-1).team_id,undefined);
 for(const tab of ['maturity','efficiency','profile']){p.selectProfileTab({currentTarget:{dataset:{tab}}});assert.equal(p.data.fdeMemberId,memberId);}
 c.show();c.select({currentTarget:{dataset:{id:'self-id'}}});await tick();assert.equal(p.data.fdeMemberId,'');assert.equal(queries.at(-1).scope,'self');assert.equal(queries.at(-1).member_id,undefined);
 const before=queries.length;c.show();c.select({currentTarget:{dataset:{id:'outside-directory'}}});assert.equal(queries.length,before);app.globalData.session.capabilities['team.view']=false;c.select({currentTarget:{dataset:{id:memberId}}});assert.equal(p.data.fdeMemberId,'');assert.equal(queries.length,before);
});

test('FDE团队协作效率使用公司团队榜，并按所选团队ID展示选择态和期间商机数',async()=>{
 const queries=[];const teams=[{user_id:'team-a',name:'北区团队',role:'fde_team',opportunity_count:6,followup_count:12,demo_scene_count:3},{user_id:'team-b',name:'南区团队',role:'fde_team',opportunity_count:4,followup_count:8,demo_scene_count:2}];
 const {instance:p,app}=page({getFdeDashboard:async query=>{queries.push(query);return {data_source:'database',summary:{period_opportunities:4,period_visits:8,own_demo_scene_count:2},company_rankings:{data_source:'database',contract_version:2,scope:'company_fde_teams',selection:{personal:false,team_ids:['team-b']},items:teams}};}});
 app.globalData.session.role='fde_lead';Object.assign(p.data,{isFde:true,isFdeLead:true,fdeProfileScope:'team',fdeTeamId:'team-b',fdeTeamLabel:'南区团队',efficiencyMetric:'opportunities',efficiencyPeriod:'month'});
 await p.loadFdeEfficiency();assert.equal(queries.at(-1).team_id,'team-b');assert.equal(p.data.efficiencyTotal,4);assert.equal(p.data.efficiencyAggregateLabel,'南区团队');assert.equal(p.data.efficiencyRanking.length,2);assert.equal(p.data.efficiencyRanking[1].isSelected,true);assert.equal(p.data.efficiencyRanking[1].roleLabel,'FDE 团队');assert.match(p.data.fdeEfficiencyNote,/公司同类团队/);
 p.data.efficiencyMetric='followup';await p.loadFdeEfficiency();assert.equal(p.data.efficiencyTotal,8);assert.equal(p.data.efficiencyRanking[1].value,8);assert.equal(p.data.efficiencyRanking[1].isSelected,true);
 p.data.efficiencyMetric='demo';await p.loadFdeEfficiency();assert.equal(p.data.efficiencyTotal,2);assert.equal(p.data.efficiencyRanking[1].value,2);assert.equal(p.data.efficiencyRanking[1].isSelected,true);
});

test('销售与FDE使用同一目标组件和范围控件，组件WXSS全部使用类选择器',()=>{
 const page=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/profile/index.wxml'),'utf8'),fde=fs.readFileSync(path.resolve(__dirname,'../miniprogram/components/fde-profile/index.wxml'),'utf8');assert.match(page,/quarter-target/);assert.match(fde,/quarter-target/);assert.match(page,/profile-scope-picker/);assert.doesNotMatch(page,/全部人员|团队平均|部门平均/);
 for(const name of ['quarter-target','profile-scope-picker']){const css=fs.readFileSync(path.resolve(__dirname,'../miniprogram/components',name,'index.wxss'),'utf8');for(const match of css.matchAll(/([^{}]+)\{/g)){const selector=match[1].replace(/@import[^;]*;/g,'');assert.doesNotMatch(selector,/(^|[\s>+~])(view|text|label|button|page|canvas|input|textarea)\b|#[\w-]+|\[[^\]]*\]/);}}
});

test('最新审批结果使用中文展示并保留运营意见',async()=>{
 const {instance:c}=target({getTargets:async()=>({items:rows,editable:true,recent_batches:[{status:'rejected',decision_reason:'请与负责人重新确认季度计划',reviewer_name:'运营甲',reviewed_at:'2026-09-16T10:00:00Z',decided_at:'2026-09-15T10:00:00Z'}]})});await c.load();assert.equal(c.data.decision.label,'已驳回');assert.equal(c.data.decision.reason,'请与负责人重新确认季度计划');assert.equal(c.data.decision.reviewer,'运营甲');assert.equal(c.data.decision.date,'2026-09-16');assert.equal(c.data.canEdit,true);
});

test('FDE季度目标和实绩共用指定团队ID，不合并session所有团队目标',async()=>{
 const requests=[];const {instance:c,app}=harness('components/fde-profile/index.js',{getFdeDashboard:async query=>{requests.push(['facts',query]);return {data_source:'database',summary:{collection_amount:100,recognized_amount:200}};},getTargets:async query=>{requests.push(['targets',query]);return {items:rows,editable:false};}});
 app.globalData.session.role='fde_lead';app.globalData.session.teamIds=['team-a','team-b'];c.properties={profileScope:'team',selectedTeamId:'team-a'};await c.loadMetrics();assert.equal(requests[0][1].team_id,'team-a');assert.deepEqual(Array.from(requests[0][1].quarters),[quarter]);assert.equal(requests[1][1].team_id,'team-a');assert.equal(requests[1][1].period_type,'quarter');assert.equal(requests.length,2);assert.equal(c.data.canEditOwnTargets,false);
});

test('FDE选定人员保持三页签主体，画像和统计均传入所选人员',async()=>{
 const {instance:p,app}=page({getFdeDashboard:async query=>({data_source:'database',summary:{period_opportunities:2},company_rankings:{data_source:'database',items:[]}})});app.globalData.session.role='fde_lead';app.globalData.session.capabilities={'team.view':true};Object.assign(p.data,{isFde:true,isFdeLead:true,fdeMemberOptions:[{id:'',name:'本人'},{id:'other',name:'成员乙'}],fdeProfileScope:'self',efficiencyPeriod:'week'});p.changeFdeMember({detail:{value:1}});await tick();assert.equal(p.data.fdeMemberId,'other');assert.equal(p.data.fdeMemberName,'成员乙');assert.equal(p.data.efficiencyAggregateLabel,'成员乙');
});

test('合法元金额回填万元保持最多六位小数，只改另一项也可整单提交',async()=>{
 const cases=[[1234567.89,'123.456789'],[100.01,'0.010001'],[0.01,'0.000001'],[1.15,'0.000115'],['100.01','0.010001'],['1234567.89','123.456789'],['90071992547409.91','9007199254.740991']];
 for(const [amount,expected] of cases){let sent;const stored=[{kind:'collection',amount,version_no:7},{kind:'recognized',amount:2000000,version_no:8}];const {instance:c}=target({getTargets:async()=>({items:stored,editable:true}),saveTargetBatch:async body=>{sent=body;return {status:'pending'};}});await c.load();c.show();assert.equal(c.data.collection,expected);if(amount==='90071992547409.91')assert.equal(c.data.rows[0].currentText,'¥90,071,992,547,409.91');Object.assign(c.data,{recognized:'210',reason:'仅调整确收，回款保持原值'});await c.save();assert.ok(sent,expected+' should submit');assert.equal(sent.items[0].amount,String(amount));assert.equal(sent.items[0].version_no,7);assert.equal(sent.items[1].amount,'2100000');assert.equal(c.data.error,'');}
});

test('万元输入按十进制转换，不在往返中改动原金额分位',async()=>{
 for(const [wan,yuan] of [['123.456789',1234567.89],['0.010001',100.01],['000.000001',0.01],['100.000001',1000000.01]]){let sent;const {instance:c}=target({getTargets:async()=>({items:[],editable:true}),saveTargetBatch:async value=>{sent=value;return {status:'effective'};}});await c.load();c.show();Object.assign(c.data,{collection:wan,recognized:'200',reason:'与负责人确认'});await c.save();assert.equal(sent.items[0].amount,String(yuan));}
});

function oldSubjectData(){return {activeProfileTab:'profile',growthReady:true,growthLoading:false,overallScore:'91',profileScoreInfo:{text:'91'},dimensions:[{name:'成员A维度',score:91}],history:[{date:'2026-09-01',overall_score:91}],growthOptions:[{name:'成员A'}],reviewSummary:'成员A复盘内容',reviewDate:'2026-09-01',visitCount:10,aiAdvice:['成员A建议'],organizationReady:true,maturityScore:'88',efficiencyScore:'90',maturityScoreInfo:{text:'88'},efficiencyScoreInfo:{text:'90'},canEditSalesTarget:true};}
function assertSubjectCleared(p){assert.equal(p.data.growthReady,false);assert.equal(p.data.growthLoading,false);assert.equal(p.data.overallScore,'--');for(const key of ['dimensions','history','growthOptions','aiAdvice'])assert.equal(p.data[key].length,0,key);assert.equal(p.data.reviewSummary,'');assert.equal(p.data.visitCount,0);assert.equal(p.data.maturityScore,'--');assert.equal(p.data.efficiencyScore,'--');assert.equal(p.data.organizationReady,false);assert.equal(p.data.canEditSalesTarget,false);}

test('主管权限变化降为本人时立即清除旧成员画像，目录失败维持空态',async()=>{
 const {instance:p,app}=page({getProfileScopeOptions:async()=>{throw Error('目录暂不可用');}});app.ensureLogin=()=>true;app.globalData.session.role='supervisor';p.sync();Object.assign(p.data,oldSubjectData(),{profileScopeMode:'person',profileSelectedMemberId:'member-a'});p.growthTimer=setTimeout(()=>assert.fail('旧复盘轮询不应触发'),10000);
 const before=p.growthRequestId;app.globalData.session.role='sales';app.globalData.session.permissionVersion='2';p.onShow();assertSubjectCleared(p);assert.equal(p.data.profileScopeMode,'self');assert.equal(p.data.profileSelectedMemberId,'self-id');assert.equal(p.growthTimer,null);assert.ok(p.growthRequestId>before);await tick();assertSubjectCleared(p);assert.equal(p.data.directoryError,'目录暂不可用');assert.match(p.data.growthStatusText,/加载失败/);
});

test('同身份目录刷新失败也清掉旧画像并废弃仍在途的成员复盘请求',async()=>{
 let resolveGrowth;const {instance:p,app}=page({getProfileScopeOptions:async()=>{throw Error('目录请求失败');},getScopedSalesGrowth:()=>new Promise(resolve=>{resolveGrowth=resolve;})});app.globalData.session.role='supervisor';p.sync();Object.assign(p.data,{role:'supervisor',profileScopeMode:'person',profileSelectedMemberId:'member-a'});const old=p.loadScopedGrowth();Object.assign(p.data,oldSubjectData());const before=p.growthRequestId;await p.loadProfileDirectory();assertSubjectCleared(p);assert.ok(p.growthRequestId>before);assert.equal(p.data.scopeMembers.length,0);resolveGrowth({data_source:'database',latest:{dimension_scores:{},improvements:['旧A建议'],input_snapshot:{visit_count:10},score_summary:{value:91,text:'91'}},framework:{dimensions:[]},history:[]});await old;assertSubjectCleared(p);assert.equal(p.data.directoryError,'目录请求失败');
});

test('FDE已显示画像在身份属性变化后立即清空，等待新账号真实响应',async()=>{
 let resolveProfile;const {instance:c}=harness('components/fde-profile/index.js',{getFdeProfile:()=>new Promise(resolve=>{resolveProfile=resolve;}),getFdeDashboard:async()=>({data_source:'database',summary:{}}),getTargets:async()=>({items:[],editable:false})});c.properties={profileScope:'self'};Object.assign(c.data,{ready:true,portraitScore:'90',history:[{date:'2026-09-01'}],historyPoints:[{score:90}],summary:'旧账号画像',canReview:true,dimensions:[{score:90}]});c.properties.identityKey='new-login';
 // Invoke the same observable property callback used by WeChat on account/permission changes.
 const filename=path.resolve(__dirname,'../miniprogram/components/fde-profile/index.js');let definition;vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Component:value=>{definition=value;},require:name=>name.endsWith('/apiClient')?{}:require(path.resolve(path.dirname(filename),name))});definition.properties.identityKey.observer.call(c);
 assert.equal(c.data.ready,false);assert.equal(c.data.canReview,false);assert.equal(c.data.portraitScore,'—');assert.equal(c.data.history.length,0);assert.equal(c.data.summary,'');assert.equal(c.data.dimensions.length,0);resolveProfile({});await tick();
});

test('一线销售目录请求失败显示统一重试状态，重试成功后恢复本人数据',async()=>{
 let rejectDirectory,calls=0;const {instance:p,app}=page({getProfileScopeOptions:()=>{calls++;return calls===1?new Promise((resolve,reject)=>{rejectDirectory=reject;}):Promise.resolve({data_source:'database',allowed_scopes:['self'],members:[{id:'self-id',display_name:'本人',role:'sales',team_ids:[]}],teams:[],defaults:{scope:'self',member_id:'self-id'}});},getProfilePerformance:async()=>performance(),ensureSalesGrowthReview:async()=>({}),getSalesGrowth:async()=>({today_status:'failed'})});
 app.globalData.session.role='sales';p.sync();const first=p.loadProfileDirectory();assert.equal(p.data.directoryLoading,true);rejectDirectory(Error('查看范围暂不可用'));await first;assert.equal(p.data.directoryLoading,false);assert.equal(p.data.directoryError,'查看范围暂不可用');assert.equal(p.data.organizationReady,false);await p.loadProfileDirectory();await tick();assert.equal(calls,2);assert.equal(p.data.directoryError,'');assert.equal(p.data.profileScopeMode,'self');assert.equal(p.data.organizationReady,true);
 const markup=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/profile/index.wxml'),'utf8');assert.match(markup,/<view wx:if="\{\{!isFde && \(directoryLoading \|\| directoryError\)\}\}" class="profile-directory-state surface">/);assert.match(markup,/<button wx:else class="profile-directory-retry" bindtap="loadProfileDirectory"/);assert.equal((markup.match(/bindtap="loadProfileDirectory"/g)||[]).length,1);assert.match(markup,/!directoryLoading && !directoryError && activeProfileTab === 'maturity'/);
});

test('amount_text优先于已丢分位的JSON数字，超大合法目标回填提交仍保留原值',async()=>{
 let sent;const {instance:c}=target({getTargets:async()=>({items:[{kind:'collection',amount:90071992547409.91,amount_text:'90071992547409.91',version_no:2},{kind:'recognized',amount:100.01,amount_text:'100.01',version_no:3}],editable:true}),saveTargetBatch:async body=>{sent=body;return {status:'pending'};}});await c.load();c.show();assert.equal(c.data.collection,'9007199254.740991');assert.equal(c.data.recognized,'0.010001');assert.equal(c.data.rows[0].currentText,'¥90,071,992,547,409.91');Object.assign(c.data,{recognized:'0.02',reason:'只调整确收目标'});await c.save();assert.equal(sent.items[0].amount,'90071992547409.91');assert.equal(sent.items[1].amount,'200');
});

test('目标弹层提升到页面根节点，入口按内容宽度显示且缺目标提示不换行',()=>{
 const component=fs.readFileSync(path.resolve(__dirname,'../miniprogram/components/quarter-target/index.wxml'),'utf8'),css=fs.readFileSync(path.resolve(__dirname,'../miniprogram/components/quarter-target/index.wxss'),'utf8');
 assert.match(component,/<root-portal wx:if="\{\{open\}\}"><view class="sheet-mask"/);assert.match(component,/<button[^>]*class="target-edit-button"[^>]*size="mini"/);assert.match(css,/\.target-sheet\{display:flex;flex-direction:column/);assert.match(css,/\.sheet-footer\{flex-shrink:0/);
 const metric=require('../miniprogram/utils/profileMetrics');assert.equal(metric.performanceMetric({},'collection',100).rateText,'目标待设置');assert.equal(metric.performanceMetric({targets:{collection:100}},'collection',null).rateText,'实绩待登记');
 for(const file of ['pages/profile/index.wxss','components/fde-profile/index.wxss'])assert.match(fs.readFileSync(path.resolve(__dirname,'../miniprogram',file),'utf8'),/white-space:nowrap/);
});

test('主管和总经理成员选择器打开时撤下画布，取消、选择、目录变化和离页均解除遮挡状态',()=>{
 for(const role of ['supervisor','manager'])for(const end of ['cancel','select','directory','hide']){
  const {instance:p}=page(),{instance:c}=harness('components/profile-scope-picker/index.js');let redraws=0;const selected=[];
  Object.assign(p.data,{role,activeProfileTab:'profile',growthReady:true});p.drawGrowthCharts=()=>redraws++;
  c.properties={mode:'person',members:[{id:'member',name:'成员'}],teams:[]};
  c.triggerEvent=(name,detail)=>{if(name==='visibilitychange')p.profilePickerVisibility({detail});if(name==='subjectchange')selected.push(detail.id);};
  c.show();assert.equal(p.data.scopePickerOpen,true);assert.equal(redraws,0);
  if(end==='cancel')c.close();
  if(end==='select')c.select({currentTarget:{dataset:{id:'member'}}});
  if(end==='directory')c.observers['members,teams,memberId,teamId,mode'].call(c);
  if(end==='hide'){p.visible=false;c.pageLifetimes.hide.call(c);}
  assert.equal(p.data.scopePickerOpen,false);assert.equal(redraws,end==='hide'?0:1);
  assert.deepEqual(selected,end==='select'?['member']:[]);
  c.close();assert.equal(redraws,end==='hide'?0:1);
 }
});

test('销售雷达和成长曲线已排队的测量回调不能在成员弹层上重新绘制',()=>{
 const {instance:p}=page(),callbacks=[];
 Object.assign(p.data,{dimensions:Array.from({length:6},()=>({score:60})),history:[{overall_score:60}],growthReady:true});
 p.createSelectorQuery=()=>({select(){return this;},boundingClientRect(fn){callbacks.push(fn);return this;},exec(){}});
 p.drawGrowthCharts();assert.equal(callbacks.length,2);
 p.profilePickerVisibility({detail:{open:true}});
 // wx.createCanvasContext is intentionally absent: a late draw would fail here.
 callbacks.forEach(fn=>fn({width:300,height:200}));
 p.drawGrowthCharts();assert.equal(callbacks.length,2);
});

test('销售和FDE画像的四张画布均按弹层状态卸载并保留等高占位',()=>{
 const read=file=>fs.readFileSync(path.resolve(__dirname,'../miniprogram',file),'utf8');
 const sales=read('pages/profile/index.wxml'),fde=read('components/fde-profile/index.wxml');
 assert.equal((sales.match(/bind:visibilitychange="profilePickerVisibility"/g)||[]).length,2);
 assert.match(sales,/<fde-profile charts-hidden="\{\{scopePickerOpen\}\}"/);
 for(const [markup,flag,ids] of [[sales,'scopePickerOpen',['abilityRadar','growthLine']],[fde,'chartsHidden',['fdeAbilityRadar','fdeHistory']]]){
  for(const id of ids)assert.match(markup,new RegExp('<canvas wx:if="\\{\\{!'+flag+'\\}\\}" canvas-id="'+id+'"'));
  assert.equal((markup.match(/<view wx:else class="(?:radar|growth|history)-canvas"\/>/g)||[]).length,2);
 }
});
