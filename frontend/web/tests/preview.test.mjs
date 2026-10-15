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

test('preview upload declares not registered, persists actor-scoped receipt and never invents extraction', async () => {
  const w = workspace(), key = 'c4754ea5-7b49-4f65-811a-d051f7a35139';
  await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const uploaded = await w.api.uploadVisitFile('fixture-only.txt', '保留原件.txt', null, key);
  assert.equal(uploaded.upload_intent_id, key); assert.equal(uploaded.state, 'retryable_failure');
  assert.equal(uploaded.id, null); assert.equal(uploaded.status, null); assert.equal(uploaded.retryable, true);
  assert.equal(uploaded.extracted_text, undefined); assert.match(uploaded.message, /未接收文件/);
  const restored = workspace(w.storage); await restored.api.loginWithAccount('PREVIEW_SALES', 'example');
  assert.equal((await restored.api.getVisitUploadIntent(key)).filename, '保留原件.txt');
  await restored.api.loginWithAccount('PREVIEW_FDE', 'example');
  await assert.rejects(restored.api.getVisitUploadIntent(key), error => error.statusCode === 404 && error.serverCode === 'UPLOAD_INTENT_NOT_FOUND');
  w.window.SALES_MODE = 'live';
  await assert.rejects(w.api.uploadVisitFile('fixture-only.txt', '保留原件.txt', null, key), error => error.statusCode === 403);
});

test('workspace preview catalog uses fixed Backend e28aef1 field definitions and tax labels', async () => {
  const fixed = JSON.parse(fs.readFileSync(path.join(webRoot, 'tests/fixtures/opportunity-workspace-e28aef1.json'), 'utf8'));
  const w = workspace();
  const options = (await w.direct('/opportunity-views/options')).data;
  assert.deepEqual(JSON.parse(JSON.stringify(options.fields.map(field => ({...field, editable:false})))), fixed.fields);
  assert.equal(options.fields.find(field => field.key === 'forecast_recognized').label, '期间预测含税确收（元）');
  assert.equal(options.fields.find(field => field.key === 'actual_recognized').label, '期间实际不含税确收（元）');
  assert.equal(options.fields.find(field => field.key === 'actual_recognized').editable, false);
});

