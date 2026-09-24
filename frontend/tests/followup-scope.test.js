const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { beijingDateParts } = require('../miniprogram/utils/opportunityQuarter');

const roles = ['sales', 'supervisor'];
const members = [
  { id: 'a', name: '销售甲', role: 'sales', team_id: 'north-id', team: '北区' },
  { id: 'b', name: '销售乙', role: 'sales', team_id: 'east-id', team: '东区' },
  { id: 'c', name: '销售丙', role: 'sales', team_id: 'south-id', team: '南区' },
  { id: 'd', name: '销售丁', role: 'sales', team_id: 'hkmo-id', team: '港澳' },
  { id: 'z', name: '零跟进总监', role: 'supervisor', team_id: 'combined-id', team: '北区东区' },
];
const visit = (id, recorder_id, customer_id) => ({ id, recorder_id, recorder_name: members.find(item => item.id === recorder_id).name, customer_id, interaction_at: new Date().toISOString(), status: 'confirmed' });
const visits = [visit('a1', 'a', 'c1'), visit('a2', 'a', 'c1'), visit('b1', 'b', 'c1'), visit('b2', 'b', 'c2'), visit('b3', 'b', 'c3'), visit('c1', 'c', 'c4'), visit('d1', 'd', 'c4')];
const current = beijingDateParts(new Date());
const closeDate = `${current.year}-${String((current.quarter - 1) * 3 + 1).padStart(2, '0')}-15`;
const opportunities = [
  { id: 'oa', owner_id: 'a', owner_name: '销售甲', team_id: 'north-id', team_name: '北区', amount: 10000 },
  { id: 'ob', owner_id: 'b', owner_name: '销售乙', team_id: 'east-id', team_name: '东区', amount: 20000 },
].map(item => ({ ...item, expected_close_date: closeDate, status: 'open', probability: 30, partner_name: null }));
const data = (scope, items = visits, deals = opportunities) => ({ data_source: 'database', scope, selection:{member_id:scope==='self'?'a':null,team_groups:scope==='self'?[]:['north_east','south_hkmo']}, opportunities: deals, recent_visits: items, quarter_forecasts: [], quarter_actuals: [] });
const event = index => ({ currentTarget: { dataset: { index } }, detail: { value: index } });
const tick = () => new Promise(resolve => setImmediate(resolve));

function pageWith(role = 'supervisor', overrides = {}) {
  let page;
  const state = { visits, opportunities, members };
  const dashboardCalls = [];
  let directoryCalls = 0;
  const api = {
    getDashboard: async personal => {
      dashboardCalls.push(personal);
      return personal ? data('self', state.visits.filter(item => item.recorder_id === 'a'), state.opportunities.filter(item => item.owner_id === 'a')) : data('workspace', state.visits, state.opportunities);
    },
    getDashboardRankings:async q=>({data_source:'database',contract_version:2,scope:q.personal?'peer':'company_teams',selection:{personal:q.personal,member_id:q.member_id||'a',team_groups:q.team_groups||[]},opportunity_acv:{rows:[],groups:[]},followup:{calculation:q.personal?'personal':'team_followup_per_capita_v1',rows:[],groups:[]},region:{rows:[],groups:[]},active_opportunities:{rows:[],groups:[]}}),
    getDashboardOptions:async()=>({members:state.members,team_groups:[{code:'north_east',name:'北区＋东区'},{code:'south_hkmo',name:'南区＋港澳'}]}),
    getDirectoryMembers: async () => { directoryCalls += 1; return { items: state.members }; },
    ...overrides,
  };
  const filename = path.resolve(__dirname, '../miniprogram/pages/bi/index.js');
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: value => { page = value; },
    require: name => name.endsWith('apiClient') ? api : require(path.resolve(path.dirname(filename), name)),
    Date, Set, Map,
    getApp: () => ({ globalData: { role, session: { role, capabilities:{'team.view':['supervisor','manager'].includes(role)}, workspaceId: 'test-workspace', userId: 'a', userName: '销售甲', teamIds: ['north-id'] } }, ensureLogin: () => true }),
    wx: { showNavigationBarLoading() {}, hideNavigationBarLoading() {}, showToast() {} },
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = values => Object.assign(page.data, values);
  return { page, state, dashboardCalls, getDirectoryCalls: () => directoryCalls };
}

