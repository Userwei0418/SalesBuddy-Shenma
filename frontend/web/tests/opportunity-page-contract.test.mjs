import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { transformSync } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));

test('opportunity overview query sends each quarter once', () => {
  const source = fs.readFileSync(path.join(root, 'source/miniprogram/utils/apiClient.js'), 'utf8');
  const match = source.match(/function listOpportunities\(options = \{\}\) \{[\s\S]*?\n\}\n\nfunction listAllOpportunities/);
  assert.ok(match, 'listOpportunities source is present');
  const context = { request: value => value };
  vm.runInNewContext(`${match[0].replace(/\n\nfunction listAllOpportunities[\s\S]*$/, '')}\nthis.listOpportunities = listOpportunities;`, context);
  const result = context.listOpportunities({ includeOverview: true, overviewYear: 2026, overviewQuarters: [1, 2, 3, 4], pageSize: 20 });
  const query = new URLSearchParams(result.path.split('?')[1]);
  assert.equal(query.get('include_overview'), 'true');
  assert.equal(query.get('overview_year'), '2026');
  assert.deepEqual(query.getAll('overview_quarters'), ['1', '2', '3', '4']);
});

test('opportunity board defaults to all follow-up activity', () => {
  const source = fs.readFileSync(path.join(root, 'source/miniprogram/pages/opportunities/index.js'), 'utf8');
  assert.match(source, /activityWindowOptions:[\s\S]*?activityWindowIndex: 4/);
  assert.match(source, /resetFilters\(\)[\s\S]*?activityWindowIndex: 4/);
  assert.match(source, /Boolean\(activity && activity\.value !== 'all'\)/);
});

test('opportunity management defaults to the current user roster and prioritizes open records', () => {
  const source = fs.readFileSync(path.join(root, 'source/miniprogram/pages/workbench/index.js'), 'utf8');
  const template = fs.readFileSync(path.join(root, 'source/miniprogram/pages/workbench/index.wxml'), 'utf8');
  assert.match(source, /activityWindowIndex: 4,[\s\S]*?activityWindow: 'all',[\s\S]*?executionScope: 'self',[\s\S]*?executionScopeIndex: 0/);
  assert.match(source, /groupOpportunitiesByQuarter\(items, \{activeFirst: true\}\)/);
  assert.match(source, /const activityWindow = [\s\S]*?\.value \|\| 'all'/);
  assert.match(template, /<view class="title">商机管理<\/view>/);
  assert.match(template, /options="\{\{opt\.idx\(executionScopeOptions,'label'\)\}\}" value="\{\{executionScopeIndex\}\}"/);
  assert.match(template, /placeholder="全部跟进"/);
});

test('active-first opportunity grouping keeps open records ahead of closed records', () => {
  const source = fs.readFileSync(path.join(root, 'source/miniprogram/utils/opportunityQuarter.js'), 'utf8');
  const module = { exports: {} };
  vm.runInNewContext(source, { module, exports: module.exports });
  const groups = module.exports.groupOpportunitiesByQuarter([
    { id: 'won', status: 'won', expected_close_date: '2026-12-01', progressPercent: 100 },
    { id: 'open', status: 'open', expected_close_date: '2027-01-01', progressPercent: 30 },
  ], { activeFirst: true });
  assert.deepEqual(JSON.parse(JSON.stringify(groups.map(group => group.items.map(item => item.id)))), [['open'], ['won']]);
});

