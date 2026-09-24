const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const row=(id,value,rank=1)=>({user_id:id,account_code:id,name:id,team:'南区',value,rank,population:3,record_count:value?2:0,customer_count:value?1:0});
function ranking(role='sales',value=100,query={personal:true}){
 const groups=[{code:'north_east',name:'北区＋东区',value:200,rank:1,record_count:1,customer_count:1},{code:'south_hkmo',name:'南区＋港澳',value,rank:2,record_count:2,customer_count:1}];
 return {contract_version:2,data_source:'database',scope:query.personal?'peer':'company_teams',selection:{personal:query.personal,member_id:query.personal?query.member_id||'a':null,cohort_role:query.member_id?'sales':role,team_groups:query.personal?[query.member_id==='b'?'north_east':'south_hkmo']:(query.team_groups&&query.team_groups.length?query.team_groups:['south_hkmo'])},own_region_codes:['south_hkmo'],
   opportunity_acv:{rows:query.personal?[row('b',200),row('a',value,2),row('z',0,3)]:groups,groups},
   region:{rows:groups,groups:[]},followup:{calculation:query.personal?'personal':'team_followup_per_capita_v1',rows:query.personal?[row('b',3),row('a',2,2),row('z',0,3)]:groups.map(g=>({...g,member_count:1,average:g.value,members:[]})),groups:[]},
   active_opportunities:{rows:[row('a',1),row('b',2)],groups:[{code:'north_east',value:2},{code:'south_hkmo',value:1}]}};
}
function pageWith(role='sales',overrides={}){
 let page;const calls=[],facts=[];const year=new Date().getFullYear();
 const response=(personal,options={})=>({data_source:'database',scope:personal?(options.member_id?'member':'self'):((options.team_groups||[]).length===1||(options.team_groups||[]).some(code=>code.startsWith('team:')))?'team':'workspace',
   selection:{personal,member_id:personal?options.member_id||'a':null,team_groups:options.team_groups||[]},
   opportunities:[{id:'o',owner_id:options.member_id||'a',amount:options.member_id==='b'?200:100,status:'open',probability:10,expected_close_date:`${year}-01-01`}],quarter_forecasts:[],quarter_actuals:[],recent_visits:[]});
 const api={getDashboard:async(personal,options)=>{facts.push({personal,...options});return response(personal,options);},
   getDashboardRankings:async query=>{calls.push(query);return ranking(role,100,query);},
   getDashboardOptions:async()=>({members:[{id:'a',name:'本人',team:'南区'},{id:'b',name:'另一成员',team:'北区'}],team_groups:[{code:'north_east',name:'北区＋东区'},{code:'south_hkmo',name:'南区＋港澳'}]}),
   getDirectoryMembers:()=>{throw Error('不应为排名查询目录');},...overrides};
 const app={globalData:{role,session:{role,userId:'a',userName:'本人',workspaceId:'w',capabilities:{'team.view':['supervisor','manager'].includes(role)}}},ensureLogin:()=>true};
 const filename=path.resolve(__dirname,'../miniprogram/pages/bi/index.js');
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:value=>page=value,require:name=>name.endsWith('/apiClient')?api:require(path.resolve(path.dirname(filename),name)),getApp:()=>app,wx:{showNavigationBarLoading(){},hideNavigationBarLoading(){},showToast(){}}});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=data=>Object.assign(page.data,data);
 return {page,calls,facts,app,response,api};
}
test('销售接受完整同级榜，保留真实名次和零值，默认摘要指向本人',async()=>{
 const {page,calls}=pageWith();await page.onShow();
 assert.equal(page.data.rankingMessage,'');assert.equal(page.data.rankingCards[0].rows.length,3);
 assert.equal(page.data.rankingCards[0].rows.find(r=>r.id==='a').rank,2);
 assert.deepEqual(Array.from(page.data.rankingCards[0].summaryIds),['a']);
 assert.deepEqual(Array.from(page.data.rankingCards[2].rows,r=>r.name),['北区＋东区','南区＋港澳']);
 assert.equal(calls.length,1);assert.equal(calls[0].personal,true);
 await page.changeView({currentTarget:{dataset:{mode:'team'}}});assert.equal(page.data.viewMode,'personal');
});
test('主管选择成员显示该人名次，团队切换到公司团队榜',async()=>{
 const {page,calls}=pageWith('supervisor');await page.onShow();const before=JSON.stringify(page.data.rankingCards);
 await page.selectMember({detail:{ids:['b']}});
 assert.equal(page.data.totalAcv,'¥200');assert.equal(page.data.activeOpportunityCount,2);
 assert.notEqual(JSON.stringify(page.data.rankingCards),before);assert.equal(calls.length,2);assert.deepEqual(Array.from(page.data.rankingCards[0].summaryIds),['b']);assert.deepEqual(Array.from(page.data.rankingCards[2].summaryIds),['north_east']);
 await page.changeView({currentTarget:{dataset:{mode:'team'}}});assert.equal(page.data.activeOpportunityCount,3);assert.equal(calls.length,3);assert.deepEqual(Array.from(page.data.rankingCards[0].summaryIds),['north_east','south_hkmo']);assert.equal(page.data.rankingCards.length,2);
});
test('总经理团队单选、全部团队和个人切换均可用，团队筛选不改变完整区域排名',async()=>{
 const {page,calls,facts}=pageWith('manager');await page.onShow();
 assert.equal(page.data.viewMode,'team');assert.equal(page.data.loadError,'');
 await page.selectTeams({detail:{ids:['north_east']}});assert.equal(page.data.activeOpportunityCount,2);
 assert.equal(page.data.rankingCards[0].rows.length,2);assert.deepEqual(Array.from(page.data.rankingCards[0].summaryIds),['north_east']);assert.equal(calls.length,2);
 await page.selectTeams({detail:{ids:['all']}});assert.equal(page.data.activeOpportunityCount,3);assert.deepEqual(Array.from(page.data.rankingCards[0].summaryIds),['north_east','south_hkmo']);
 await page.changeView({currentTarget:{dataset:{mode:'personal'}}});await page.selectMember({detail:{ids:['b']}});
 assert.equal(page.data.loadError,'');assert.equal(facts.at(-1).member_id,'b');assert.equal(page.data.totalAcv,'¥200');
});
test('排名失败独立重试，不能清除已取得的经营数据',async()=>{
 let fail=true;const {page}=pageWith('sales',{getDashboardRankings:async()=>{if(fail)throw Error('排名暂不可用');return ranking();}});
 await page.onShow();assert.equal(page.data.totalAcv,'¥100');assert.match(page.data.rankingMessage,/暂不可用/);
 fail=false;await page.reloadRankings();assert.equal(page.data.rankingMessage,'');assert.equal(page.data.rankingCards[0].rows.length,3);
});
test('拒绝旧本人榜、错误部门范围或缺失统计，避免误报完整榜',async()=>{
 for(const payload of [{data_source:'database',scope:'peer'},{...ranking(),scope:'self'},{...ranking(),scope:'workspace'}]){
  const {page}=pageWith('sales',{getDashboardRankings:async()=>payload});await page.onShow();assert.ok(page.data.rankingMessage);assert.equal(page.data.rankingCards[0].rows.length,0);
 }
});
test('旧季度排名、旧成员响应和卸载后的响应不能覆盖当前结果',async()=>{
 let old,requests=0;const {page}=pageWith('sales',{getDashboardRankings:()=>++requests===1?new Promise(r=>old=r):Promise.resolve(ranking('sales',222))});
 page.onShow();await tick();page.changeQuarter({detail:{value:page.data.quarterOptions.findIndex(q=>q.quarter===4&&q.year===new Date().getFullYear())}});await tick();
 old(ranking('sales',111));await tick();assert.equal(page.data.rankingCards[0].rows.find(r=>r.id==='a').value,222);
 let late;const next=pageWith('supervisor');await next.page.onShow();
 let memberReply;next.api.getDashboard=(personal,options)=>options.member_id==='b'?new Promise(r=>memberReply=r):Promise.resolve(next.response(personal,options));
 next.page.selectMember({detail:{ids:['b']}});await next.page.selectMember({detail:{ids:['a']}});
 memberReply(next.response(true,{member_id:'b',team_groups:[]}));await tick();assert.equal(next.page.data.totalAcv,'¥100');
 // Start a request for a different identity, then invalidate it on unload.
 const closed=pageWith('supervisor',{getDashboard:()=>new Promise(r=>late=r)});closed.page.onShow();await tick();closed.page.onUnload();late(closed.response(true));await tick();assert.equal(closed.page._raw,null);
});
test('成员回执不匹配时不展示错误金额',async()=>{
 const {page}=pageWith('supervisor',{getDashboard:async()=>({data_source:'database',scope:'member',selection:{member_id:'wrong'},opportunities:[],quarter_forecasts:[],quarter_actuals:[]})});
 await page.onShow();await page.selectMember({detail:{ids:['b']}});assert.match(page.data.loadError,/不一致/);assert.equal(page.data.totalAcv,'—');
});
test('真实空榜使用空状态',async()=>{
 const empty={rows:[],groups:[]};const {page}=pageWith('sales',{getDashboardRankings:async()=>({...ranking(),opportunity_acv:empty,region:empty,followup:empty,active_opportunities:empty})});
 await page.onShow();assert.equal(page.data.rankingMessage,'');assert.equal(page.data.rankingCards[0].rows.length,0);assert.equal(page.data.activeOpportunityCount,0);
});
test('快速切换成员时旧排名回执不得覆盖新对象',async()=>{
 const {page,api}=pageWith('supervisor');await page.onShow();let reply;
 api.getDashboardRankings=query=>query.member_id==='b'?new Promise(resolve=>reply=()=>resolve(ranking('supervisor',999,query))):Promise.resolve(ranking('supervisor',100,query));
 page.selectMember({detail:{ids:['b']}});await tick();await page.selectMember({detail:{ids:['a']}});
 reply();await tick();assert.deepEqual(Array.from(page.data.rankingCards[0].summaryIds),['a']);
 assert.equal(page.data.rankingCards[0].rows.find(r=>r.id==='a').value,100);
});
test('排名成员或团队回执错误不能展示其他对象名次',async()=>{
 const {page,api}=pageWith('supervisor');await page.onShow();api.getDashboardRankings=async()=>ranking('supervisor');
 await page.selectMember({detail:{ids:['b']}});assert.match(page.data.rankingMessage,/成员/);assert.equal(page.data.rankingCards[0].rows.length,0);
 const m=pageWith('manager');await m.page.onShow();m.api.getDashboardRankings=async query=>ranking('manager',100,{...query,team_groups:['south_hkmo']});
 await m.page.selectTeams({detail:{ids:['north_east']}});assert.match(m.page.data.rankingMessage,/团队/);
});