test('native pagination and detail read models consume the example workspace', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const first = await w.api.listOpportunities({includeClosed: true, pageSize: 20, scope: 'self'});
  assert.equal(first.items.length, 20); assert.equal(first.summary.total, 24); assert.equal(first.has_more, true);
  const second = await w.api.listOpportunities({includeClosed: true, pageSize: 20, scope: 'self', offset: first.next_offset});
  assert.equal(second.items.length, 4); assert.equal(second.has_more, false);
  const all = await w.api.listAllOpportunities({includeClosed: true, pageSize: 20, scope: 'self'}); assert.equal(all.items.length, 24);
  const maps = await w.api.getCustomerMap({scope: 'self'}); assert.equal(maps.items.length, 6);
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

test('opportunity preview sorts open records before pagination and counts formal activity in the chosen statistics period', async () => {
  const seed = workspace(); seed.window.SalesPreview.reset();
  const key = 'sales-web:preview-workspace:v1', saved = JSON.parse(seed.storage.get(key)), base = saved.opportunities[0];
  saved.opportunities = Array.from({length: 45}, (_, i) => ({...base, id:`opp-order-${String(i).padStart(2,'0')}`,
    status:i < 24 ? 'open' : i < 36 ? 'won' : 'lost', probability:i < 24 ? 30 : i < 36 ? 100 : 0,
    expected_close_date:i < 24 ? '2027-04-10' : '2025-01-10', created_at:'2025-01-01T00:00:00+08:00',
    won_at:i < 36 && i >= 24 ? '2025-06-01T00:00:00+08:00' : null}));
  saved.visits = [
    {id:'formal-this-period', status:'confirmed', opportunity_id:saved.opportunities[0].id, interaction_at:'2026-04-01T00:00:00+08:00'},
    {id:'duplicate-day', status:'confirmed', opportunity_id:saved.opportunities[0].id, interaction_at:'2026-04-02T00:00:00+08:00'},
    {id:'formal-old', status:'archived', opportunity_id:saved.opportunities[1].id, interaction_at:'2025-04-01T00:00:00+08:00'},
    {id:'draft', status:'draft', opportunity_id:saved.opportunities[2].id, interaction_at:'2026-04-01T00:00:00+08:00'},
    {id:'lost', status:'confirmed', opportunity_id:saved.opportunities[40].id, interaction_at:'2026-04-01T00:00:00+08:00'},
  ];
  seed.storage.set(key, JSON.stringify(saved));
  const w = workspace(seed.storage);
  const query = '/opportunities?include_closed=true&order=quarter_stage&page_size=20&include_overview=true&overview_year=2026';
  const first = await w.direct(query + '&overview_quarters=2'), second = await w.direct(query + '&overview_quarters=2&offset=20'), third = await w.direct(query + '&overview_quarters=2&offset=40');
  assert.equal(first.statusCode, 200);
  assert.deepEqual([first, second, third].map(response => response.data.items.length), [20,20,5]);
  assert.deepEqual([first, second, third].map(response => response.data.items.filter(row => row.status === 'open').length), [20,4,0]);
  assert.equal(new Set([first,second,third].flatMap(response => response.data.items.map(row => row.id))).size,45);
  assert.equal(first.data.overview.metrics.total,45);
  assert.equal(first.data.overview.metrics.active,1);
  assert.equal(first.data.overview.metrics.won,0);
  assert.equal(first.data.overview.metrics.newCount,0);
  const all = await w.direct(query);
  assert.equal(all.data.overview.metrics.active,2);
  assert.equal(all.data.overview.metrics.won,12);
  assert.equal(all.data.overview.metrics.newCount,45);
});

test('native opportunity form save/readback, version conflicts and idempotency are coherent', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const customer = (await w.api.listCustomers()).items[0];
  await w.api.getBusinessOptions();
  const {formFor, payload} = w.load('utils/opportunity.js');
  const form = formFor(null); Object.assign(form, {name: '【示例】手工新增验证', amount: '125', expected_close_date: '2026-12-15', stageIndex: 2, product_line: '示例产品', quarters: [{year: 2026, quarter: 4, recognized: '75', collection: '50', collection_confidence: 'high'}]});
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
  const history = await w.direct('/opportunities/creation-history?q=' + encodeURIComponent(edited.name));
  assert.equal(history.statusCode, 200); assert.equal(history.data.total, 1);
  assert.equal(history.data.items[0].creator_id, (await w.api.getCurrentActor()).actor.user_id);
  assert.equal(history.data.items[0].created_at, created.created_at);
  const creatorOptions = await w.direct('/opportunities/creation-history/options');
  assert.ok(creatorOptions.data.members.some(person => person.id === history.data.items[0].creator_id));
  const restored = workspace(w.storage); await restored.api.loginWithAccount('PREVIEW_SALES', 'example');
  assert.equal((await restored.api.getOpportunityDetailOverview(edited.id)).primary_opportunity.amount, 1300000);
  assert.deepEqual([...w.storage.keys()], ['sales-web:preview-workspace:v1']);
});

