require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

test('商机页的查看入口直接打开只读商机详情', () => {
  const js = fs.readFileSync(__dirname + '/../miniprogram/pages/opportunities/index.js', 'utf8');
  assert.match(js, /pages\/customer-assets\/index\?customer_id=/);
  assert.match(js, /opportunity_id=.*period=all&readonly=1/);
  assert.doesNotMatch(js, /pendingOpenCustomerId|switchTab/);
});

test('商机经营首页整合总览、筛选和商机卡片', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/workbench/index.wxml', 'utf8');
  const js = fs.readFileSync(__dirname + '/../miniprogram/pages/workbench/index.js', 'utf8');
  const wxss = fs.readFileSync(__dirname + '/../miniprogram/pages/workbench/index.wxss', 'utf8');
  const opportunityStart = wxml.indexOf('class="overview-board opportunity-overview opportunity-summary-card surface"');
  const opportunityCard = wxml.slice(opportunityStart);
  assert.doesNotMatch(wxml, /class="summary surface"/);
  assert.match(opportunityCard, /opportunity-summary-card/);
  assert.match(opportunityCard, />商机总览</);
  const metricLabels = ['已成单商机', '全部商机', '活跃商机', '新增商机'];
  const positions = metricLabels.map(label => opportunityCard.indexOf(`>${label}<`));
  assert.ok(positions.every((position, index) => position >= 0 && (!index || position > positions[index - 1])));
  assert.match(opportunityCard, /opportunity-summary-won/);
  assert.match(opportunityCard, /data-scope="summary"/);
  assert.match(opportunityCard, /data-scope="list"/);
  assert.doesNotMatch(opportunityCard, /opportunity-summary-label">[^<]*<text>›<\/text>/);
  assert.doesNotMatch(opportunityCard, /mini-opportunities/);
  assert.doesNotMatch(wxml, />\{\{role === 'sales' \? '个人执行' : '团队执行'\}\}</);
  assert.doesNotMatch(wxml, /class="member-list surface"/);
  assert.doesNotMatch(opportunityCard, /opportunity-summary-foot|opportunity-summary-stats|class="opportunity-link"|openAllOpportunities/);
  assert.match(opportunityCard, /id="workbench-opportunity-list"/);
  assert.match(wxml, />新增商机</);
  assert.doesNotMatch(wxml, /<text>＋<\/text><label>新增商机<\/label>/);
  assert.ok(wxml.indexOf('head-create-opportunity') < opportunityStart);
  assert.doesNotMatch(wxml, /截至\{\{sourceDate\}\}/);
  assert.doesNotMatch(wxml, /class="workbench-create-row"/);
  assert.match(js, /canCreateOpportunity:\s*can\(session,'opportunity.create'\)/);
  assert.equal((opportunityCard.match(/class="workbench-filter-item filter-/g) || []).length, 5);
  assert.match(opportunityCard, /wx:if="\{\{canViewTeam\}\}" class="workbench-filter-item filter-team"/);
  assert.match(opportunityCard, /bindchange="changeExecutionTeam"/);
  assert.match(opportunityCard, /wx:if="\{\{canViewTeam\}\}" class="workbench-filter-item filter-owner"/);
  assert.match(opportunityCard, /class="workbench-filter-item filter-stage/);
  assert.match(opportunityCard, /class="workbench-stage-panel"/);
  assert.match(opportunityCard, /bindtap="toggleOpportunityStage"/);
  assert.doesNotMatch(opportunityCard, /filter-amount|>金额</);
  assert.match(opportunityCard, /template is="opportunity-list-card"/);
  const sharedCard=fs.readFileSync(__dirname+'/../miniprogram/templates/opportunity-list-card.wxml','utf8');
  assert.match(sharedCard, /grade-\{\{item\.gradeCode\}\}/);
  assert.match(opportunityCard, /class="workbench-opportunity-card surface"/);
  assert.match(opportunityCard, /bindtap="openOpportunity"/);
  assert.doesNotMatch(wxml, />商机待办</);
  assert.doesNotMatch(wxml, />商机风险</);
  assert.doesNotMatch(js, /openAllOpportunities|pageScrollTo\(\{ selector: "#workbench-opportunity-list"/);
  assert.match(js, /pages\/customer-assets\/index\?customer_id=/);
  assert.doesNotMatch(js, /getWorkbench|baseTasks|baseRisks|baseCustomers/);
  assert.match(js, /listOpportunities/);
  assert.match(wxss, /\.workbench-filter-item\{width:188rpx/);
});

test('总经理在商机列表和商机经营页均可创建商机', () => {
  const workbench = fs.readFileSync(__dirname + '/../miniprogram/pages/workbench/index.js', 'utf8');
  const opportunities = fs.readFileSync(__dirname + '/../miniprogram/pages/opportunities/index.js', 'utf8');
  assert.match(workbench, /can\(session,'opportunity.create'\)/);
  assert.match(opportunities, /access\.can\(session,'opportunity.create'\)/);
});

test('商机经营页移除总监和总经理的客户下发入口与弹层', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/workbench/index.wxml', 'utf8');
  const js = fs.readFileSync(__dirname + '/../miniprogram/pages/workbench/index.js', 'utf8');
  assert.doesNotMatch(wxml, /下发客户|下发新客户|assignment-entry|assignment-layer/);
  assert.doesNotMatch(js, /openCustomerAssignment|confirmCustomerAssignment|assignCustomer/);
});

test('商机待办和商机风险列表可返回对应商机卡片', () => {
  for (const page of ['tasks', 'risks']) {
    const wxml = fs.readFileSync(`${__dirname}/../miniprogram/pages/${page}/index.wxml`, 'utf8');
    const js = fs.readFileSync(`${__dirname}/../miniprogram/pages/${page}/index.js`, 'utf8');
    assert.match(wxml, /data-opportunity-id="\{\{item\.opportunityId\}\}"/);
    assert.match(wxml, /catchtap="openOpportunity"/);
    assert.match(js, /pendingOpenCustomerId/);
    assert.match(js, /pendingOpenOpportunityId/);
    assert.match(js, /switchTab\(\{ url: "\/pages\/customers\/index" \}\)/);
  }
});