test('Web workbench select callbacks translate values into native page indexes', () => {
  const file = path.join(root, 'department-ui/Opportunities.jsx');
  const source = transformSync(fs.readFileSync(file, 'utf8'), { loader: 'jsx', format: 'cjs' }).code;
  const module = { exports: {} };
  vm.runInNewContext(source, {
    module,
    exports: module.exports,
    require: name => name === 'react' ? {...React,
      useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
      useEffect: () => {}, useMemo: factory => factory(), useCallback: factory => factory,
    } : name === './pc/data.js' ? {identity: () => 'contract-test'} : new Proxy({}, { get: (_, key) => key }),
  });
  const calls = [];
  const data = {
    role: 'sales', dataReady: true, opportunityGroups: [], opportunityDataReady: true, opportunityBoard: {},
    summaryQuarter: { quarters: [], options: [] }, listQuarter: { quarters: [] },
    summaryYearOptions: [], opportunityStageOptions: [], opportunityCloseOptions: [], opportunityGradeOptions: [],
    activityWindowOptions: [{ value: 'today', label: '今天' }, { value: 'all', label: '全部' }],
    activityWindow: 'all', executionScope: 'team', canViewTeam: true, executionScopeOptions: [{ value: 'team', label: '授权范围' }, { value: 'self', label: '我的' }],
    opportunitySelectedStages: [], opportunityFilterActive: false,
  };
  const tree = module.exports.default({ page: { route: 'pages/workbench/index' }, data, invoke: (name, payload) => calls.push({ name, payload }) });
  function find(node, label) {
    if (!node || typeof node !== 'object') return null;
    if (node.type && node.props?.label === label) return node.props;
    for (const child of React.Children.toArray(node.props?.children)) {
      const found = find(child, label); if (found) return found;
    }
    return find(node.props?.extra, label);
  }
  find(tree, '跟进时间').onChange('all');
  find(tree, '范围').onChange('self');
  assert.deepEqual(calls.map(call => [call.name, call.payload.detail.value]), [['changeActivityWindow', 1], ['changeExecutionScope', 1]]);
});

test('personal opportunity views are account-isolated and keep the identity column', () => {
  const source = transformSync(fs.readFileSync(path.join(root, 'department-ui/Opportunities.jsx'), 'utf8'), { loader: 'jsx', format: 'cjs' }).code;
  const module = { exports: {} };
  vm.runInNewContext(source, {
    module,
    exports: module.exports,
    require: name => name === 'react' ? React : new Proxy({}, { get: (_, key) => key }),
  });
  const {normalizeOpportunityColumns, opportunityViewStorageKey, opportunityGroupsInServerOrder, OPPORTUNITY_COLUMN_OPTIONS} = module.exports;
  assert.notEqual(opportunityViewStorageKey('workspace-a:user-1'), opportunityViewStorageKey('workspace-a:user-2'));
  assert.notEqual(opportunityViewStorageKey('workspace-a:user-1'), opportunityViewStorageKey('workspace-b:user-1'));
  assert.deepEqual(JSON.parse(JSON.stringify(normalizeOpportunityColumns(['signing', 'missing']))), ['name', 'signing']);
  assert.ok(['owner', 'amount', 'signing', 'product'].every(key => OPPORTUNITY_COLUMN_OPTIONS.some(option => option.value === key)));
  const rows = [{id: 'a', status: 'open'}, {id: 'b', status: 'open'}, {id: 'c', status: 'won'}, {id: 'd', status: 'lost'}];
  const groups = [{key:'Q4', label:'2026 Q4', items:[rows[1], rows[2], rows[3]]}, {key:'Q3', label:'2026 Q3', items:[rows[0]]}];
  const displayed = opportunityGroupsInServerOrder(groups, rows);
  assert.deepEqual(JSON.parse(JSON.stringify(displayed.flatMap(group => group.items))), rows, 'larger quarterly groups must not override global API order');
  assert.deepEqual(JSON.parse(JSON.stringify(displayed.map(group => group.label))), ['2026 Q3 · 进行中', '2026 Q4 · 进行中', '2026 Q4 · 已结束']);
  assert.deepEqual(JSON.parse(JSON.stringify(opportunityGroupsInServerOrder(groups, rows.slice(0, 2)).flatMap(group => group.items))), rows.slice(0, 2), 'appending a later page must not move the earlier page');
});