test('task accept/complete flow verifies native owner, status and version rules', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const list = await w.api.listTaskPage({tab: 'all', page_size: 20, view: 'self'});
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
  assert.equal(dash.contract_version, 4); assert.equal(dash.forecast_basis, 'latest_sales_input');
  const forecast = dash.quarter_forecasts[0];
  assert.equal(Object.hasOwn(forecast, 'weighted_recognized_amount'), false);
  const original = (await w.api.getOpportunityDetail(forecast.opportunity_id)).primary_opportunity.quarterly_forecasts.find(q => q.year === forecast.year && q.quarter === forecast.quarter);
  assert.equal(forecast.recognized_amount, original.recognized_amount);
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
  assert.equal(overview.metrics.total, 32); assert.equal(overview.metrics.won, 4);
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

test('synthetic identity directory preserves own/external links and excludes pure partners from the portfolio', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const input = {...customerInput('合成纯伙伴企业'), business_identities: ['partner'], customer_type: null, partner_id: '00000014-0000-4000-8000-000000000001'};
  delete input.partner_name;
  const row = await w.api.createCustomer(input);
  assert.ok(row.partner_profile_id); assert.notEqual(row.partner_profile_id, row.primary_partner_id);
  assert.equal(row.customer_type_code, null);
  const directory = await w.api.request({path: '/customers/claim-pool?business_identity=partner'});
  assert.equal(directory.total, 1); assert.equal(directory.items[0].can_read_customer, true);
  assert.equal(directory.items[0].id, row.id);
  const portfolio = await w.api.request({path: '/customers/portfolio'});
  assert.equal(portfolio.items.some(item => item.id === row.id), false);
  const changed = await w.api.updateCustomer(row.id, {business_identities: ['customer','partner'], customer_type: '潜在客户', partner_id: null});
  assert.equal(changed.partner_profile_id, row.partner_profile_id); assert.equal(changed.primary_partner_id, null);
});

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
  assert.equal((await w.api.getCustomerMap()).items.some(c => c.id === managed.id),false,'assigned assets without formal follow-up are not active map customers');
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
  await assert.rejects(managerReload.api.coordinateTask(task.id, {event_type: 'cancel', version_no: completed.version_no + 1, note: '不能取消已完成'}), /不允许取消/);
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
  const form = formFor(null); Object.assign(form, {name: '【示例】关闭与重开', amount: '10', expected_close_date: '2026-12-20', stageIndex: 2, quarters: [{year: 2026, quarter: 4, recognized: '0', collection: '0', collection_confidence: 'high'}]});
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

test('sales forecasts replace current values and remain visible after winning without changing actuals', async () => {
  const w = workspace(); await w.api.loginWithAccount('PREVIEW_SALES', 'example'); await w.api.getBusinessOptions();
  const customer = (await w.api.listCustomers()).items[0], {formFor, payload} = w.load('utils/opportunity.js');
  const form = formFor(null);
  Object.assign(form, {name: '【合成测试】最新季度预测', amount: '100', expected_close_date: '2026-12-20', stageIndex: 1});
  let current = null;
  for (const amount of ['100', '70', '80', '40']) {
    form.quarters = [{year: 2026, quarter: 4, recognized: '0', collection: amount, collection_confidence: 'high'}];
    current = await w.api.createOpportunity(customer.id, payload(form, current));
    const dash = await w.api.getDashboard(true), row = dash.quarter_forecasts.find(row => row.opportunity_id === current.id);
    assert.equal(Number(row.collection_amount), Number(amount) * 10000);
    assert.equal(Number(row.recognized_amount), 0);
    assert.equal(current.actuals.collection_amount, null);
  }
  const won = formFor(current); won.stageIndex = 5;
  current = await w.api.createOpportunity(customer.id, {...payload(won, current), closure_confirmed: true});
  const dash = await w.api.getDashboard(true);
  assert.equal(dash.quarter_forecasts.find(row => row.opportunity_id === current.id).collection_amount, 400000);
  const extra = await w.api.createOpportunity(customer.id, {...payload(formFor(current), current), quarterly_forecasts: [{year: 2027, quarter: 1, recognized_amount: 0, collection_amount: 100, collection_confidence: 'high'}]});
  assert.equal(extra.quarterly_forecasts.find(row => row.year === 2026 && row.quarter === 4).collection_amount, 400000);
  assert.equal(extra.quarterly_forecasts.find(row => row.year === 2027 && row.quarter === 1).collection_amount, 100);
});

test('every preview sales and FDE identity is read-only for actual money', async () => {
  const w = workspace();
  for (const role of ['sales', 'supervisor', 'manager', 'fde', 'fde_lead']) {
    const {actor} = await w.api.loginWithAccount('PREVIEW_' + role.toUpperCase(), 'example');
    assert.equal(actor.capabilities['actual.manage'], false);
    assert.equal(actor.permissions['actual.create'], false);
    assert.equal(actor.permissions['actual.void'], false);
    const header = {Authorization: 'Bearer preview-access-' + role};
    for (const path of ['/customer-assets', '/customer-assets/synthetic-record/void']) {
      const result = await w.direct(path, {header, method: 'POST', data: {amount: '50', confirmed: true}});
      assert.equal(result.statusCode, 403); assert.match(result.data.detail, /运营/);
    }
  }
});

