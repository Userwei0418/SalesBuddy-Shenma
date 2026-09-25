import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const webRoot = fileURLToPath(new URL('../', import.meta.url));
const miniRoot = path.join(webRoot, 'source/miniprogram');
const previewScript = fs.readFileSync(path.join(webRoot, 'preview-api.js'), 'utf8');

function workspace(storage = new Map()) {
  const window = {SALES_MODE: 'preview', setTimeout, clearTimeout, localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)}};
  const wxStorage = new Map();
  const context = vm.createContext({window, URL, URLSearchParams, setTimeout, clearTimeout, console});
  vm.runInContext(previewScript, context, {filename: 'preview-api.js'});
  context.wx = {request: options => window.SalesPreview.request(options), uploadFile: options => window.SalesPreview.uploadFile(options),
    getStorageSync: key => wxStorage.get(key), setStorageSync: (key, value) => wxStorage.set(key, value), removeStorageSync: key => wxStorage.delete(key)};
  const cache = new Map();
  function load(filename) {
    const absolute = path.resolve(miniRoot, filename.endsWith('.js') ? filename : filename + '.js');
    if (cache.has(absolute)) return cache.get(absolute).exports;
    const module = {exports: {}}; cache.set(absolute, module);
    const wrapper = vm.runInContext('(function(require,module,exports){' + fs.readFileSync(absolute, 'utf8') + '\n})', context, {filename: absolute});
    wrapper(specifier => load(path.relative(miniRoot, path.resolve(path.dirname(absolute), specifier))), module, module.exports);
    return module.exports;
  }
  const api = load('utils/apiClient.js');
  const direct = (endpoint, options = {}) => new Promise((resolve, reject) => window.SalesPreview.request({url: 'https://example.invalid/api/v1' + endpoint, header: {Authorization: 'Bearer preview-access-sales'}, ...options, success: resolve, fail: reject}));
  return {window, api, direct, load, storage, context};
}

test('preview is explicit, isolated and never masks unknown/live endpoints', async () => {
  const w = workspace();
  const missing = await w.direct('/not-implemented');
  assert.equal(missing.statusCode, 501);
  assert.match(missing.data.message, /尚未实现/);
  w.window.SALES_MODE = 'live';
  assert.equal((await w.direct('/customers')).statusCode, 403);
  w.window.SALES_MODE = 'preview';
  assert.equal((await w.direct('/customers', {header: {Authorization: 'Bearer a-real-token'}})).statusCode, 401);
  const upload = await new Promise(resolve => w.window.SalesPreview.uploadFile({url: '/api/v1/audio/transcriptions', success: resolve}));
  assert.equal(upload.statusCode, 501);
  assert.match(JSON.parse(upload.data).message, /不支持上传/);
  let completed = 0, failed = 0, succeeded = 0;
  const task = w.window.SalesPreview.request({url: '/api/v1/customers', success() {succeeded++;}, fail() {failed++;}, complete() {completed++;}});
  task.abort(); task.abort();
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(completed, 1); assert.equal(failed, 1); assert.equal(succeeded, 0);
});

test('all five actors preserve native role/capability boundaries and labels', async () => {
  const w = workspace();
  for (const role of ['sales', 'supervisor', 'manager', 'fde', 'fde_lead']) {
    const auth = await w.api.loginWithAccount('PREVIEW_' + role.toUpperCase(), 'example', 'manager');
    assert.equal(auth.actor.role, role, 'account determines actor; role selector does not grant capabilities');
    assert.equal(auth.demo, true); assert.match(auth.source_label, /工作区/);
    const current = await w.api.getCurrentActor(); assert.equal(current.actor.user_id, auth.actor.user_id);
    const capabilities = auth.actor.capabilities;
    if (role.startsWith('fde')) {
      assert.equal(capabilities['opportunity.edit'], false);
      assert.equal(capabilities['actual.manage'], false);
      assert.equal(capabilities['visit.create'], true);
      assert.equal(capabilities['team.view'], role === 'fde_lead');
      const customers = await w.api.listCustomers();
      await assert.rejects(w.api.createOpportunity(customers.items[0].id, {name: 'blocked', amount: 100}), /未开放/);
    }
    assert.ok((await w.api.getAssistantHome()).display_policy);
  }
});

