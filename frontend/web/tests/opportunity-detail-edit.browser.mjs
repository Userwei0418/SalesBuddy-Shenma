/** Shared edit form and Web entry in a synthetic workspace; all business network is blocked. */
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const customer = '00000010-0000-4000-8000-000000000001', opportunity = '00000011-0000-4000-8000-000000000001';
const entry = '[data-handler=webEditOpportunity]';
let server, browser, base;
before(async () => {server = createSalesWebServer({target: ''}); await new Promise(r => server.listen(0, '127.0.0.1', r)); base = `http://127.0.0.1:${server.address().port}`; browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close(); await new Promise(r => server?.close(r));});
const paint = page => page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
async function fixture(run, config = {}) {
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'}), errors = [], blocked = [];
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), config.role || 'sales');
  await context.route('**/*', route => {const url = new URL(route.request().url()); if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();} return route.continue();});
  const page = await context.newPage(); page.setDefaultTimeout(9000); page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview'); await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(config => {
      const preview = SalesPreview;
      window.editProbe = {requests: [], receipts: [], toasts: [], failure: '', headerFailure: false, ownerOverride: config.otherOwner ? 'synthetic-other-sales' : null, capabilities: config.noEdit ? {'opportunity.edit': false} : {}};
      Object.assign(SalesRuntime.app.globalData.session.capabilities, editProbe.capabilities); SalesRuntime.app._capabilityCheckedAt = Date.now();
      window.addEventListener('sales:toast', event => editProbe.toasts.push(event.detail.message));
      window.SalesPreview = {...preview, request(options = {}) {
        const path = new URL(options.url, location.href).pathname, method = (options.method || 'GET').toUpperCase();
        const write = method === 'POST' && /\/customers\/[^/]+\/opportunities$/.test(path);
        editProbe.requests.push({path, method, ...(write ? {data: structuredClone(options.data)} : {})});
        if (write && editProbe.failure === 'conflict' || editProbe.headerFailure && /\/opportunities\/[^/]+\/header$/.test(path)) {
          const result = {statusCode: write ? 409 : 403, data: {detail: write ? '合成版本冲突，请刷新后重试' : '合成对象已无权查看'}};
          queueMicrotask(() => {options.success?.(result); options.complete?.(result);}); return {abort() {}};
        }
        return preview.request({...options, success(response) {
          const next = structuredClone(response);
          if (/\/auth\/me$/.test(path) && next.statusCode === 200) Object.assign((next.data.actor || next.data).capabilities, editProbe.capabilities);
          if (/\/opportunities\/[^/]+\/(header|overview)$/.test(path) && next.statusCode === 200 && editProbe.ownerOverride) {
            for (const row of [...(next.data.opportunities || []), next.data.primary_opportunity].filter(Boolean)) row.owner_id = editProbe.ownerOverride;
          }
          if (write) {
            if (editProbe.failure === 'missing-receipt') delete next.data.event_id;
            editProbe.receipts.push(structuredClone(next));
          }
          options.success?.(next);
        }});
      }};
    }, config);
    await run(page); assert.deepEqual(errors, []); assert.deepEqual(blocked, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function openDetail(page, loaded = true) {
  await page.evaluate(({customer, opportunity}) => SalesRuntime.wx.navigateTo({url: `/pages/customer-assets/index?customer_id=${customer}&opportunity_id=${opportunity}&period=all&readonly=1`}), {customer, opportunity});
  if (loaded) await page.waitForFunction(() => SalesRuntime.current.route === 'pages/customer-assets/index' && !!SalesRuntime.current.data.opportunity && !SalesRuntime.current.data.opportunityLoading && SalesRuntime.current.data.detailSummary?.loaded);
  else await page.waitForFunction(() => !!SalesRuntime.current.data.opportunityError && !SalesRuntime.current.data.opportunityLoading);
  await paint(page);
}
async function edit(page) {await page.locator(entry).click(); await page.locator('#opportunityForm input[data-key=name]').waitFor(); await page.waitForFunction(() => !!SalesRuntime.current.data.existing && !SalesRuntime.current.data.loading);}
async function dirty(page) {await page.evaluate(() => {const p = SalesRuntime.current; p.fdeMembersChanged({detail: {members: p.data.fdeMembers.slice(1)}});}); await paint(page);}
async function revoke(page) {await page.evaluate(() => {editProbe.capabilities['opportunity.edit'] = false; SalesRuntime.app.globalData.session.capabilities['opportunity.edit'] = false; SalesRuntime.app._capabilityCheckedAt = Date.now(); SalesRuntime.current.setData({canEditOpportunity: false});}); await paint(page);}
const writes = page => page.evaluate(() => editProbe.requests.filter(r => r.method === 'POST' && /\/customers\/[^/]+\/opportunities$/.test(r.path)));
const savedRow = page => page.evaluate(id => JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).opportunities.find(row => row.id === id), opportunity);

