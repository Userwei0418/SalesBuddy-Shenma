import test from 'node:test';
import assert from 'node:assert/strict';
import {customRange, dayKey, periodRange, statisticsRange} from '../department-ui/pc/period.js';
import {byMember, clearCache, collectFollowups, filterVisitsToScope, groupBy, loadCompletedTasks, loadRisks, loadTasks, mapParams, normalizeVisit, summarize, visitSource} from '../department-ui/pc/data.js';
import {composeWorkReport} from '../department-ui/pc/report.js';
import {composePlaybook, composeProposal, normalizeCatalog, playbookModel} from '../department-ui/pc/knowledge-data.js';
import {SAMPLE_CATALOG} from '../department-ui/pc/knowledge-sample.js';

const at = '2026-10-06T10:00:00+08:00';
const now = Date.parse(at);

test('periods follow Beijing natural days, Monday weeks, months and quarters', () => {
  assert.deepEqual([periodRange('day', now).start, periodRange('day', now).end], ['2026-10-06', '2026-10-06']);
  assert.deepEqual([periodRange('week', now).start, periodRange('week', now).end], ['2026-10-05', '2026-10-11']);
  assert.deepEqual([periodRange('biweek', now).start, periodRange('biweek', now).end], ['2026-09-28', '2026-10-11']);
  assert.deepEqual([periodRange('month', now).start, periodRange('month', now).end], ['2026-10-01', '2026-10-31']);
  assert.deepEqual([periodRange('quarter', now).start, periodRange('quarter', now).end], ['2026-10-01', '2026-12-31']);
  assert.equal(periodRange('week', now).elapsed, 2);
  assert.equal(periodRange('unknown', now).kind, 'day');
  // 周日仍属于本周（周一开始）
  const sunday = Date.parse('2026-10-11T23:00:00+08:00');
  assert.equal(periodRange('week', sunday).start, '2026-10-05');
  assert.deepEqual([customRange('2026-10-09', '2026-10-01', now).start, customRange('2026-10-09', '2026-10-01', now).end], ['2026-10-01', '2026-10-09']);
  assert.equal(dayKey('2026-10-05T16:30:00Z'), '2026-10-06');
  assert.equal(dayKey('2026-10-06 09:00'), '2026-10-06');
  assert.equal(dayKey(''), '');
});

const people = new Map([
  ['s1', {id: 's1', name: '王一', role: 'sales', team_ids: ['t1'], teamLabel: '南区'}],
  ['s2', {id: 's2', name: '李二', role: 'sales', team_ids: ['t1'], teamLabel: '南区'}],
  ['f1', {id: 'f1', name: '周三', role: 'fde', team_ids: ['tf'], teamLabel: 'FDE'}],
]);
const raw = [
  {id: 'v1', recorder_team_id: 't1', customer_id: 'c1', customer_name: '甲公司', opportunity_id: 'o1', opportunity_name: '试点', recorder_id: 's1', recorder_name: '王一', visit_date: '2026-10-06', follow_up_record: '沟通试点', next_action: '周五发方案', interaction_mode_code: 'online_meeting', status: 'archived'},
  {id: 'v2', recorder_team_id: 't1', customer_id: 'c2', customer_name: '乙公司', recorder_id: 's1', recorder_name: '王一', visit_date: '2026-10-05', follow_up_record: '电话沟通', next_action: '', status: 'archived'},
  {id: 'v3', customer_id: 'c1', customer_name: '甲公司', opportunity_ids: ['o1', 'o2'], recorder_id: 'f1', recorder_name: '周三', visit_date: '2026-10-06', follow_up_record: '技术交流', next_action: '联调', status: 'archived'},
  {id: 'v4', customer_id: 'c3', customer_name: '丙公司', recorder_id: 'x', recorder_name: '外部', visit_date: '2026-10-06', follow_up_record: '同步', source_system: 'ProjectBuddy', status: 'archived'},
];
const visits = raw.map(row => normalizeVisit(row, people));

test('visits normalize labels, linked opportunities and source without inventing fields', () => {
  assert.equal(visits[0].mode, '线上会议');
  assert.deepEqual(visits[2].opportunityIds, ['o1', 'o2']);
  assert.equal(visits[0].source, 'sales');
  assert.equal(visits[2].source, 'fde');
  assert.equal(visits[3].source, 'projectbuddy');
  assert.equal(visitSource({recorder_id: 'f9', fde_participant_ids: ['f9']}), 'fde');
  assert.equal(normalizeVisit({id: 'fde', recorder_user_id: 'f1', recording_role_code_snapshot: 'fde', customer_id: 'c1'} , people).recorderId, 'f1');
  assert.equal(visitSource({recorder_user_id: 'f1', recording_role_code_snapshot: 'fde'}, people), 'fde');
  assert.equal(normalizeVisit({id: 'x', customer_id: '', follow_up_record: null}).customerName, '未关联客户');
  assert.equal(mapParams({kind: 'team', teamId: 't1'}, {role: 'sales', teamIds: ['t1']}, {kind: 'quarter'}).activity_window, 'year');
});