test('preview material edits retain same-SKU identities, catalog snapshots and independent actuals', async () => {
  const seed = workspace(); seed.window.SalesPreview.reset(); await seed.api.loginWithAccount('PREVIEW_SALES', 'example');
  assert.equal((await seed.direct('/materials')).data.total, 0, 'no invented official catalog');
  const key = 'sales-web:preview-workspace:v1', saved = JSON.parse(seed.storage.get(key));
  saved.materials = [{id: 'synthetic-sku', code: 'TEST_ONLY', name: '合成物料，仅用于测试', catalog_price: '100.00', unit: '份', recognition_rule: 'manual', version_no: 1}];
  seed.storage.set(key, JSON.stringify(saved));
  const w = workspace(seed.storage); await w.api.loginWithAccount('PREVIEW_SALES', 'example'); await w.api.getBusinessOptions();
  const customer = (await w.api.listCustomers()).items[0], formUtil = w.load('utils/opportunity.js'), form = formUtil.formFor(null);
  Object.assign(form, {name: '【合成测试】物料多行', amount: '10', expected_close_date: '2026-12-20', stageIndex: 1, quarters: [{year: 2026, quarter: 4, recognized: '0', collection: '0', collection_confidence: 'high'}]});
  const body = formUtil.payload(form, null), input = {material_id: 'synthetic-sku', quantity: '1.2500', unit_price: '0.02', usage: 'self_use'};
  let op = await w.api.createOpportunity(customer.id, {...body, trade_relation: 'direct', material_lines: [input, input]});
  assert.equal(op.material_lines.length, 2); assert.notEqual(op.material_lines[0].id, op.material_lines[1].id);
  assert.equal(op.material_lines[0].line_amount, '0.03');
  const originalIds = op.material_lines.map(row => row.id), originalSnapshot = JSON.stringify(op.material_lines[0].catalog_snapshot);
  op = await w.api.createOpportunity(customer.id, {...body, opportunity_id: op.id, version_no: op.version_no, material_lines: null});
  assert.deepEqual(op.material_lines.map(row => row.id), originalIds);
  const materialRows = (await w.direct(`/opportunities/${op.id}/materials`)).data.items;
  assert.equal(materialRows.length, 2);
  const money = await w.direct(`/opportunities/${op.id}/financial-overview?year=2026&quarter=4`);
  assert.equal(money.data.schema_version, 2); assert.equal(money.data.can_confirm, false);
  assert.equal(money.data.periods.length, 2); assert.ok(money.data.periods.every(p => p.amount === null && p.status === 'missing' && p.occurred_on === null));
  const reference = await w.direct(`/opportunities/${op.id}/material-forecast-preview`, {method: 'POST', data: {year: 2026, quarter: 4}});
  assert.equal(reference.data.status, 'incomplete'); assert.equal(reference.data.recognized_inclusive, null);
  const remaining = {...input, id: op.material_lines[1].id, unit_price: '70.00'};
  op = await w.api.createOpportunity(customer.id, {...body, opportunity_id: op.id, version_no: op.version_no, material_lines: [remaining]});
  assert.equal(op.material_lines[0].id, originalIds[1]); assert.equal(JSON.stringify(op.material_lines[0].catalog_snapshot), originalSnapshot);
  assert.equal(op.retired_material_lines[0].id, originalIds[0]); assert.equal(op.actuals.recognized_amount, null);
  op = await w.api.createOpportunity(customer.id, {...body, opportunity_id: op.id, version_no: op.version_no, material_lines: []});
  assert.equal(op.material_lines.length, 0); assert.equal(op.retired_material_lines.length, 2);
  assert.equal(op.actuals.collection_amount, null);
});

