const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const firstVisit = require('../miniprogram/utils/visitFirstVisit');
const flow = require('../miniprogram/utils/visitFlow');

test('首次拜访字段兼容 AI 旧别名并规范金额与角色', () => {
  const values = firstVisit.normalizeValues({
    industry: '企业软件',
    demand_summary: '建设销售系统',
    budget: '50万',
    relationship_role: '最终决策人',
  }, true);
  assert.equal(values.is_first_visit, true);
  assert.equal(values.customer_main_business, '企业软件');
  assert.equal(values.customer_needs, '建设销售系统');
  assert.equal(values.customer_budget, '50 万元');
  assert.equal(values.contact_role, '决策者');
});

test('首次拜访 AI 提示要求只提取事实，普通拜访保持原文', () => {
  const original = '今天拜访客户并确认需求';
  assert.equal(firstVisit.buildAgentText(original, false), original);
  const prompted = firstVisit.buildAgentText(original, true);
  assert.match(prompted, /首次拜访/);
  assert.match(prompted, /客户主营业务、客户需求、客户预算、联系人角色/);
  assert.match(prompted, /不要猜测/);
  assert.match(prompted, new RegExp(original));
});

test('首次拜访四项内容纳入质量审核，修改后会使旧审核失效', () => {
  const values = firstVisit.normalizeValues({
    follow_up_record: '确认客户现状',
    next_action: '2026年9月15日提交方案',
    customer_main_business: '企业软件',
    customer_needs: '建设销售系统',
    customer_budget: '50 万元',
    contact_role: '决策者',
  }, true);
  const reviewed = flow.reviewText(values);
  assert.match(reviewed, /拜访类型：首次拜访/);
  assert.match(reviewed, /客户预算：50 万元/);
  assert.notEqual(flow.reviewText({...values, customer_budget:'60 万元'}), reviewed);
});

test('确认页仅在首次拜访时展示四项补充字段', () => {
  const entry = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-entry/index.wxml', 'utf8');
  const confirm = fs.readFileSync(__dirname + '/../miniprogram/pages/visit-confirm/index.wxml', 'utf8');
  assert.match(entry, /checkbox[\s\S]*首次拜访/);
  assert.match(confirm, /wx:if="\{\{isFirstVisit\}\}" class="first-visit-section"/);
  assert.deepEqual(firstVisit.REQUIRED_FIELDS.map((field) => field.label), ['客户主营业务', '客户需求', '客户预算', '联系人角色']);
});