test('summaries count active people per team member and per-capita over all members, including zero', () => {
  const members = [people.get('s1'), people.get('s2')];
  const summary = summarize(visits, members);
  assert.equal(summary.visits, 4);
  assert.equal(summary.customers, 3);
  assert.equal(summary.opportunities, 2);
  assert.equal(summary.activeMembers, 1);
  assert.equal(summary.members, 2);
  assert.equal(summary.perCapita, 1); // 王一 2 条 ÷ 2 人；FDE 与外部记录不计入团队人均
  assert.equal(summary.presales, 2);
  const rows = byMember(visits, members);
  assert.deepEqual(rows.map(r => [r.name, r.count]), [['王一', 2], ['外部', 1], ['周三', 1], ['李二', 0]]); // 同条数按姓名拼音排序
  assert.equal(rows.find(r => r.name === '周三').outside, true);
  const byOpportunity = groupBy(visits, 'opportunity');
  assert.equal(byOpportunity.find(g => g.key === 'o1').count, 2);
  assert.equal(byOpportunity.find(g => g.key === 'none').count, 2);
  assert.equal(groupBy(visits, 'customer').find(g => g.key === 'c1').people.length, 2);
});

test('follow-up scope filters by recorder, never by shared customer', () => {
  const directory = {members: [people.get('s1'), people.get('s2')], teams: [{id: 't1', name: '南区'}]};
  assert.deepEqual(filterVisitsToScope(visits, {kind: 'self'}, directory, {userId: 's1'}).map(v => v.id), ['v1', 'v2']);
  assert.deepEqual(filterVisitsToScope(visits, {kind: 'member', memberId: 's2'}, directory, {userId: 's1'}).map(v => v.id), []);
  assert.deepEqual(filterVisitsToScope(visits, {kind: 'team', teamId: 't1'}, directory, {userId: 's1'}).map(v => v.id), ['v1', 'v2']);
});

test('FDE activity hydrates readable detail without replacing summary identity', async () => {
  const previous = globalThis.SalesRuntime;
  const calls = [];
  globalThis.SalesRuntime = {
    app: {globalData: {session: {workspaceId: 'w', userId: 'f1', role: 'fde', permissionVersion: 1, loginAt: 1}}},
    require: () => ({
      getFdeActivity: async () => ({items: [{id: 'vf', customer_id: 'c1', customer_name: '甲公司', opportunity_id: 'o1', opportunity_name: '试点', interaction_at: '2026-10-06T01:00:00Z', recorder_user_id: 'f1', recorder_name: '周三', can_read_detail: true}], total: 1, has_more: false}),
      getVisit: async id => { calls.push(id); return {id: 'wrong-id', customer_id: 'other', interaction_at: '2026-10-07T01:00:00Z', recorder_id: 'other', follow_up_record: '技术交流', next_action: '发方案', status: 'archived'}; },
    }),
  };
  try {
    clearCache();
    const result = await collectFollowups({range: {kind: 'day', start: '2026-10-06', end: '2026-10-06'}, scope: {kind: 'self'}, directory: {members: [people.get('f1')], people: new Map([['f1', people.get('f1')]])}});
    assert.deepEqual(calls, ['vf']);
    assert.equal(result.items.length, 1);
    assert.equal(result.items[0].id, 'vf');
    assert.equal(result.items[0].customerId, 'c1');
    assert.equal(result.items[0].date, '2026-10-06');
    assert.equal(result.items[0].recorderId, 'f1');
    assert.equal(result.items[0].content, '技术交流');
    assert.equal(result.items[0].nextAction, '发方案');
    assert.equal(result.coverage.detailPartial, false);
  } finally { globalThis.SalesRuntime = previous; clearCache(); }
});