test('本人金额与日柱图裁剪他人明细，排名不依赖目录',async()=>{
 const {page,getDirectoryCalls}=pageWith('sales',{getDashboard:async()=>data('self',visits,opportunities)});
 await page.loadData();await tick();assert.equal(page.data.totalAcv,'¥10,000');assert.equal(page.data.weeklyVisitCount,2);assert.equal(getDirectoryCalls(),0);
});
test('部门金额接受完整授权或部门响应，拒绝单团队及本人响应冒充部门',async()=>{
 for(const scope of ['workspace','all','department','team','self']){
  const {page}=pageWith('manager',{getDashboard:async()=>data(scope)});await page.loadData();await tick();
  if(['workspace','all','department'].includes(scope)){assert.equal(page.data.loadError,'');assert.equal(page.data.kpis[4].value,'¥30,000');}
  else{assert.equal(page.data.kpis.length,0);assert.match(page.data.loadError,/范围/);}
 }
});
test('总经理可切换个人视角，默认金额使用部门整体',async()=>{
 const {page,dashboardCalls,getDirectoryCalls}=pageWith('manager');await page.loadData();await tick();
 assert.deepEqual(dashboardCalls,[false]);assert.equal(getDirectoryCalls(),0);assert.equal(page.data.teamLabel,'部门合计 · 全部团队');
 await page.changeView({currentTarget:{dataset:{mode:'personal'}}});assert.equal(page.data.viewMode,'personal');
});
test('团队多季度金额与倒计时保留所选季度，活跃不由在推数量代替',async()=>{
 const deals=[{...opportunities[0],expected_close_date:`${current.year}-01-15`},{...opportunities[1],expected_close_date:`${current.year}-07-15`}];
 const {page}=pageWith('manager',{getDashboard:async()=>data('workspace',visits,deals)});await page.loadData();await tick();
 page.setData({selectedQuarterKeys:[`${current.year}-Q1`]});page.syncQuarterFilter();page.rebuild();await tick();
 assert.equal(page.data.totalAcv,'¥10,000');assert.equal(page.data.activeOpportunityCount,0);
 page.setData({selectedQuarterKeys:[`${current.year}-Q1`,`${current.year}-Q3`]});page.syncQuarterFilter();page.rebuild();await tick();
 assert.equal(page.data.totalAcv,'¥30,000');assert.equal(page.data.opportunityCount,2);assert.match(page.data.countdown.sentence,/Q3/);
});
test('排名独立失败不阻断部门金额，金额失败不借用个人值',async()=>{
 const {page}=pageWith('manager',{getDashboardRankings:async()=>{throw Error('排名中断');}});await page.loadData();await tick();
 assert.equal(page.data.totalAcv,'¥30,000');assert.equal(page.data.rankingMessage,'排名中断');
 const {page:other}=pageWith('manager',{getDashboard:async()=>{throw Error('金额中断');}});await other.loadData();await tick();
 assert.equal(other.data.totalAcv,'—');assert.equal(other.data.rankingMessage,'');assert.equal(other.data.loadError,'金额中断');
});
test('刷新团队金额失败清空旧值，季度变化也不复用失败前金额',async()=>{
 let fail=false;const {page}=pageWith('manager',{getDashboard:async()=>{if(fail)throw Error('offline');return data('workspace');}});
 await page.loadData();await tick();assert.equal(page.data.totalAcv,'¥30,000');
 fail=true;await page.loadFacts();assert.equal(page.data.totalAcv,'—');assert.equal(page.data.kpis.length,0);
 page.resetQuarters();await tick();assert.equal(page.data.totalAcv,'—');assert.equal(page.data.loadError,'offline');
});
test('迟到的个人或团队金额响应不能覆盖新视角，页面卸载使响应失效',async()=>{
 let resolveOld;const {page}=pageWith('supervisor',{getDashboard:personal=>personal?new Promise(resolve=>resolveOld=resolve):Promise.resolve(data('workspace'))});
 page.loadData();await tick();await page.changeView({currentTarget:{dataset:{mode:'team'}}});await tick();
 resolveOld(data('self'));await tick();assert.equal(page.data.viewMode,'team');assert.equal(page.data.totalAcv,'¥30,000');
 let resolveLate;const {page:closed}=pageWith('sales',{getDashboard:()=>new Promise(resolve=>resolveLate=resolve)});
 closed.loadData();closed.onUnload();resolveLate(data('self'));await tick();assert.equal(closed._raw,null);
});
test('本人数据未取得本人scope时明确失败',async()=>{
 for(const scope of ['team','workspace']){
  const {page}=pageWith('sales',{getDashboard:async()=>data(scope)});await page.loadData();await tick();assert.match(page.data.loadError,/成员/);
 }
});