test('native pagination and detail read models consume the example workspace', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const first = await w.api.listOpportunities({includeClosed: true, pageSize: 20});
  assert.equal(first.items.length, 20); assert.equal(first.summary.total, 24); assert.equal(first.has_more, true);
  const second = await w.api.listOpportunities({includeClosed: true, pageSize: 20, offset: first.next_offset});
  assert.equal(second.items.length, 4); assert.equal(second.has_more, false);
  const all = await w.api.listAllOpportunities({includeClosed: true, pageSize: 20}); assert.equal(all.items.length, 24);
  const maps = await w.api.getCustomerMap(); assert.equal(maps.items.length, 6);
  const {mapAcv} = w.load('utils/customerMap.js'); assert.ok(mapAcv(maps.items) > 0);
  const customer = maps.items[0];
  const header = await w.api.getCustomerHeader(customer.id); assert.equal(header.read_model, 'detail_header_v1'); assert.equal(header.summary, undefined);
  const overview = await w.api.getCustomerOverview(customer.id); assert.equal(overview.summary.opportunity_count, 4); assert.equal(overview.profile.dimensions.length, 6);
  const {DetailReadSession, customerLoaders, detailWithPages} = w.load('utils/detailReadSession.js');
  const reader = new DetailReadSession(null, () => 'test'); reader.reset(customer.id);
  for (const [name, request] of Object.entries(customerLoaders(w.api, customer.id))) {await reader.load(name, request); assert.equal(reader.state(name).error, '', name);}
  const {normalizeCustomerDetail} = w.load('utils/customerDetail.js');
  const view = normalizeCustomerDetail(detailWithPages(overview, reader.pages));
  assert.equal(view.opportunityCount, 4); assert.equal(view.contactCount, 2); assert.equal(view.opportunities.length, 4);
  const op = first.items[0], focused = await w.api.getOpportunityDetailOverview(op.id);
  assert.equal(focused.id, op.customer_id); assert.equal(focused.primary_opportunity.id, op.id);
  const {loadQuarterActuals} = w.load('utils/opportunityQuarterActuals.js');
  const asOf = new Date(Date.now() + 28800000).toISOString().slice(0, 10);
  const quarters = await loadQuarterActuals(w.api, {customer_id: customer.id, opportunity_id: op.id}, asOf); assert.ok(quarters.options.length >= 4);
});

test('task order is applied to the full scope before pagination, matches the shared comparator and puts absent dates last', async () => {
  const seed = workspace(); seed.window.SalesPreview.reset();
  const key = 'sales-web:preview-workspace:v1', saved = JSON.parse(seed.storage.get(key));
  const base = saved.tasks[0], today = new Date(Date.now() + 28800000).toISOString().slice(0, 10);
  saved.tasks = Array.from({length: 27}, (_, i) => ({...base, id: `task-order-${i}`, status: 'pending_execution',
    due_at: i === 0 ? null : i === 1 ? 'invalid' : i === 2 ? today + 'T18:00:00+08:00' : new Date(Date.now() + (20 - i) * 86400000).toISOString(),
    created_at: new Date(Date.now() - i * 60000).toISOString()}));
  seed.storage.set(key, JSON.stringify(saved));
  const w = workspace(seed.storage), {sortTasks} = w.load('utils/taskOverview.js');
  for (const order of ['due_asc', 'due_desc', 'today_first', 'created_desc']) {
    const first = await w.direct(`/tasks?tab=pending&order=${order}&page_size=20&offset=0`);
    assert.equal(first.statusCode, 200); assert.equal(first.data.items.length, 20); assert.equal(first.data.summary.filtered_total, 27);
    const last = await w.direct(`/tasks?tab=pending&order=${order}&page_size=20&offset=${first.data.next_offset}`);
    assert.equal(last.data.has_more, false);
    const rows = [...first.data.items, ...last.data.items];
    assert.deepEqual(Array.from(rows, row => row.id), Array.from(sortTasks(saved.tasks, order), row => row.id));
    if (order.startsWith('due_')) assert.deepEqual(rows.slice(-2).map(row => row.id), ['task-order-0', 'task-order-1']);
  }
});