test('sales follow-up aggregation uses the range contract before legacy per-customer reads', async () => {
  const previous = globalThis.SalesRuntime;
  const calls = [];
  globalThis.SalesRuntime = {
    app: {globalData: {session: {workspaceId: 'w', userId: 's1', role: 'sales', permissionVersion: 1, loginAt: 1, teamIds: ['t1']}}},
    require: () => ({
      listVisitRange: async params => { calls.push(params); return {items: [{id: 'vr1', customer_id: 'c1', customer_name: '甲公司', recorder_id: 's1', recorder_name: '王一', visit_date: '2026-10-06', follow_up_record: '范围查询', status: 'archived'}], summary: {total: 1}, has_more: false}; },
      getCustomerMap: async () => { throw new Error('不应回退到逐客户地图'); },
    }),
  };
  try {
    clearCache();
    const result = await collectFollowups({range: {kind: 'day', start: '2026-10-06', end: '2026-10-06'}, scope: {kind: 'self'}, directory: {members: [people.get('s1')], people: new Map([['s1', people.get('s1')]])}});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].scope, 'mine');
    assert.equal(calls[0].date_from, '2026-10-06');
    assert.deepEqual(result.items.map(row => row.id), ['vr1']);
    assert.equal(result.coverage.source, 'range');
  } finally { globalThis.SalesRuntime = previous; clearCache(); }
});

test('sales and FDE biweek queries use calendar start and today cutoff', async () => {
  const previous = globalThis.SalesRuntime;
  const range = periodRange('biweek', Date.parse('2026-10-08T12:00:00+08:00'));
  try {
    for (const role of ['sales', 'fde']) {
      const calls = [];
      const read = async params => { calls.push(params); return {items: [], total: 0, has_more: false}; };
      globalThis.SalesRuntime = {
        app: {globalData: {session: {workspaceId: 'w', userId: 's1', role, teamIds: ['t1']}}},
        require: () => ({listVisitRange: read, getFdeActivity: read}),
      };
      clearCache();
      await collectFollowups({range, scope: {kind: 'self'}, directory: {members: [], people: new Map()}});
      assert.deepEqual(calls.map(p => [p.date_from, p.date_to]), [['2026-09-28', '2026-10-08']], role);
      assert.equal(range.end, '2026-10-11');
    }
  } finally { globalThis.SalesRuntime = previous; clearCache(); }
});

test('task queries use only supported member filters and expose partial scope', async () => {
  const previous = globalThis.SalesRuntime;
  const calls = [];
  globalThis.SalesRuntime = {
    app: {globalData: {session: {workspaceId: 'w', userId: 's1', role: 'sales', permissionVersion: 1, loginAt: 1}}},
    require: () => ({listTaskPage: async params => { calls.push(params); return {items: [], summary: {filtered_total: 101}, has_more: true}; }}),
  };
  try {
    const member = await loadTasks({kind: 'member', memberId: 's2'});
    const team = await loadTasks({kind: 'team', teamId: 't1'});
    assert.equal(calls[0].member_id, 's2');
    assert.equal('team_id' in calls[0], false);
    assert.equal('member_id' in calls[1], false);
    assert.equal('team_id' in calls[1], false);
    assert.equal(member.partial, true);
    assert.deepEqual(team.scope, {kind: 'team', memberId: '', teamId: 't1'});
  } finally { globalThis.SalesRuntime = previous; }
});

test('completed task query narrows to the range year and quarter when representable', async () => {
  const previous = globalThis.SalesRuntime;
  let query;
  globalThis.SalesRuntime = {
    app: {globalData: {session: {workspaceId: 'w', userId: 's1', role: 'sales', permissionVersion: 1, loginAt: 1}}},
    require: () => ({listTaskPage: async params => { query = params; return {items: [], has_more: false}; }}),
  };
  try {
    await loadCompletedTasks({start: '2026-10-01', end: '2026-12-31'}, {kind: 'self'});
    assert.equal(query.completed_year, 2026);
    assert.deepEqual(query.completed_quarters, [4]);
  } finally { globalThis.SalesRuntime = previous; }
});

test('risk cards preserve forbidden errors instead of claiming an empty view', async () => {
  const previous = globalThis.SalesRuntime;
  globalThis.SalesRuntime = {
    app: {globalData: {session: {workspaceId: 'w', userId: 's1', role: 'sales', permissionVersion: 1, loginAt: 1}}},
    require: () => ({listRisks: async () => { throw Object.assign(new Error('禁止访问'), {code: 'HTTP_403', statusCode: 403}); }}),
  };
  try {
    assert.deepEqual(await loadRisks(), {items: [], error: '禁止访问'});
  } finally { globalThis.SalesRuntime = previous; }
});

test('work report lists only recorded facts and flags missing next steps', () => {
  const members = byMember(visits, [people.get('s1'), people.get('s2')]);
  const summary = summarize(visits, [people.get('s1'), people.get('s2')]);
  const report = composeWorkReport({kind: 'day', range: periodRange('day', now), scopeName: '南区 · ', author: '主管', summary, members,
    opportunities: [{id: 'o1', name: '试点', customerName: '甲公司', stage: '方案沟通', amount: 1280000, close: '2026-12-25', lastVisit: visits[0]}],
    newOpportunities: [], completedTasks: [{id: 't1', title: '整理清单'}], openTasks: {total: 3, overdue: [{id: 't2', title: '回访', owner: '王一'}]}, risks: [], generatedAt: '2026-10-06 10:00'});
  assert.match(report.title, /南区 · 日报/);
  assert.match(report.text, /跟进 4 条，覆盖客户 3 家、商机 2 个/);
  assert.match(report.text, /本期未跟进：李二/);
  assert.match(report.text, /（未写下一步）/);
  assert.match(report.text, /128 万/);
  assert.match(report.text, /逾期任务：回访（王一）/);
  assert.match(report.text, /未做 AI 推断/);
  assert.doesNotMatch(report.text, /提升|保证/);
});