test('总经理首次加载先确定三个动态团队，再发送事实与排名，不丢弃迟到数据',async()=>{
 let directoryReply,factsReply,ranksReply;const queries=[];
 const options={members:[{id:'a',name:'本人',role:'manager'}],team_groups:[{code:'team:east',name:'东区'},{code:'team:north',name:'北区'},{code:'team:south',name:'南区'}]};
 const {page,response}=pageWith('manager',{
  getDashboardOptions:()=>new Promise(resolve=>directoryReply=resolve),
  getDashboard:(personal,query)=>{queries.push({personal,...query});return new Promise(resolve=>factsReply=()=>resolve(response(personal,query)));},
  getDashboardRankings:query=>{queries.push(query);return new Promise(resolve=>ranksReply=()=>resolve(ranking('manager',100,query)));},
 });
 const loading=page.onShow();assert.equal(queries.length,0);
 directoryReply(options);await tick();assert.equal(queries.length,2);
 for(const query of queries)assert.deepEqual(Array.from(query.team_groups),['team:east','team:north','team:south']);
 factsReply();ranksReply();await loading;
 assert.equal(page.data.loading,false);assert.equal(page.data.rankingLoading,false);
 assert.equal(page.data.loadError,'');assert.equal(page.data.totalAcv,'¥100');
 assert.equal(page.data.teamLabel,'部门合计 · 全部团队');
});