test('native opportunity form save/readback, version conflicts and idempotency are coherent', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const customer = (await w.api.listCustomers()).items[0];
  await w.api.getBusinessOptions();
  const {formFor, payload} = w.load('utils/opportunity.js');
  const form = formFor(null); Object.assign(form, {name: '【示例】手工新增验证', amount: '125', expected_close_date: '2026-12-15', stageIndex: 2, product_line: '示例产品', quarters: [{year: 2026, quarter: 4, recognized: '75', collection: '50'}]});
  const body = payload(form, null), created = await w.api.createOpportunity(customer.id, body);
  assert.equal(created.amount, 1250000); assert.equal(created.changed, true); assert.ok(created.event_id);
  const readback = await w.api.getOpportunityDetailOverview(created.id); assert.equal(readback.primary_opportunity.amount, 1250000);
  assert.equal(readback.primary_opportunity.quarterly_forecasts[0].recognized_amount, 750000);
  const nextForm = formFor(created); nextForm.amount = '130'; const edited = await w.api.createOpportunity(customer.id, payload(nextForm, created));
  assert.equal(edited.version_no, 2); assert.equal(edited.amount, 1300000);
  await assert.rejects(w.api.createOpportunity(customer.id, payload(nextForm, created)), /版本已变化/);
  const idemBody = {...body, name: '【示例】重复提交验证'};
  const opts = {method: 'POST', data: idemBody, header: {Authorization: 'Bearer preview-access-sales', 'Idempotency-Key': 'same-preview-key'}};
  const one = await w.direct('/customers/' + customer.id + '/opportunities', opts), two = await w.direct('/customers/' + customer.id + '/opportunities', opts);
  assert.equal(one.data.id, two.data.id);
  const restored = workspace(w.storage); await restored.api.loginWithAccount('PREVIEW_SALES', 'example');
  assert.equal((await restored.api.getOpportunityDetailOverview(edited.id)).primary_opportunity.amount, 1300000);
  assert.deepEqual([...w.storage.keys()], ['sales-web:preview-workspace:v1']);
});

test('task accept/complete flow verifies native owner, status and version rules', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const list = await w.api.listTaskPage({tab: 'all', page_size: 20});
  assert.equal(list.summary.total, 22); assert.equal(list.items.length, 20); assert.equal(list.has_more, true);
  assert.ok(list.items.every(t => t.title && t.description), 'original task cards require title separately from description');
  const pending = list.items.find(t => t.status === 'pending_confirm'); assert.ok(pending); assert.match(pending.id, /^[0-9a-f-]{36}$/i);
  const accepted = await w.api.respondTask(pending.id, 'accept', '', pending.version_no); assert.equal(accepted.status, 'pending_execution');
  await assert.rejects(w.api.completeTask(pending.id, '旧版本', pending.version_no), /版本已变化/);
  const completed = await w.api.completeTask(pending.id, '示例任务已完成', accepted.version_no); assert.equal(completed.status, 'pending_review');
  assert.equal((await w.api.getTask(pending.id)).completion_note, '示例任务已完成');
  const assignees = await w.api.getTaskAssignees(), owner = assignees.items.find(a => a.role === 'sales');
  const task = await w.api.createTask({description: '【示例】新建任务', assigneeAccount: owner.account_code, dueAt: new Date(Date.now() + 86400000).toISOString(), priority: '中'});
  assert.equal(task.status, 'pending_confirm'); assert.equal(task.priority_code, 'medium');
  const overview = await w.api.getTaskOverview([task.id]); assert.equal(overview.items[0].id, task.id); assert.ok(Number.isInteger(overview.metrics.all_pending));
});

test('dashboard/profile/FDE contracts distinguish actuals, forecasts and unscored examples', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const dash = await w.api.getDashboard(true); assert.equal(dash.scope, 'self'); assert.equal(dash.demo, true);
  assert.ok(dash.opportunities.every(o => o.status === 'open'));
  const forecast = dash.quarter_forecasts[0]; assert.notEqual(forecast.recognized_amount, forecast.weighted_recognized_amount);
  const {rankingDisplay} = w.load('utils/rankingDisplay.js'); const ranks = await w.api.getDashboardRankings({year: 2026, quarters: [1, 2, 3], personal: true});
  for (const key of ['opportunity_acv', 'followup', 'partner', 'region']) assert.doesNotThrow(() => rankingDisplay(ranks[key]));
  const profile = await w.api.getProfilePerformance(); assert.equal(profile.targets.recognized, null, 'missing target must not be filled with a synthetic default'); assert.ok(profile.actuals.collection >= 0);
  const growth = await w.api.getSalesGrowth(); assert.match(growth.latest.summary, /Agent 复盘/); assert.equal(growth.latest.score_summary.score, null);
  const convo = await w.api.runAgent('chatbi', '今年销售情况'); assert.match(convo.result.summary, /Agent/);
  await assert.rejects(w.api.runAgent('visit_entry', '拜访'), /Agent 暂未接入/);
  await w.api.loginWithAccount('PREVIEW_FDE', 'example');
  const fde = await w.api.getFdeDashboard({scope: 'self'}); assert.ok(fde.summary.opportunities > 0); assert.ok(fde.ranking.length);
  const fdeProfile = await w.api.getFdeProfile(); assert.equal(fdeProfile.latest.dimensions.length, 6); assert.ok(fdeProfile.latest.dimensions.every(d => d.score === null));
  const fdeOpportunities = await w.api.listFdeVisitOpportunities(); assert.ok(fdeOpportunities.items.length);
});