test('biweekly report uses the previous and current Monday-to-Sunday weeks', () => {
  const members = byMember(visits, [people.get('s1'), people.get('s2')]);
  const summary = summarize(visits, [people.get('s1'), people.get('s2')]);
  const report = composeWorkReport({kind: 'biweek', range: periodRange('biweek', now), scopeName: '南区 · ', author: '主管', summary, members});
  assert.match(report.title, /南区 · 双周报/);
  assert.match(report.title, /9月28日–10月11日/);
});

test('biweek boundaries stay fixed within a week and switch at Beijing Monday midnight', () => {
  const cases = [
    ['2026-10-08T12:00:00+08:00', '2026-09-28', '2026-10-11', 11],
    ['2026-10-11T15:59:59Z', '2026-09-28', '2026-10-11', 14],
    ['2026-10-11T16:00:00Z', '2026-10-05', '2026-10-18', 8],
    ['2027-01-01T12:00:00+08:00', '2026-12-21', '2027-01-03', 12],
    ['2028-03-01T12:00:00+08:00', '2028-02-21', '2028-03-05', 10],
  ];
  for (const [date, start, end, elapsed] of cases) {
    const range = periodRange('biweek', Date.parse(date));
    assert.deepEqual([range.start, range.end, range.elapsed, range.name], [start, end, elapsed, '双周'], date);
  }
});

test('statistics stop at today while period headings retain their complete calendar range', () => {
  for (const kind of ['day', 'week', 'biweek', 'month', 'quarter']) {
    const range = periodRange(kind, now), calendarEnd = range.end;
    assert.equal(statisticsRange(range).end, '2026-10-06');
    assert.equal(range.end, calendarEnd, 'data queries must not mutate the displayed calendar range');
  }
  assert.equal(statisticsRange(customRange('2026-09-01', '2026-09-30', now)).end, '2026-09-30');
});

test('catalog, playbook and proposal drafts keep sample content labeled and cite the chosen sources', () => {
  assert.equal(normalizeCatalog(null).empty, true);
  const catalog = normalizeCatalog(SAMPLE_CATALOG);
  assert.equal(catalog.sample, true);
  assert.ok(catalog.items.every(item => item.id && item.title));
  assert.ok(catalog.items.some(item => item.status === 'archived'));
  const model = playbookModel({
    overview: {id: 'c1', name: '甲公司', owner_name: '王一', industry_code: '制造业', level_code: 'A', quadrant_code: 'main_attack', potential_score: 80, relationship_score: 50},
    contacts: {rows: [{name: '张三', title: '信息化负责人', relationship_role_code: 'decision_maker', is_primary: true}]},
    opportunities: {rows: [{id: 'o1', name: '试点', stage_code: 'solution', status: 'open', amount: 1280000, expected_close_date: '2026-12-25', product_line: '智能质检', owner_name: '王一'}]},
    visits: {rows: [raw[0], {...raw[2], fields: {customer_needs: '降低误报', customer_budget: '年度专项'}}]}, tasks: [{id: 't1', description: '发方案', due_at: '2026-10-09T09:00:00+08:00'}], risks: [],
  }, catalog);
  assert.equal(model.open.length, 1);
  assert.ok(model.recommended.some(item => item.product_line === '智能质检'));
  const playbook = composePlaybook(model, '2026-10-06');
  assert.match(playbook, /张三 · 信息化负责人 · 决策者（首要联系人）/);
  assert.match(playbook, /需求 降低误报；预算 年度专项/);
  const solution = catalog.items.find(item => item.id === 'sol-qc-2'), pricing = catalog.items.find(item => item.id === 'price-qc');
  assert.equal(composeProposal({model, opportunity: model.opportunities[0], solution: null, pricing, generatedOn: '2026-10-06'}), '');
  const draft = composeProposal({model, opportunity: model.opportunities[0], solution, pricing, generatedOn: '2026-10-06'});
  assert.match(draft, /草稿/);
  assert.match(draft, /\| 标准版 \| 每条产线 \/ 年 \| ¥ 80,000/);
  assert.match(draft, /报价以审批结果为准/);
  assert.match(draft, /不预设提升比例/);
});