test('动态部门多选接受真实team范围，回传所选部门不一致仍拒绝事实',async()=>{
 const options={members:[],team_groups:[{code:'team:east',name:'东区'},{code:'team:north',name:'北区'}]};
 for(const mismatched of [false,true]){
  const {page,response}=pageWith('manager',{
   getDashboardOptions:async()=>options,
   getDashboard:async(personal,query)=>({...response(personal,query),scope:'team',selection:{personal:false,team_groups:mismatched?['team:east']:query.team_groups}}),
  });
  await page.onShow();assert.equal(page.data.loading,false);
  if(mismatched){assert.match(page.data.loadError,/返回团队与当前选择不一致/);assert.equal(page.data.totalAcv,'—');}
  else{assert.equal(page.data.loadError,'');assert.equal(page.data.totalAcv,'¥100');}
 }
});

test('目录首次失败后重试自动加载有效团队；部门失效后清理选择并等待新数据',async()=>{
 let failed=true;let groups=[{code:'team:east',name:'东区'},{code:'team:north',name:'北区'},{code:'team:south',name:'南区'}];
 const {page,calls,facts}=pageWith('manager',{getDashboardOptions:async()=>{if(failed)throw Error('目录不可用');return {members:[],team_groups:groups};}});
 await page.onShow();assert.equal(page.data.optionsError,'目录不可用');assert.equal(facts.length,1);
 failed=false;await page.loadOptions();assert.equal(page.data.optionsError,'');assert.equal(facts.length,2);assert.equal(calls.length,2);
 assert.deepEqual(Array.from(facts.at(-1).team_groups),['team:east','team:north','team:south']);
 await page.selectTeams({detail:{ids:['team:north']}});groups=groups.filter(row=>row.code!=='team:north');
 await page.loadOptions();assert.deepEqual(Array.from(page.data.selectedTeamGroups),['team:east','team:south']);
 assert.deepEqual(Array.from(facts.at(-1).team_groups),['team:east','team:south']);assert.equal(page.data.loading,false);
});