test('browser-discovered home, overview, manager scope and FDE fields match original templates', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const home = await w.api.getAssistantHome(), {buildVisitReceipt} = w.load('utils/visitCards.js');
  assert.equal(home.archived_visits.length, 3);
  for (const visit of home.archived_visits) {
    assert.equal(visit.status, 'archived'); assert.equal(visit.score, null);
    const card = buildVisitReceipt(visit); assert.match(card.card.subtitle, /有限公司/); assert.equal(card.card.metrics[1].value, '—'); assert.match(card.card.metrics[2].value, /暂未评分/);
  }
  const overview = await w.api.getOpportunityOverview({year: new Date().getFullYear(), quarters: []});
  for (const key of ['won', 'total', 'active', 'newCount', 'missingCloseDates', 'missingWonDates', 'missingCreatedDates']) assert.ok(Number.isInteger(overview.metrics[key]), key);
  assert.equal(overview.metrics.total, 24); assert.equal(overview.metrics.won, 3);
  const salesTask = (await w.api.listTaskPage({tab: 'all'})).items[0]; assert.equal((await w.api.getTask(salesTask.id)).can_coordinate, false);
  await w.api.loginWithAccount('PREVIEW_MANAGER', 'example');
  assert.equal((await w.api.getDashboardRankings({year: new Date().getFullYear(), quarters: [1, 2, 3], personal: false})).scope, 'company_teams');
  await w.api.loginWithAccount('PREVIEW_FDE', 'example');
  const fde = await w.api.getFdeDashboard({scope: 'self'});
  for (const key of ['period_visits', 'active_recorders', 'period_customers']) assert.ok(Number.isInteger(fde.summary[key]), key);
  const activity = await w.api.getFdeActivity({scope: 'self'}); assert.ok(activity.items.every(v => v.recorder_id === w.api.getAuth().actor.user_id));
  const candidate = (await w.api.listFdeVisitOpportunities()).items[5];
  const eligibility = await w.api.listFdeVisitOpportunities({customer_id: candidate.customer_id, opportunity_id: candidate.id, limit: 1});
  assert.equal(eligibility.items.length, 1); assert.equal(eligibility.items[0].id, candidate.id);
  assert.equal((await w.api.getFdeProfile()).sample_count, activity.total);
});

const customerInput = name => ({name, industry: '企业软件', customer_type: '潜在客户', level_code: 'Tier-2', source: '市场活动', target_team: '渠道销售-南区', partner_name: '示例伙伴', contact_name: '示例联系人', contact_title: '示例总监', contact_role: '决策者'});

