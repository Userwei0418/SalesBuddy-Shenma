const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const tick = () => new Promise(setImmediate);
const assets = path.resolve(__dirname, '../../backend/src/sales_backend/web/assets');
const actor = (user = 'admin', extra = {}) => ({workspace_id: 'workspace', user_id: user, role: 'administrator', ...extra});
const customerRows = (name = '已入库客户', total = 120) => ({items: [{id: name, name, ownership_state: 'claimed', ownership_version: 2}], total});
const summary = {customers: 120, unclaimed: 8, pending_claims: 2, legacy_review: 1};
const report = (calls = 123) => ({summary: {calls, succeeded: calls, failed: 0, cancelled: 0, running: 0, input_tokens: null, output_tokens: null, unknown_token_calls: calls, average_latency_ms: 45, business_operations: calls, audio_seconds: null}, trend: [], roles: [], legacy_logical_calls: 0});
const callRows = (name = '现有用户', total = 120) => ({items: [{id: name, actor_name: name, actor_role_code: 'sales', operation_code: 'competency_review', status: 'succeeded', record_kind: 'provider_attempt', latency_ms: null, input_tokens: null, output_tokens: null}], total});
const rules = (name = '现有规则') => ({items: [{id: name, name, period: 'month', enabled: true, calls_limit: 100, version_no: 3}], alerts: []});