test('离开看板后迟到目录不会再请求事实或排名',async()=>{
 let reply;const {page,calls,facts}=pageWith('manager',{getDashboardOptions:()=>new Promise(resolve=>reply=resolve)});
 const loading=page.onShow();page.onUnload();reply({members:[],team_groups:[{code:'team:east',name:'东区'}]});await loading;
 assert.equal(calls.length,0);assert.equal(facts.length,0);
});

test('总经理选择FDE不请求销售排名，前一销售排名迟到失败不能覆盖提示',async()=>{
 for(const role of ['fde','fde_lead']){
  const {page,api,calls}=pageWith('manager',{getDashboardOptions:async()=>({members:[{id:'a',name:'本人',role:'manager'},{id:'b',name:'销售乙',role:'sales'},{id:'f',name:'FDE成员',role}],team_groups:[]})});
  await page.onShow();await page.changeView({currentTarget:{dataset:{mode:'personal'}}});
  let reject;api.getDashboardRankings=()=>{calls.push('old sales');return new Promise((resolve,no)=>reject=no);};
  const previous=page.selectMember({detail:{ids:['b']}});await tick();const before=calls.length;
  await page.selectMember({detail:{ids:['f']}});assert.equal(calls.length,before);assert.equal(page.data.rankingMessage,'FDE暂不提供销售排名');
  reject(Error('旧销售排名失败'));await previous;
  assert.equal(page.data.rankingLoading,false);assert.equal(page.data.rankingMessage,'FDE暂不提供销售排名');assert.equal(page.data.rankingCards[0].rows.length,0);
 }
});

test('具体团队与全部团队的活跃商机分别计算',async()=>{
 const {page}=pageWith('manager');await page.onShow();
 page.data.teamOptions=[{id:'team:east',name:'东区'},{id:'team:north',name:'北区'},{id:'team:south',name:'南区'}];
 page._nativeRankings.active_opportunities={rows:[{value:99}],groups:[{code:'team:east',value:2},{code:'team:north',value:3},{code:'team:south',value:7}]};
 page.data.selectedTeamChoice='team:east';page.data.selectedTeamGroups=['team:east'];page.updateActiveCount();assert.equal(page.data.activeOpportunityCount,2);
 page.updateSelectionLabels();assert.equal(page.data.teamLabel,'东区');
 page.data.selectedTeamChoice='team:south';page.data.selectedTeamGroups=['team:south'];page.updateActiveCount();assert.equal(page.data.activeOpportunityCount,7);
 page.updateSelectionLabels();assert.equal(page.data.teamLabel,'南区');
 page.data.selectedTeamChoice='all';page.data.selectedTeamGroups=['team:east','team:north','team:south'];page.updateActiveCount();assert.equal(page.data.activeOpportunityCount,12);
 page.updateSelectionLabels();assert.equal(page.data.teamLabel,'部门合计 · 全部团队');
});

test('团队跟进显示人均、总数和人数，个人次数与ACV辅助商机数保持原口径',async()=>{
 const {page,api}=pageWith('manager');
 api.getDashboardRankings=async query=>{
  const payload=ranking('manager',100,query);
  payload.followup.rows=[{code:'south_hkmo',name:'南区',value:2/3,average:0.67,member_count:3,record_count:2,customer_count:1,rank:2,members:[{user_id:'a',followup_count:2},{user_id:'z',followup_count:0}]}];
  return payload;
 };
 await page.onShow();assert.equal(page.data.rankingMessage,'');
 const card=page.data.rankingCards.find(c=>c.key==='followup'),row=card.rows[0];
 assert.equal(card.title,'团队人均跟进排名');assert.equal(row.displayValue,'0.67 次/人');
 assert.equal(row.meta,'共 2 次 · 3 名有效成员 · 1 家客户');assert.equal(row.members.length,2);
 assert.match(row.memberSummary,/总次数 2 ÷ 有效成员 3/);
 assert.equal(page.data.rankingCards.length,2);assert.match(page.data.rankingCards[0].rows[1].meta,/个在推商机/);
});

test('旧后端团队总次数不能冒充人均接口，明确显示配套版本错误',async()=>{
 const {page}=pageWith('manager',{getDashboardRankings:async query=>{
  const payload=ranking('manager',100,query);delete payload.followup.calculation;return payload;
 }});
 await page.onShow();assert.match(page.data.rankingMessage,/当前后端尚未提供人均/);
 assert.equal(page.data.rankingCards[1].rows.length,0);
});