test('preview readers share a current actual projection without adding raw history or inventing dates', async () => {
  const seed = workspace(); seed.window.SalesPreview.reset(); const {actor} = await seed.api.loginWithAccount('PREVIEW_SALES', 'example');
  const fde = (await seed.api.loginWithAccount('PREVIEW_FDE', 'example')).actor;
  const key = 'sales-web:preview-workspace:v1', saved = JSON.parse(seed.storage.get(key));
  const op = saved.opportunities.find(op => op.owner_id === actor.user_id && op.fde_member_ids.includes(fde.user_id));
  assert.ok(op);
  const now = new Date(Date.now() + 28800000), year = now.getUTCFullYear(), quarter = Math.ceil((now.getUTCMonth() + 1) / 3);
  const base = {customer_id: op.customer_id, opportunity_id: op.id, owner_id: op.owner_id, year, quarter, occurred_on: null, source: 'operations', period_precision: 'quarter', status: 'confirmed', confirmed: true};
  saved.effective_actuals = [
    {...base, id: 'material-first', kind: 'recognized', amount: '200000', complete: true, missing_material_count: 0},
    {...base, id: 'material-second', kind: 'recognized', amount: '300000', complete: true},
    {...base, id: 'opportunity-collection', kind: 'collection', amount: '1000000', complete: true},
  ];
  seed.storage.set(key, JSON.stringify(saved));
  const w = workspace(seed.storage); await w.api.loginWithAccount('PREVIEW_SALES', 'example');
  const assets = await w.api.getCustomerAssets({customer_id: op.customer_id, period: 'year'});
  assert.equal(assets.summary.recognized_amount, 500000); assert.equal(assets.summary.collection_amount, 1000000);
  assert.ok(assets.items.every(row => row.occurred_on === null && row.period_precision === 'quarter' && row.can_void === false));
  const dash = await w.api.getDashboard(true), actual = dash.quarter_actuals.find(row => row.year === year && row.quarter === quarter);
  assert.equal(actual.recognized_amount, 500000); assert.equal(actual.collection_amount, 1000000);
  assert.equal(actual.complete, true); assert.equal(actual.recognized_missing_material_count, 0);
  const detail = await w.api.getOpportunityDetail(op.id); assert.equal(detail.primary_opportunity.actuals.collection_amount, 1000000);
  const profile = await w.api.getProfilePerformance({year, quarter}); assert.equal(profile.actuals.collection, 1000000);
  await w.api.loginWithAccount('PREVIEW_FDE', 'example');
  const dashboard = await w.api.getFdeDashboard({scope: 'self', year, quarters: [quarter]}); assert.equal(dashboard.summary.recognized_amount, 500000);
  saved.effective_actuals = [{...base, id: 'zero', kind: 'collection', amount: '0'}];
  seed.storage.set(key, JSON.stringify(saved));
  const zero = workspace(seed.storage); await zero.api.loginWithAccount('PREVIEW_SALES', 'example');
  const totals = (await zero.api.getCustomerAssets({customer_id: op.customer_id})).summary;
  assert.equal(totals.collection_amount, 0); assert.equal(totals.recognized_amount, null);
  saved.effective_actuals = [{...base, id: 'partial-zero', kind: 'recognized', amount: null, confirmed_subtotal: '0', status: 'partial', complete: false, missing_material_count: 1}];
  seed.storage.set(key, JSON.stringify(saved));
  const partial = workspace(seed.storage); await partial.api.loginWithAccount('PREVIEW_SALES', 'example');
  const partialTotals = (await partial.api.getCustomerAssets({customer_id: op.customer_id})).summary;
  assert.equal(partialTotals.recognized_amount, null); assert.equal(partialTotals.recognized_confirmed_subtotal, 0);
  assert.equal(partialTotals.complete, false); assert.equal(partialTotals.recognized_missing_material_count, 1);
  assert.equal((await partial.api.getOpportunityDetail(op.id)).primary_opportunity.actuals.recognized_amount, null);
  const period = (await partial.direct(`/opportunities/${op.id}/financial-overview?year=${year}&quarter=${quarter}`)).data.periods.find(row => row.kind === 'recognized');
  assert.equal(period.amount, null); assert.equal(period.confirmed_subtotal, 0); assert.equal(period.status, 'partial');
  assert.equal(period.missing_material_count, 1);
  saved.effective_actuals.push({...base, id: 'complete-other-period', quarter: quarter === 1 ? 2 : 1, kind: 'recognized', amount: '500000', complete: true});
  seed.storage.set(key, JSON.stringify(saved));
  const mixed = workspace(seed.storage); await mixed.api.loginWithAccount('PREVIEW_SALES', 'example');
  const mixedTotals = (await mixed.api.getCustomerAssets({customer_id: op.customer_id})).summary;
  assert.equal(mixedTotals.recognized_amount, null); assert.equal(mixedTotals.recognized_confirmed_subtotal, 500000);
  assert.equal(mixedTotals.recognized_incomplete_period_count, 1); assert.equal(mixedTotals.recognized_complete, false);
  saved.effective_actuals = []; seed.storage.set(key, JSON.stringify(saved));
  const empty = workspace(seed.storage); await empty.api.loginWithAccount('PREVIEW_SALES', 'example');
  const absent = (await empty.api.getCustomerAssets({customer_id: op.customer_id})).summary;
  assert.equal(absent.collection_amount, null); assert.equal(absent.recognized_amount, null);
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

test('workspace preview saves config independently from data and restores only the actor default', async () => {
  const storage = new Map(), w = workspace(storage);
  const opts = await w.direct('/opportunity-views/options');
  assert.equal(opts.statusCode, 200); assert.equal(opts.data.definition_version, 'opportunity_workspace_v2');
  const config = {...opts.data.default_config, columns: [{key: 'name', width: 240}, {key: 'summary', width: 180}], group_by: 'stage'};
  const write = (endpoint, method, data, key, role='sales') => w.direct(endpoint, {method, data, header: {Authorization: `Bearer preview-access-${role}`, 'Idempotency-Key': key}});
  const created = await write('/opportunity-views', 'POST', {name: '演示周会', visibility: 'private', config}, 'create-view');
  assert.equal(created.statusCode, 200);
  const id = created.data.id;
  assert.equal((await write('/opportunity-views/preference', 'PUT', {default_view_id: id, version_no: 0}, 'set-default')).statusCode, 200);
  const restored = workspace(storage);
  assert.equal((await restored.direct('/opportunity-views/preference')).data.effective_view_id, id);
  const list = await restored.direct('/opportunity-views');
  assert.deepEqual(JSON.parse(JSON.stringify(list.data.items[0].config.columns)), JSON.parse(JSON.stringify(config.columns)));
  assert.equal((await w.direct('/opportunity-views', {header: {Authorization: 'Bearer preview-access-fde'}})).data.total, 0);
  const query = await w.direct('/opportunity-views/query', {method: 'POST', data: {config, offset: 0, limit: 2}});
  assert.equal(query.statusCode, 200); assert.ok(query.data.total > 2); assert.equal(query.data.items.length, 2); assert.ok(query.data.groups.every(group => !['qualified','identified'].includes(group.label)));
  assert.equal((await write('/opportunity-views/preference', 'PUT', {default_view_id: null, version_no: 0}, 'stale-default')).statusCode, 409);
  assert.equal((await write(`/opportunity-views/${id}`, 'DELETE', {version_no: 1}, 'delete-view')).statusCode, 200);
  const unavailable = (await w.direct('/opportunity-views/preference')).data;
  assert.equal(unavailable.default_view_id, id); assert.equal(unavailable.effective_view_id, null); assert.equal(unavailable.unavailable_reason, 'unavailable');
});

test('advice latest preview reads the newest matching receipt without creating and isolates actors', async () => {
  const w=workspace();
  await w.api.loginWithAccount('PREVIEW_SALES','example');
  const id='00000011-0000-4000-8000-000000000001';
  assert.equal(await w.api.getLatestBusinessAdvice('opportunity',id,'overview'),null);
  const first=await w.api.requestBusinessAdvice('opportunity',id,'overview');
  const second=await w.api.requestBusinessAdvice('opportunity',id,'overview',true);
  const latest=await w.api.getLatestBusinessAdvice('opportunity',id,'overview');
  assert.notEqual(first.id,second.id);assert.equal(latest.id,second.id);
  assert.equal(latest.opportunity_id,id);assert.equal(latest.customer_id,'00000010-0000-4000-8000-000000000001');
  assert.equal(await w.api.getLatestBusinessAdvice('opportunity',id,'tasks'),null);
  await w.api.loginWithAccount('PREVIEW_SUPERVISOR','example');
  assert.equal(await w.api.getLatestBusinessAdvice('opportunity',id,'overview'),null);
});