async function harness(page = 'customers') {
  const core = await import(pathToFileURL(path.join(assets, 'core.js')).href);
  const pages = await import(pathToFileURL(path.join(assets, 'pages.js')).href);
  core.clearSession();
  Object.assign(core.state, {actor: actor(), token: 'fixture-only', page, org: {accounts: [], departments: []}});
  const requests = [];
  global.fetch = (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject, answered: false}));
  global.FormData = class { constructor(form) { this.values = form.values || []; } [Symbol.iterator]() { return this.values[Symbol.iterator](); } get(name) { return this.values.find(([key]) => key === name)?.[1] ?? null; } has(name) { return this.values.some(([key]) => key === name); } };
  const submitButton = {disabled: false}, formError = {innerHTML: ''};
  const dialogForm = {values: [], querySelector: name => name === 'button[type=submit]' ? submitButton : formError};
  const dialog = {innerHTML: '', querySelectorAll: () => [], showModal() {}, close() {}};
  global.document = {querySelector: name => ({'#dialog': dialog, '#dialog-form': dialogForm, '#toast': {style: {}}})[name] || null, dispatchEvent() {}};
  const h = {core, requests, dialog, dialogForm, generation: 0};
  h.count = endpoint => requests.filter(r => r.url.split('?')[0] === '/api/v1/console' + endpoint).length;
  h.pending = endpoint => requests.find(r => !r.answered && r.url.split('?')[0] === '/api/v1/console' + endpoint);
  h.reply = (request, data, status = 200) => { assert.ok(request, 'expected a pending request'); request.answered = true; request.resolve({ok: status < 400, status, json: async () => data}); };
  h.respond = (endpoint, data, status = 200) => h.reply(h.pending(endpoint), data, status);
  h.render = async (reason = 'refresh') => {
    const generation = ++h.generation;
    const view = await (core.state.page === 'ai' ? pages.aiUsage : pages.customers)({reason, isCurrent: () => generation === h.generation});
    const regions = Object.fromEntries([...view.html.matchAll(/data-region="([^"]+)"/g)].map(match => [match[1], {innerHTML: '', setAttribute(name, value) { this[name] = value; }}]));
    const form = {values: []};
    const root = {html: view.html, querySelector(selector) { if (selector === '#filters') return form; return regions[selector.match(/data-region="([^"]+)"/)?.[1]] || null; }};
    const mount = {view, root, regions, form};
    h.mounted = mount;
    mount.settled = view.bind(root);
    return mount;
  };
  core.state.refresh = options => h.render(options?.reason || 'refresh');
  h.region = name => h.mounted.regions[core.state.page + '-' + name].innerHTML;
  h.click = (action, id = '', mount = h.mounted) => mount.root.onclick({target: {closest: () => ({dataset: {action, id}, disabled: false})}});
  h.next = () => h.mounted.root.onclick({target: {closest: () => ({dataset: {page: 'next'}})}});
  h.finish = async (names = {}) => {
    for (const req of requests.filter(r => !r.answered)) {
      const endpoint = req.url.split('?')[0].replace('/api/v1/console', '');
      h.reply(req, names[endpoint] ?? ({'/summary': summary, '/customers': customerRows(), '/ai/overview': report(), '/ai/calls': callRows(), '/ai/rules': rules()})[endpoint]);
    }
    await tick();
  };
  return h;
}

test('customer shell and list render before a slow summary; paging shares its in-flight read', async () => {
  const h = await harness(); const first = await h.render();
  assert.match(first.root.html, /客户管理|搜索客户名称/);
  h.respond('/customers', customerRows('第一页客户')); await tick();
  assert.match(h.region('list'), /第一页客户/); assert.match(h.region('summary'), /正在加载客户概览/);
  await h.next(); assert.equal(h.count('/summary'), 1); assert.equal(h.count('/customers'), 2);
  assert.match(h.pending('/customers').url, /offset=50/);
  h.respond('/customers', customerRows('第二页客户')); h.respond('/summary', summary); await h.mounted.settled;
  assert.match(h.region('list'), /第二页客户/); assert.match(h.region('list'), /51–100/);
  assert.match(h.region('summary'), /120/); assert.doesNotMatch(first.regions['customers-summary'].innerHTML, /120/);
  await h.next(); await h.finish(); assert.equal(h.count('/summary'), 1); assert.equal(h.count('/customers'), 3);
});

test('customer summary failure is local and can be retried without reloading successful rows', async () => {
  const h = await harness(); await h.render(); h.respond('/summary', {detail: '数据库暂忙'}, 503); h.respond('/customers', customerRows()); await h.mounted.settled;
  assert.match(h.region('summary'), /客户概览加载失败：数据库暂忙/); assert.match(h.region('list'), /已入库客户/);
  const retry = h.click('retry-summary'); h.respond('/summary', summary); await retry;
  assert.match(h.region('summary'), /120/); assert.equal(h.count('/customers'), 1);
});

test('AI list appears when overview is slow and rules fail; only list is requested on page changes', async () => {
  const h = await harness('ai'); await h.render();
  h.respond('/ai/calls', callRows('第一页用户')); h.respond('/ai/rules', {detail: '规则读取失败'}, 500); await tick();
  assert.match(h.region('list'), /第一页用户/); assert.match(h.region('rules'), /规则读取失败/); assert.match(h.region('overview'), /正在加载调用概览/);
  await h.next(); h.respond('/ai/calls', callRows('第二页用户')); h.respond('/ai/overview', report()); await h.mounted.settled;
  assert.equal(h.count('/ai/rules'), 1); assert.equal(h.count('/ai/overview'), 1); assert.equal(h.count('/ai/calls'), 2);
  assert.match(h.region('list'), /第二页用户/); assert.match(h.region('list'), /51–100/);
  const retry = h.click('retry-rules'); h.respond('/ai/rules', rules('恢复规则')); await retry;
  assert.match(h.region('rules'), /恢复规则/); assert.equal(h.count('/ai/calls'), 2);
});

test('a failing AI list leaves overview and rules available, and has its own retry', async () => {
  const h = await harness('ai'); await h.render(); h.respond('/ai/calls', {detail: '读取明细失败'}, 503); h.respond('/ai/overview', report()); h.respond('/ai/rules', rules()); await h.mounted.settled;
  assert.match(h.region('list'), /读取明细失败/); assert.match(h.region('overview'), /123/); assert.match(h.region('rules'), /现有规则/);
  const retry = h.click('retry-list'); h.respond('/ai/calls', callRows('恢复用户')); await retry;
  assert.match(h.region('list'), /恢复用户/); assert.equal(h.count('/ai/overview'), 1); assert.equal(h.count('/ai/rules'), 1);
});

for (const action of ['query', 'reset', 'refresh']) test(`AI ${action} refreshes all sections and excludes an earlier filter's response`, async () => {
  const h = await harness('ai'); const old = await h.render(); const oldOverview = h.pending('/ai/overview');
  h.respond('/ai/calls', callRows('旧用户')); h.respond('/ai/rules', rules()); await tick();
  if (action === 'query') { h.mounted.form.values = [['period', 'week'], ['role', 'fde']]; h.mounted.form.onsubmit({preventDefault() {}}); await tick(); }
  else await h.click(action);
  assert.equal(h.count('/ai/overview'), 2); assert.equal(h.count('/ai/rules'), 2);
  if (action === 'query') assert.match(h.pending('/ai/calls').url, /period=week&role=fde/);
  h.reply(oldOverview, report(999)); await tick(); assert.doesNotMatch(h.region('overview'), /999/);
  await h.finish(); await old.settled; assert.match(h.region('overview'), /123/);
});

test('retrying a loading section cannot be overwritten by its earlier response', async () => {
  const h = await harness(); await h.render(); const old = h.pending('/summary');
  const retry = h.click('retry-summary'); const newer = h.requests.filter(r => r.url.endsWith('/summary')).at(-1);
  h.reply(newer, {...summary, customers: 7}); await retry; h.reply(old, {...summary, customers: 999}); h.respond('/customers', customerRows()); await h.mounted.settled;
  assert.match(h.region('summary'), /<strong>7<\/strong>/); assert.doesNotMatch(h.region('summary'), /999/);
});

for (const change of ['user', 'workspace', 'permissions', 'same-account-login']) test(`section cache is discarded for ${change} and late previous data is not painted`, async () => {
  const h = await harness('ai'); const old = await h.render(); const requests = [...h.requests];
  const replacement = change === 'user' ? actor('B') : change === 'workspace' ? actor('admin', {workspace_id: 'other'}) : change === 'permissions' ? actor('admin', {data_scope: 'self', permissions: ['ai.read'], permission_version: 2}) : actor();
  h.core.clearSession(); h.core.state.actor = replacement; h.core.state.token = 'new-fixture';
  // Even if a caller asks for pagination, a different login actor object cannot
  // reuse the previous page's overview or rules.
  await h.render('page'); assert.equal(h.count('/ai/overview'), 2); assert.equal(h.count('/ai/rules'), 2);
  requests.forEach(req => h.reply(req, {summary: report(999).summary, items: [{name: '不应显示'}], trend: [], roles: [], alerts: []}));
  await old.settled; await h.finish(); assert.doesNotMatch(h.region('list') + h.region('rules') + h.region('overview'), /999|不应显示/);
});

test('same-session permission changes during a read rebuild every section once', async () => {
  const h = await harness('ai'); const old = await h.render(); const prior = [...h.requests];
  h.core.state.actor = actor('admin', {scope: 'self', permissions: ['ai.read']});
  prior.forEach(req => h.reply(req, {items: [], total: 0, summary: report(999).summary, trend: [], roles: [], alerts: []}));
  await old.settled; await tick();
  assert.equal(h.count('/ai/overview'), 2); assert.equal(h.count('/ai/calls'), 2); assert.equal(h.count('/ai/rules'), 2);
  await h.finish(); assert.doesNotMatch(h.region('overview'), /999/);
});

test('customer ownership write refreshes list and summary instead of reusing old totals', async () => {
  const h = await harness(); await h.render(); await h.finish();
  await h.click('release', '已入库客户'); h.dialogForm.values = [['reason', '经过核实的交接']];
  const submit = h.dialogForm.onsubmit({preventDefault() {}, currentTarget: h.dialogForm});
  const write = h.requests.at(-1); assert.equal(write.options.method, 'POST'); assert.match(write.url, /\/release$/);
  h.reply(write, {released: true}); await submit;
  assert.equal(h.count('/summary'), 2); assert.equal(h.count('/customers'), 2);
  h.respond('/summary', {...summary, unclaimed: 9}); h.respond('/customers', customerRows('写后客户')); await h.mounted.settled;
  assert.match(h.region('summary'), /<strong>9<\/strong>/); assert.match(h.region('list'), /写后客户/);
});

test('saving a reminder rule invalidates cached rules and the displayed report', async () => {
  const h = await harness('ai'); await h.render(); await h.finish();
  await h.click('edit-rule', '现有规则'); h.dialogForm.values = [['name', '修改规则'], ['period', 'month'], ['calls_limit', '200'], ['enabled', 'on']];
  const submit = h.dialogForm.onsubmit({preventDefault() {}, currentTarget: h.dialogForm});
  const write = h.requests.at(-1); assert.equal(write.options.method, 'PUT'); assert.match(write.url, /\/ai\/rules\//);
  h.reply(write, {saved: true}); await submit;
  assert.equal(h.count('/ai/overview'), 2); assert.equal(h.count('/ai/calls'), 2); assert.equal(h.count('/ai/rules'), 2);
  await h.finish({'/ai/rules': rules('修改规则')}); assert.match(h.region('rules'), /修改规则/);
});