test('owner edits via the existing form, keeps raw values/version and reads back on return without stale pending markers', () => fixture(async page => {
  await openDetail(page); assert.equal(await page.locator(entry).count(), 1);
  const before = await savedRow(page), headersBefore = await page.evaluate(() => editProbe.requests.filter(r => /\/header$/.test(r.path)).length);
  await edit(page); const existing = await page.evaluate(() => SalesRuntime.current.data.existing);
  assert.equal(existing.id, opportunity); assert.equal(existing.amount, before.amount); assert.equal(existing.version_no, before.version_no);
  await page.locator('#opportunityForm input[data-key=name]').fill('合成详情编辑后商机');
  await page.locator('.footer .submit').click();
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/customer-assets/index' && SalesRuntime.current.data.opportunity?.name === '合成详情编辑后商机' && !SalesRuntime.current.data.opportunityLoading && SalesRuntime.current.data.detailSummary?.loaded);
  const sent = await writes(page); assert.equal(sent.length, 1); assert.equal(sent[0].data.action, 'update'); assert.equal(sent[0].data.opportunity_id, opportunity); assert.equal(sent[0].data.version_no, before.version_no); assert.equal(sent[0].data.amount, before.amount);
  const after = await savedRow(page); assert.equal(after.version_no, before.version_no + 1); assert.equal(after.name, '合成详情编辑后商机');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.opportunity.version_no), after.version_no);
  assert.ok(await page.evaluate(() => editProbe.requests.filter(r => /\/header$/.test(r.path)).length) > headersBefore);
  assert.deepEqual(await page.evaluate(() => [SalesRuntime.wx.getStorageSync('pendingOpenCustomerId'), SalesRuntime.wx.getStorageSync('pendingOpenOpportunityId')]), ['', '']);
  assert.equal(await page.locator(entry).count(), 1);
}));

for (const scenario of [
  {label: 'another sales owner', otherOwner: true, allowed: false},
  {label: 'sales capability missing', noEdit: true, allowed: false},
  {label: 'manager explicitly granted edit', role: 'manager', allowed: true},
  {label: 'FDE lead with roster rights only', role: 'fde_lead', allowed: false},
]) test('detail entry respects ' + scenario.label, () => fixture(async page => {
  await openDetail(page); assert.equal(await page.locator(entry).count(), scenario.allowed ? 1 : 0);
  if (scenario.role === 'fde_lead') assert.deepEqual(await page.evaluate(() => [SalesRuntime.current.data.canManageFdeRelation, SalesRuntime.app.can('fde.members.manage'), SalesRuntime.app.can('opportunity.edit')]), [true, true, false]);
  await page.evaluate(() => SalesRuntime.current.webEditOpportunity()); await paint(page);
  if (scenario.allowed) await page.locator('#opportunityForm input[data-key=name]').waitFor();
  else assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-assets/index');
  assert.deepEqual(await writes(page), []);
}, scenario));

test('failed object read has no edit entry and direct calls do not navigate', () => fixture(async page => {
  await page.evaluate(() => {editProbe.headerFailure = true;}); await openDetail(page, false);
  assert.equal(await page.locator(entry).count(), 0); await page.evaluate(() => SalesRuntime.current.webEditOpportunity()); await paint(page);
  assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-assets/index'); assert.deepEqual(await writes(page), []);
}));

