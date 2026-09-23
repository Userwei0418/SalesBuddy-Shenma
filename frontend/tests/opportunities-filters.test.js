require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const pageResponse=require('./helpers/opportunity-pages');

function pageDefinition(items, role = 'sales', directoryTeams) {
  let page;
  const filename = path.resolve(__dirname, '../miniprogram/pages/opportunities/index.js');
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: definition => { page = definition; },
    require: name => name.endsWith('apiClient') ? { listOpportunities: async options => pageResponse(items,{...options,directoryTeams}) } : require(path.resolve(path.dirname(filename), name)),
    Date, setTimeout, clearTimeout,
    wx: { showToast() {}, navigateTo() {} },
    getApp: () => ({ ensureLogin: () => true, globalData: { role, session: { scope: role === 'manager' ? '全部团队' : 'self' } } }),
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = function setData(values, callback) { Object.assign(this.data, values); if (callback) callback(); };
  page.onLoad({});
  return page;
}

function waitForLoad() { return new Promise(resolve => setTimeout(resolve, 0)); }

test('商机筛选以销售基础项为准，管理角色按权限追加范围筛选', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/opportunities/index.wxml', 'utf8');
  const wxss = fs.readFileSync(__dirname + '/../miniprogram/pages/opportunities/index.wxss', 'utf8');
  assert.doesNotMatch(wxml, /customerOptions|probabilityOptions|>金额</);
  for (const label of ['商机阶段', '关单日期', '商机等级']) assert.match(wxml, new RegExp(`>${label}<`));
  assert.match(wxml, /role === 'manager'.*filter-team/);
  assert.match(wxml, /role !== 'sales'.*filter-owner/);
  assert.match(wxml, /role === 'supervisor' \? '直属成员' : '人员'/);
  assert.match(wxml, /class="stage-panel"/);
  assert.match(wxml, /class="filter-scroll" scroll-x/);
  assert.match(wxss, /\.filter-item\{width:188rpx/);
});

test('总经理团队筛选会联动人员选项，总监只追加直属成员', async () => {
  const items=[
    {id:'1',status:'open',stage_code:'identified',amount:1,team_name:'南区',owner_name:'甲'},
    {id:'2',status:'open',stage_code:'identified',amount:1,team_name:'南区',owner_name:'乙'},
    {id:'3',status:'open',stage_code:'identified',amount:1,team_name:'北区',owner_name:'丙'},
  ];
  const manager=pageDefinition(items,'manager');manager.onShow();await waitForLoad();
  manager.changeTeam({detail:{value:manager.data.teamOptions.findIndex(item=>item.value==='南区')}});await waitForLoad();
  assert.deepEqual([...manager.data.ownerOptions.map(item=>item.label)],['全部成员','甲','乙']);
  manager.changeFilter({currentTarget:{dataset:{key:'owner'}},detail:{value:2}});await waitForLoad();
  assert.deepEqual([...manager.data.filtered.map(item=>item.id)],['2']);
  const supervisor=pageDefinition(items,'supervisor');supervisor.onShow();await waitForLoad();
  assert.equal(supervisor.data.teamIndex,0);
  assert.deepEqual([...supervisor.data.ownerOptions.map(item=>item.label)],['全部成员','甲','乙','丙']);
});

test('商机筛选按阶段固定概率、关单周期和 ACV 等级工作', async () => {
  const now = new Date(Date.now() + 8 * 3600000);
  const year = now.getUTCFullYear();
  const month = String(now.getUTCMonth() + 1).padStart(2, '0');
  const items = [
    { id: 'a', status: 'open', stage_code: 'proposal', probability: 70, amount: 1000000, expected_close_date: `${year}-${month}-15`, owner_name: '甲' },
    { id: 'b', status: 'open', stage_code: 'qualified', probability: 30, amount: 500000, expected_close_date: `${year}-12-31`, owner_name: '乙' },
    { id: 'c', status: 'open', stage_code: 'solution', probability: 50, amount: 100000, expected_close_date: `${year}-12-31`, owner_name: '乙' },
    { id: 'd', status: 'open', stage_code: 'identified', probability: 10, amount: 99999, expected_close_date: `${year}-12-31`, owner_name: '乙' },
    { id: 'missing', status: 'open', stage_code: 'identified', probability: 10, amount: null, expected_close_date: `${year}-12-31`, owner_name: '乙' },
  ];
  const page = pageDefinition(items);
  page.onShow();
  await waitForLoad();
  assert.match(page.data.stageOptions.find(item => item.value === 'proposal').label, /70%/);
  page.setData({ closeIndex: page.data.closeOptions.findIndex(item => item.value === 'month') });
  await page.applyFilters();
  assert.deepEqual([...page.data.filtered.map(item => item.id)], ['a']);
  page.resetFilters();
  page.toggleStage({currentTarget:{dataset:{value:'identified'}}});
  page.setData({ gradeIndex: page.data.gradeOptions.findIndex(item => item.value === 'D') });
  await page.applyFilters();
  assert.deepEqual([...page.data.filtered.map(item => item.id)], ['d']);
});


test('商机目录包含无业务团队，改名后仍按同一个团队ID筛选',async()=>{
 const items=[{id:'1',status:'open',amount:10,team_id:'team-a',team_name:'旧名字',owner_name:'甲'}];
 const directory=[{id:'team-a',name:'更名后的团队'},{id:'empty',name:'暂无数据团队'}];
 const p=pageDefinition(items,'manager',directory);p.onShow();await waitForLoad();
 assert.deepEqual(Array.from(p.data.teamOptions,row=>row.value),['all','team-a','empty']);
 p.changeTeam({detail:{value:2}});await waitForLoad();assert.equal(p.data.total,0);assert.equal(p.data.filtered.length,0);assert.equal(p.data.teamOptions.length,3);
 p.changeTeam({detail:{value:1}});await waitForLoad();assert.equal(p.data.total,1);assert.equal(p.data.teamOptions[p.data.teamIndex].label,'更名后的团队');assert.equal(p.pageRequest.teamId,'team-a');
});
