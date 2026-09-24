const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

test('作战地图使用单行胶囊筛选，并移除快捷筛选', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  const wxss = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxss', 'utf8');
  assert.doesNotMatch(wxml, /map-filter-trigger|toggleFilters|filterExpanded|>收起</);
  assert.match(wxml, /class="operating-filters surface"/);
  assert.match(wxml, /class="operating-filter-title">筛选</);
  assert.match(wxml, /class="operating-picker-scroll" scroll-x/);
  assert.match(wxml, /class="operating-picker-row"/);
  assert.equal((wxml.match(/class="operating-picker filter-/g) || []).length, 5);
  assert.match(wxml, /class="operating-picker filter-level/);
  assert.match(wxml, />客户优先级</);
  assert.match(wxml, /mapSelectedLevels/);
  assert.match(wxml, /class="map-level-panel"/);
  assert.doesNotMatch(wxml, />状态</);
  assert.doesNotMatch(wxml, />跟进</);
  assert.doesNotMatch(wxml, /operating-quick|tapFilter/);
  assert.doesNotMatch(wxml, /operating-filter-footer|地图与列表同步筛选/);
  assert.match(wxml, /wx:if="\{\{mapFilterActive\}\}" class="operating-filter-reset"/);
  assert.match(wxss, /filter-bar\.wxss/);

  const filterCss = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/filter-bar.wxss', 'utf8');
  assert.match(filterCss, /\.operating-picker-row\s*\{[^}]*display:\s*inline-flex/);
  assert.match(filterCss, /\.operating-picker-content\s*\{[^}]*display:\s*flex/);
  assert.match(filterCss, /\.operating-picker\s*\{[^}]*height:\s*50rpx/);
  assert.match(filterCss, /filter-plan,[\s\S]*filter-level,[\s\S]*filter-amount,[\s\S]*filter-team,[\s\S]*filter-member\s*\{\s*width:\s*188rpx/);
  assert.match(wxss, /\.asset-period\{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
});

test('作战地图提供客户认领入口并进入独立页面', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  const js = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.js', 'utf8');
  assert.match(wxml, /class="map-claim-entry" bindtap="openCustomerClaim">客户认领/);
  assert.match(js, /openCustomerClaim\(\)[\s\S]*navigateTo\(\{ url: "\/pages\/customer-claim\/index" \}\)/);
});

test('客户资产卡移除说明行和信息提示以压缩空间', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  assert.doesNotMatch(wxml, /asset-help|asset-caption|客户资产口径说明/);
});

test('作战地图客户标签统一显示 Tier 等级，并把旧 A/B/C 映射到新等级', () => {
  const detail = require('../miniprogram/utils/customerDetail');
  assert.equal(detail.normalizeCustomerSummary({ level_code: 'Tier-1' }).level, 'Tier-1');
  assert.equal(detail.normalizeCustomerSummary({ level_code: 'Tier 2' }).level, 'Tier-2');
  assert.equal(detail.normalizeCustomerSummary({ level_code: 'A' }).level, 'Tier-1');
  assert.equal(detail.normalizeCustomerSummary({ level_code: 'B级' }).level, 'Tier-2');
  assert.equal(detail.normalizeCustomerSummary({ level: 'C' }).level, 'Tier-3');
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  assert.match(wxml, /wx:if="\{\{item\.level\}\}" class="level"/);
});

test('管理角色复用地图筛选条并按角色增加范围筛选', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  const filterCss = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/filter-bar.wxss', 'utf8');
  assert.doesNotMatch(wxml, /class="asset-scope-panel"/);
  assert.match(wxml, /wx:if="\{\{canViewTeam\}\}" class="operating-picker filter-team/);
  assert.match(wxml, /wx:if="\{\{!isFde && canViewTeam\}\}" class="operating-picker filter-member/);
  assert.match(wxml, /role === 'supervisor' \? '团队成员' : '人员'/);
  assert.match(wxml, /bindchange="changeTeam"/);
  assert.match(wxml, /bindchange="changeMember"/);
  assert.match(filterCss, /\.operating-picker\.filter-team/);
  assert.match(filterCss, /\.operating-picker\.filter-member/);
});

test('编辑客户复用 Tier 等级字段并提交 level_code', () => {
  const js = fs.readFileSync(__dirname + '/../miniprogram/pages/customer-edit/index.js', 'utf8');
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customer-edit/index.wxml', 'utf8');
  assert.match(js, /CUSTOMER_LEVELS/);
  assert.match(js, /level_code: normalizeCustomerLevel/);
  assert.match(wxml, />客户优先级 \*</);
  assert.match(wxml, /bindchange="changeCustomerLevel"/);
});

test('客户经营详情不显示顶部快捷操作栏', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  const js = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.js', 'utf8');
  assert.doesNotMatch(wxml, /customer-context-actions/);
  assert.doesNotMatch(wxml, /bindtap="recordVisit"/);
  assert.doesNotMatch(js, /recordVisit\s*\(/);
});

test('客户详情商机卡片底部只保留只读实绩明细入口', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  const js = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.js', 'utf8');
  assert.doesNotMatch(wxml, /opportunity-context-actions|opportunity-related/);
  assert.equal((wxml.match(/class="opportunity-actual-link"/g) || []).length, 2);
  assert.doesNotMatch(wxml, />跟进与待办/);
  assert.match(js, /viewOpportunityActuals\s*\(/);
  assert.match(js, /opportunity_id=.*readonly=1/);
  assert.doesNotMatch(js, /relatedOpportunityId|toggleOpportunityRecords\s*\(|openCustomerAssets\s*\(/);
});

test('客户概览商机只提供实绩查看，商机进展仍可编辑', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  const overviewStart = wxml.indexOf("detailTab === 'overview'");
  const overviewEnd = wxml.indexOf('<view wx:if="{{detailTab === \'opportunity\'}}" class="customer-signal-legend">', overviewStart);
  assert.ok(overviewStart >= 0 && overviewEnd > overviewStart, 'overview section boundaries must exist');
  const overview = wxml.slice(overviewStart, overviewEnd);
  const opportunity = wxml.split("<view wx:if=\"{{detailTab === 'opportunity'}}\">")[1];
  assert.match(overview, /opportunity-actual-link/);
  assert.doesNotMatch(overview, /(?:bindtap|catchtap)="editOpportunity"|修改人工商机信息/);
  assert.match(opportunity, /catchtap="editOpportunity"/);
});

test('拜访记录从列表进入新的只读详情页', () => {
  const listWxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.wxml', 'utf8');
  const detailWxml = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-detail/index.wxml', 'utf8');
  const listJs = fs.readFileSync(__dirname + '/../miniprogram/pages/customers/index.js', 'utf8');
  assert.match(listWxml, /bindtap="openVisitDetail"/);
  assert.match(listWxml, /查看完整记录/);
  assert.doesNotMatch(listWxml, /点击展开|点击收起|visit-expanded|toggleVisit/);
  assert.match(listJs, /url: visitDetailUrl\(visit\)/);
  for (const label of ['拜访方式','拜访时长','达成结果','是否首次拜访','客户主营业务','客户需求','客户预算','联系人角色']) {
    assert.match(detailWxml, new RegExp(label));
  }
  assert.match(detailWxml, /wx:if="\{\{visit\.isFirstVisit\}\}"/);
  assert.match(detailWxml, /已归档正文只读/);
});