test('customer create/edit preserve source form fields; manager assignment preserves one owner and first action', async () => {
  const w = workspace(); const sales = await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const created = await w.api.createCustomer(customerInput('【示例】字段读回'));
  assert.equal(created.owner_user_ref_id, sales.actor.user_id);
  let view = await w.api.getCustomerOverview(created.id);
  assert.equal(view.industry_code, '企业软件'); assert.equal(view.source_code, '市场活动'); assert.equal(view.primary_contact.title, '示例总监'); assert.equal(view.primary_contact.relationship_role_code, 'decision_maker');
  await w.api.updateCustomer(created.id, {industry: '人工智能', partner_name: '修改后的示例伙伴', contact_title: '示例技术主管'});
  view = await w.api.getCustomerOverview(created.id); assert.equal(view.industry_code, '人工智能'); assert.equal(view.primary_partner_name, '修改后的示例伙伴'); assert.equal(view.primary_contact.title, '示例技术主管');
  await w.api.loginWithAccount('PREVIEW_SUPERVISOR', 'example');
  const managed = await w.api.createCustomer(customerInput('【示例】待下发')); assert.equal(managed.owner_id, null);
  const assigned = await w.api.assignCustomer(managed.id, {assignee_account_code: 'PREVIEW_SALES', first_action: '明天联系示例客户并确认首访时间'});
  assert.equal(assigned.customer_id, managed.id); assert.match(assigned.first_action, /首访/);
  await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  assert.equal((await w.api.getCustomerMap()).items.some(c => c.id === managed.id),false,'newly assigned customer without confirmed activity is outside the active map');
  const portfolio=await w.direct('/customer-assets'); assert.ok(portfolio.data.items.some(c=>c.customer_id===managed.id),'full assets still include inactive assigned customers');
  view = await w.api.getCustomerOverview(managed.id); assert.equal(view.sales_members.length, 1); assert.equal(view.sales_members[0].id, sales.actor.user_id);
  const notices = await w.direct('/notifications'); assert.ok(notices.data.items.some(n => n.template_code === 'customer_assigned' && n.object_id === managed.id && n.payload.first_action === assigned.first_action));
});

test('claim submission stays pending, persists across reload and never grants ownership before operations approval', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const pool = await w.api.listCustomerClaimPool(); assert.equal(pool.total, 9); assert.ok(pool.items.some(c => c.claimed && !c.can_claim));
  const available = pool.items.find(c => c.can_claim); assert.ok(available);
  const before = (await w.api.getCustomerMap()).items.length, claim = await w.api.claimCustomer(available.id);
  assert.equal(claim.status, 'pending'); assert.equal((await w.api.claimCustomer(available.id)).id, claim.id);
  assert.equal((await w.api.getCustomerMap()).items.length, before);
  const restored = workspace(w.storage); await restored.api.loginWithAccount('PREVIEW_SALES', 'example');
  const item = (await restored.api.listCustomerClaimPool()).items.find(c => c.id === available.id);
  assert.equal(item.claim_status, 'pending'); assert.equal(item.can_claim, false); assert.equal(item.owner_id, null); assert.equal(item.claimed, false);
  assert.equal((await w.direct('/customers/' + available.id + '/claims/' + claim.id + '/approve', {method: 'POST', data: {}})).statusCode, 501);
});

test('task transitions survive role switches and reload, retain coordination history, and persist scoped notifications', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SUPERVISOR', 'example');
  const dueAt = new Date(Date.now() + 86400000).toISOString();
  const task = await w.api.createTask({description: '【示例】确认任务完整流程', assigneeAccount: 'PREVIEW_SALES', dueAt, priority: '高'});
  const restored = workspace(w.storage); await restored.api.loginWithAccount('PREVIEW_SALES', 'example');
  const received = await restored.api.getTask(task.id); assert.equal(received.status, 'pending_confirm'); assert.equal(received.requires_action, true);
  const accepted = await restored.api.respondTask(task.id, 'accept', '', received.version_no);
  const completed = await restored.api.completeTask(task.id, '示例交付物已确认', accepted.version_no);
  assert.equal(completed.status, 'pending_review'); assert.equal(completed.events.length, 2); assert.equal(completed.events[1].event_type, 'submit_completion');
  const managerReload = workspace(w.storage); await managerReload.api.loginWithAccount('PREVIEW_SUPERVISOR', 'example');
  assert.equal((await managerReload.api.getTask(task.id)).completion_note, '示例交付物已确认');
  await managerReload.api.respondTask(task.id, 'approve_completion', '验收确认', completed.version_no);
  const notices = await managerReload.direct('/notifications', {header: {Authorization: 'Bearer preview-access-supervisor'}});
  assert.ok(notices.data.items.some(n => n.object_id === task.id && n.template_code === 'task_completion_submitted'));
  await assert.rejects(managerReload.api.coordinateTask(task.id, {event_type: 'cancel', version_no: completed.version_no + 1, note: '不能取消已完成'}), /已结束/);
  const transfer = await managerReload.api.createTask({description: '【示例】转交任务完整流程', assigneeAccount: 'PREVIEW_SALES', dueAt});
  const reassigned = await managerReload.api.coordinateTask(transfer.id, {event_type: 'reassign', assignee_account_code: 'PREVIEW_FDE', note: '示例技术验证交接', version_no: 1});
  assert.equal(reassigned.owner_name, '周思远'); assert.equal(reassigned.assignees[0].name, reassigned.owner_name); assert.equal(reassigned.events[0].previous_owner_ids.length, 1);
  await managerReload.api.loginWithAccount('PREVIEW_FDE', 'example');
  const rejected = await managerReload.api.respondTask(transfer.id, 'reject', '示例排期冲突', reassigned.version_no);
  assert.equal(rejected.status, 'cancelled'); assert.equal(rejected.last_event_note, '示例排期冲突');
});