test('unsaved roster can cancel or confirm edit; in-flight roster save disables editing', () => fixture(async page => {
  await openDetail(page); await dirty(page);
  await page.locator(entry).click(); await page.getByText('协助名单尚未保存', {exact: true}).waitFor();
  await page.locator('.wx-modal-mask').getByRole('button', {name: '取消', exact: true}).click();
  assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-assets/index'); assert.equal(await page.evaluate(() => SalesRuntime.current.data.fdeRelationDirty), true);
  await page.evaluate(() => SalesRuntime.current.setData({fdeRelationSaving: true})); await paint(page); assert.equal(await page.locator(entry).isDisabled(), true);
  await page.evaluate(() => SalesRuntime.current.webEditOpportunity()); await paint(page); assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-assets/index');
  await page.evaluate(() => SalesRuntime.current.setData({fdeRelationSaving: false})); await paint(page); await page.locator(entry).click();
  await page.locator('.wx-modal-mask').getByRole('button', {name: '继续编辑', exact: true}).click(); await page.locator('#opportunityForm input[data-key=name]').waitFor();
  assert.equal(await page.evaluate(() => editProbe.requests.filter(r => r.method === 'PUT').length), 0); assert.deepEqual(await writes(page), []);
}));

test('capability revocation blocks both a direct call and confirmation of an already open dirty-roster dialog', () => fixture(async page => {
  await openDetail(page); await dirty(page); await page.locator(entry).click(); await page.getByText('协助名单尚未保存', {exact: true}).waitFor();
  await revoke(page); await page.locator('.wx-modal-mask').getByRole('button', {name: '继续编辑', exact: true}).click(); await paint(page);
  assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-assets/index');
  await page.evaluate(() => SalesRuntime.current.webEditOpportunity()); await paint(page);
  assert.equal(await page.locator(entry).count(), 0); assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-assets/index'); assert.deepEqual(await writes(page), []);
}));

for (const change of ['identity', 'object', 'page']) test('delayed edit confirmation is discarded after ' + change + ' changes', () => fixture(async page => {
  await openDetail(page); await dirty(page); await page.locator(entry).click(); await page.getByText('协助名单尚未保存', {exact: true}).waitFor();
  if (change === 'identity') await page.evaluate(() => {SalesRuntime.app.globalData.session.permissionVersion = 'synthetic-new-permissions';});
  if (change === 'object') await page.evaluate(() => SalesRuntime.current.setData({opportunityId: 'synthetic-other-opportunity'}));
  if (change === 'page') await page.evaluate(() => SalesRuntime.wx.navigateTo({url: '/pages/index/index'}));
  if (await page.locator('.wx-modal-mask').count()) await page.locator('.wx-modal-mask').getByRole('button', {name: '继续编辑', exact: true}).click();
  await paint(page); assert.notEqual(await page.evaluate(() => SalesRuntime.current.route), 'pages/opportunity-create/index'); assert.deepEqual(await writes(page), []);
}));

for (const failure of ['conflict', 'missing-receipt']) test(failure + ' preserves the edit form and does not claim success or consume a pending redirect', () => fixture(async page => {
  await openDetail(page); const before = await savedRow(page); await edit(page);
  await page.evaluate(failure => {editProbe.failure = failure; editProbe.toasts = [];}, failure);
  await page.locator('#opportunityForm input[data-key=name]').fill('合成失败回执商机'); await page.locator('.footer .submit').click();
  await page.waitForFunction(() => !!SalesRuntime.current.data.error && !SalesRuntime.current.data.busy);
  assert.match(await page.evaluate(() => SalesRuntime.current.data.error), failure === 'conflict' ? /版本冲突/ : /完整保存回执/);
  await page.waitForTimeout(850); // The shared success path returns after 700 ms; errors must not take it.
  assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/opportunity-create/index');
  assert.equal(await page.locator('#opportunityForm input[data-key=name]').inputValue(), '合成失败回执商机');
  assert.equal(await page.evaluate(() => editProbe.toasts.some(t => /商机已保存|内容未变化/.test(t))), false);
  assert.deepEqual(await page.evaluate(() => [SalesRuntime.wx.getStorageSync('pendingOpenCustomerId'), SalesRuntime.wx.getStorageSync('pendingOpenOpportunityId')]), ['', '']);
  const sent = await writes(page); assert.equal(sent.length, 1); assert.equal(sent[0].data.version_no, before.version_no);
  const after = await savedRow(page); assert.equal(after.version_no, before.version_no + (failure === 'missing-receipt' ? 1 : 0));
}));
