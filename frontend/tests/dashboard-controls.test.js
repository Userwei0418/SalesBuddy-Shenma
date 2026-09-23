const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function component(name,props={}){
 let def;const events=[];
 const filename=path.resolve(__dirname,'../miniprogram/components',name,'index.js');
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Component:value=>def=value,require:name=>require(path.resolve(path.dirname(filename),name)),getApp:()=>({globalData:{session:{userId:'self'}}})});
 const instance={...def.methods,data:JSON.parse(JSON.stringify(def.data)),properties:props,setData(values){Object.assign(this.data,values);},triggerEvent(name,data){events.push({name,data});}};
 return {instance,def,events};
}
test('个人排名卡仅显示本人原始名次，完整榜单保留并列与零值',()=>{
 const rows=[{id:'other',rank:1},{id:'self',rank:2},{id:'zero',rank:3}];
 const {instance,def}=component('dashboard-ranking',{rows,summaryIds:['self']});
 def.observers['rows, summaryIds'].call(instance);assert.equal(instance.data.summary.length,1);assert.equal(instance.data.summary[0].rank,2);
 instance.open();assert.equal(instance.data.open,true);assert.equal(instance.properties.rows.length,3);
 instance.touchStart({touches:[{clientX:10,clientY:10}]});instance.touchEnd({changedTouches:[{clientX:100,clientY:20}]});assert.equal(instance.data.open,true);
 instance.touchStart({touches:[{clientX:10,clientY:10}]});instance.touchEnd({changedTouches:[{clientX:15,clientY:70}]});assert.equal(instance.data.open,false);
});
test('选人支持分组搜索，取消保留原选择，确认只提交一个成员',()=>{
 const {instance:p,events}=component('dashboard-picker',{options:[{id:'a',name:'甲',group:'南区'},{id:'b',name:'乙',group:'北区',account_code:'XS002'}],selected:['a']});
 p.open();p.search({detail:{value:'XS002'}});assert.equal(p.data.groups.length,1);assert.equal(p.data.groups[0].name,'北区');
 p.select({currentTarget:{dataset:{id:'b'}}});p.close();assert.equal(events.length,0);assert.deepEqual(p.properties.selected,['a']);
 p.open();p.select({currentTarget:{dataset:{id:'b'}}});p.apply();assert.equal(events.length,1);assert.deepEqual(Array.from(events[0].data.ids),['b']);
});
test('团队多选不能提交空范围；关闭与重新打开不保留未确认修改',()=>{
 const {instance:p,events}=component('dashboard-picker',{multiple:true,options:[{id:'north_east',name:'北区＋东区'},{id:'south_hkmo',name:'南区＋港澳'}],selected:['north_east','south_hkmo']});
 p.open();for(const id of ['north_east','south_hkmo'])p.select({currentTarget:{dataset:{id}}});p.apply();assert.equal(events.length,0);assert.equal(p.data.canApply,false);
 p.close();p.open();assert.equal(p.data.draft.length,2);p.apply();assert.equal(events[0].data.ids.length,2);
});
test('FDE本人空ID可选择；目录失效的成员不提交',()=>{
 const {instance:p,events}=component('dashboard-picker',{options:[{id:'',name:'本人'},{id:'old',name:'已停用'}],selected:['']});
 p.open();p.apply();assert.deepEqual(Array.from(events[0].data.ids),['']);
 p.open();p.select({currentTarget:{dataset:{id:'old'}}});p.properties.options=[{id:'',name:'本人'}];p.apply();assert.equal(events.length,1);assert.equal(p.data.canApply,false);
});
test('FDE共用详情卡保留专属指标，所选成员及团队摘要跟随查看对象',()=>{
 const {instance:c}=component('fde-dashboard');
 c.data.followupRanking=[{user_id:'self',name:'本人',rank:2,value:'5',width:50}];
 c.data.recognizedRanking=[{user_id:'other',name:'乙',rank:1,value:'3',width:100}];
 c.data.demoRanking=[];c.data.demoRankingReady=true;c.data.companyRankingError='';
 c.data.rankingSelection={personal:true,member_id:'other',cohort_role:'fde'};c.data.memberOptions=[{id:'',name:'本人'},{id:'other',name:'乙'}];c.data.memberIndex=1;
 c.rebuildCompanyCards();c.updateMemberPicker();assert.deepEqual(Array.from(c.data.ownIds),['other']);assert.deepEqual(Array.from(c.data.pickerSelected),['other']);
 assert.equal(c.data.companyCards[0].rows[0].displayValue,'5 条');assert.equal(c.data.companyCards[1].title,'FDE 确收排名');c.data.rankingSelection={personal:false,team_ids:['fde-team']};c.rebuildCompanyCards();assert.deepEqual(Array.from(c.data.ownIds),['fde-team']);assert.equal(c.data.rankingCohort,'公司全部 FDE 团队');
});
test('FDE和负责人进入看板均不请求销售排名接口',()=>{
 for(const role of ['fde','fde_lead']){
  let page;let calls=0;const filename=path.resolve(__dirname,'../miniprogram/pages/bi/index.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:value=>page=value,require:name=>name.endsWith('/apiClient')?new Proxy({}, {get(){return()=>{calls++;};}}):require(path.resolve(path.dirname(filename),name)),getApp:()=>({globalData:{role,session:{role,userId:'self'}},ensureLogin:()=>true})});
  page.setData=values=>Object.assign(page.data,values);page.onShow();assert.equal(page.data.isFde,true);assert.equal(calls,0);
 }
});
test('详情弹层定位当前所选对象，并高亮多选团队',()=>{
 const {instance:c,def}=component('dashboard-ranking',{rows:[{id:'a',rank:1},{id:'b',rank:2},{id:'c',rank:3}],summaryIds:['b','c']});
 def.observers['rows, summaryIds'].call(c);assert.equal(c.data.focusId,'rank-item-1');
 assert.deepEqual(Array.from(c.data.summary,r=>r.id),['b','c']);assert.equal(c.data.detailRows[0].isSelected,false);
});

test('看板全部团队与具体团队互斥单选，取消不提交，单个团队不冒充全部团队',()=>{
 const options=[{id:'all',name:'全部团队'},{id:'team:empty',name:'空团队'}];
 const {instance:p,events}=component('dashboard-picker',{selectionKind:'team',options,selected:['all'],multiple:false});
 p.open();p.select({currentTarget:{dataset:{id:'team:empty'}}});assert.deepEqual(Array.from(p.data.draft),['team:empty']);
 p.close();assert.equal(events.length,0);p.open();assert.deepEqual(Array.from(p.data.draft),['all']);
 p.select({currentTarget:{dataset:{id:'team:empty'}}});p.apply();assert.deepEqual(Array.from(events[0].data.ids),['team:empty']);
 p.open();p.select({currentTarget:{dataset:{id:'team:empty'}}});p.select({currentTarget:{dataset:{id:'all'}}});p.apply();
 assert.deepEqual(Array.from(events[1].data.ids),['all']);
});

test('看板单选同步事实和排名，全部选项只在UI中存在且适应目录变更',async()=>{
 let page,groups=[{code:'team:empty',name:'空团队'}],reads=0;
 const filename=path.resolve(__dirname,'../miniprogram/pages/bi/index.js');
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{
  Page:value=>page=value,
  require:name=>name.endsWith('/apiClient')?{getDashboardOptions:async()=>({members:[],team_groups:groups})}:require(path.resolve(path.dirname(filename),name)),
  getApp:()=>({globalData:{role:'manager',session:{role:'manager',userId:'self',workspaceId:'w'}}}),
 });
 page.data=JSON.parse(JSON.stringify(page.data));Object.assign(page.data,{role:'manager',viewMode:'team'});
 page.setData=values=>Object.assign(page.data,values);page.loadFacts=async()=>{reads++;};page.loadRankingData=async()=>{reads++;};
 await page.loadOptions(false);assert.deepEqual(Array.from(page.data.teamPickerSelected),['all']);
 assert.deepEqual(Array.from(page.factsQuery().team_groups),['team:empty']);
 await page.selectTeams({detail:{ids:['team:empty']}});assert.equal(page.data.teamLabel,'空团队');assert.equal(reads,2);
 await page.selectTeams({detail:{ids:['all','team:empty']}});assert.equal(reads,2,'multiple selection is rejected');
 await page.selectTeams({detail:{ids:['all']}});assert.equal(page.data.teamLabel,'部门合计 · 全部团队');
 assert.ok(!page.factsQuery().team_groups.includes('all'));
 groups.push({code:'team:new',name:'新团队'});await page.loadOptions();
 assert.deepEqual(Array.from(page.factsQuery().team_groups),['team:empty','team:new']);
 await page.selectTeams({detail:{ids:['team:empty']}});await page.loadOptions();assert.equal(page.data.selectedTeamChoice,'team:empty');
 groups=groups.filter(row=>row.code!=='team:empty');await page.loadOptions();assert.equal(page.data.selectedTeamChoice,'all');
 assert.deepEqual(Array.from(page.factsQuery().team_groups),['team:new']);
});