test('position tasks remain unowned until acceptance and cannot be claimed twice', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SUPERVISOR', 'example');
  const task = await w.api.createTask({description: '【示例】岗位一人领取', targetPosition: 'fde', dueAt: new Date(Date.now() + 86400000).toISOString()});
  assert.equal(task.assignees.length, 0); assert.equal(task.owner_name, ''); assert.equal(task.requires_action, false);
  await w.api.loginWithAccount('PREVIEW_FDE', 'example');
  assert.equal((await w.api.getTask(task.id)).requires_action, true);
  const claimed = await w.api.respondTask(task.id, 'accept', '', 1); assert.equal(claimed.assignees.length, 1); assert.equal(claimed.status, 'pending_execution');
  await assert.rejects(w.api.respondTask(task.id, 'accept', '', 1), /版本已变化/);
  await assert.rejects(w.api.respondTask(task.id, 'accept', '', claimed.version_no), /已处理/);
  assert.equal((await w.api.completeTask(task.id, '已完成岗位要求', claimed.version_no)).status, 'pending_review');
});

test('opportunity closure/reopen/no-op save keeps audit and actuals separate', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  await w.api.getBusinessOptions();
  const c = (await w.api.listCustomers()).items[0], {formFor, payload} = w.load('utils/opportunity.js');
  const form = formFor(null); Object.assign(form, {name: '【示例】关闭与重开', amount: '10', expected_close_date: '2026-12-20', stageIndex: 2, quarters: [{year: 2026, quarter: 4, recognized: '0', collection: '0'}]});
  const created = await w.api.createOpportunity(c.id, payload(form, null));
  const same = await w.api.createOpportunity(c.id, payload(formFor(created), created)); assert.equal(same.changed, false); assert.equal(same.version_no, 1);
  const close = formFor(created); close.stageIndex = 5;
  await assert.rejects(w.api.createOpportunity(c.id, payload(close, created)), /确认关闭/);
  const won = await w.api.createOpportunity(c.id, {...payload(close, created), closure_confirmed: true}); assert.equal(won.status, 'won'); assert.ok(won.won_at); assert.equal(won.actuals.recognized_amount, null);
  const reopen = formFor(won); reopen.stageIndex = 2;
  await assert.rejects(w.api.createOpportunity(c.id, payload(reopen, won)), /重新打开/);
  const opened = await w.api.createOpportunity(c.id, {...payload(reopen, won), reopen_confirmed: true}); assert.equal(opened.won_at, null); assert.equal(opened.actuals.collection_amount, null);
  const timeline = await w.direct('/opportunities/' + opened.id + '/timeline'); assert.equal(timeline.data.total, 3); assert.equal(timeline.data.items[0].type, 'reopened');
});

test('v5 migration preserves entered example records and backs up original state', async () => {
  const old = workspace(); await old.api.loginWithAccount('PREVIEW_SALES', 'example');
  const created = await old.api.createCustomer(customerInput('【示例】旧版输入保留'));
  const saved = JSON.parse(old.storage.get('sales-web:preview-workspace:v1')); saved.version = 5;
  const row = saved.customers.find(c => c.id === created.id); row.contact_title = '旧版已填写职位'; row.industry = '旧版已填写行业';
  const raw = JSON.stringify(saved); old.storage.set('sales-web:preview-workspace:v1', raw);
  const migrated = workspace(old.storage); const auth = await migrated.api.loginWithAccount('PREVIEW_SALES', 'example');
  assert.match(auth.migration_notice, /保留/); assert.equal(old.storage.get('sales-web:preview-backup:v5'), raw);
  const view = await migrated.api.getCustomerOverview(created.id); assert.equal(view.industry_code, '旧版已填写行业'); assert.equal(view.primary_contact.title, '旧版已填写职位');
});
