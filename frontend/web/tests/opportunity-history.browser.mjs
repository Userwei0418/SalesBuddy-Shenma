// Compiled browser acceptance with synthetic contract responses, never live DB evidence.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import fs from 'node:fs';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser, server, base;
before(async () => {server = createSalesWebServer({previewOnly: true}); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); base = `http://127.0.0.1:${server.address().port}`; browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close(); if (server) await new Promise(resolve => {server.close(resolve); server.closeAllConnections();});});
const uid = (prefix, n) => `${String(prefix).padStart(8, '0')}-0000-4000-8000-${String(n).padStart(12, '0')}`;
function fixture() {
  const calls = [], members = Array.from({length: 101}, (_, i) => ({id: uid(70, i + 1), name: i === 100 ? '历史停用同事' : `创建人${i}`, is_active: i !== 100, team_ids: i === 100 ? [] : ['south']}));
  const rows = Array.from({length: 31}, (_, i) => ({id: uid(11, i + 1), customer_id: uid(10, Math.floor(i / 4) + 1), name: `历史商机 ${String(i).padStart(2, '0')}`, customer_name: `合成客户 ${i}`, creator_id: i < 21 ? members[0].id : i < 30 ? members[100].id : null, creator_name: i < 21 ? '创建人0' : i < 30 ? '历史停用同事' : null, owner_name: '转交后的负责人', created_at: null, business_created_on: i === 30 ? null : i < 21 ? '2026-10-09' : '2026-09-01', status: 'open', stage: 'qualified', can_read_customer: i % 2 === 0}));
  let denied = false, defer, release;
  return {calls, rows, members, set denied(value) {denied = value;}, set defer(value) {defer = value;}, release() {release?.();}, async handle(request) {
    const url = new URL(request.url, base), p = url.searchParams; calls.push({path: url.pathname, ...Object.fromEntries(p)});
    const ok = data => ({statusCode: 200, data});
    if (url.pathname.endsWith('/options')) {
      const offset = Number(p.get('offset')), more = offset + 100 < members.length; return ok({teams: [{id: 'south', name: '南区'}], members: members.slice(offset, offset + 100), total: members.length, has_more: more, next_offset: more ? offset + 100 : null, default_scope: 'self', team_basis: 'current_creator_membership'});
    }
    if (denied) return {statusCode: 403, data: {code: 'FORBIDDEN', message: '当前商机查看权限已撤销'}};
    if (p.get('q') === defer) await new Promise(resolve => {release = resolve;});
    let items = rows.filter(row => p.get('scope') !== 'self' || row.creator_id === members[0].id);
    if (p.get('member_id')) items = items.filter(row => row.creator_id === p.get('member_id'));
    if (p.get('team_id')) items = items.filter(row => row.creator_id === members[0].id);
    if (p.get('q')) items = items.filter(row => row.name.includes(p.get('q')));
    if (p.get('created_from')) items = items.filter(row => row.business_created_on && row.business_created_on >= p.get('created_from'));
    if (p.get('created_to')) items = items.filter(row => row.business_created_on && row.business_created_on <= p.get('created_to'));
    const offset = Number(p.get('offset')), limit = Number(p.get('limit')), more = offset + limit < items.length;
    return ok({items: items.slice(offset, offset + limit), total: items.length, has_more: more, next_offset: more ? offset + limit : null});
  }};
}
async function client(f, width = 1440, role = 'sales') {
  const context = await browser.newContext({viewport: {width, height: 900}, serviceWorkers: 'block'});
  await context.exposeBinding('__historyFixture', (_, request) => f.handle(request));
  await context.addInitScript(role => {
    sessionStorage.setItem('sales-web:preview-role', role);
    let provider;
    Object.defineProperty(window, 'SalesPreview', {configurable: true, get: () => provider, set(value) {
      const original = value.request.bind(value);
      provider = {...value, request(options) {
        if (!new URL(options.url, location.href).pathname.includes('/opportunities/creation-history')) return original(options);
        let aborted = false;
        window.__historyFixture({url: options.url, method: options.method}).then(response => {if (!aborted) {options.success?.(response); options.complete?.(response);}});
        return {abort() {aborted = true;}};
      }};
    }});
  }, role);
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message)); page.setDefaultTimeout(10000);
  await page.goto(`${base}/?mode=preview#/pages/opportunity-history/index`);
  await page.getByRole('combobox', {name: '创建人范围'}).waitFor();
  await page.getByRole('button', {name: '历史商机 00', exact: true}).waitFor();
  return {context, page, errors};
}
async function selectScope(page, label) {
  await page.getByRole('combobox', {name: '创建人范围'}).click();
  await page.getByRole('combobox', {name: '创建人范围'}).fill(label);
  await page.locator('.ant-select-dropdown:visible').getByText(label, {exact: true}).click();
}
async function search(page, value) {await page.getByPlaceholder('商机或客户名称').fill(value); await page.getByPlaceholder('商机或客户名称').press('Enter');}

test('history entry, second page, detail return and creator scope keep distinct ownership', {timeout: 45000}, async () => {
  const f = fixture(), c = await client(f), {page} = c;
  try {
    assert.deepEqual(f.calls.filter(call => call.path.endsWith('/options')).map(call => call.offset), ['0', '100']);
    assert.equal(await page.getByRole('menuitem', {name: '商机创建', exact: true}).innerText(), '商机创建');
    await page.getByTitle('2', {exact: true}).click(); await page.getByRole('button', {name: '历史商机 20', exact: true}).waitFor();
    await page.getByRole('button', {name: '历史商机 20', exact: true}).click();
    await page.waitForFunction(() => SalesRuntime.current?.route === 'pages/customer-assets/index');
    await page.getByRole('button', {name: '返回上一页', exact: true}).click(); await page.getByRole('button', {name: '历史商机 20', exact: true}).waitFor();
    assert.equal(f.calls.filter(call => !call.path.endsWith('/options')).at(-1).offset, '20');
    await selectScope(page, '历史停用同事（已停用）'); await page.getByRole('button', {name: '历史商机 21', exact: true}).waitFor();
    const call = f.calls.filter(call => !call.path.endsWith('/options')).at(-1);
    assert.equal(call.member_id, f.members[100].id); assert.equal(call.offset, '0'); assert.equal(call.owner_id, undefined);
    assert.match(await page.locator('.opp-creation-history').innerText(), /转交后的负责人/);
    assert.equal(await page.getByRole('button', {name: '合成客户 21', exact: true}).count(), 0);
    await selectScope(page, '全部可见创建记录'); await page.getByRole('button', {name: '历史商机 00', exact: true}).waitFor();
    await page.getByTitle('2', {exact: true}).click(); await page.getByText('日期未记录', {exact: true}).waitFor();
    assert.equal(await page.getByText('创建人未记录', {exact: true}).count(), 1);
    assert.deepEqual(c.errors, []);
  } finally {await c.context.close();}
});

test('compact history stays one filter row, dates/search reset pages, failure and late results clear safely', {timeout: 45000}, async () => {
  const f = fixture(), c = await client(f, 1024), {page} = c;
  try {
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const row = page.locator('.pc-filterbar-row'); const box = await row.boundingBox();
    assert.ok(box.height < 60, 'filters should occupy one row');
    if (!await page.getByPlaceholder('创建开始日期').isVisible()) await page.getByRole('button', {name: /更多筛选/}).click();
    await page.getByPlaceholder('创建开始日期').fill('2026-10-01'); await page.getByPlaceholder('创建开始日期').press('Tab');
    await page.getByPlaceholder('创建结束日期').fill('2026-10-09'); await page.getByPlaceholder('创建结束日期').press('Enter');
    await page.waitForFunction(() => document.querySelector('.opp-history-context')?.textContent.includes('2026-10-01'));
    if (await page.getByRole('button', {name: '收起', exact: true}).isVisible()) await page.getByRole('button', {name: '收起', exact: true}).click();
    await page.getByRole('button', {name: '清除筛选', exact: true}).click(); await page.getByRole('button', {name: '历史商机 00', exact: true}).waitFor();
    f.defer = '历史商机 01'; await search(page, '历史商机 01');
    await page.getByPlaceholder('商机或客户名称').fill('历史商机 02'); await page.getByPlaceholder('商机或客户名称').press('Enter');
    await page.getByRole('button', {name: '历史商机 02', exact: true}).waitFor(); f.release();
    await page.waitForTimeout(100);
    assert.equal(await page.getByRole('button', {name: '历史商机 01', exact: true}).count(), 0);
    f.denied = true; await page.getByRole('button', {name: '刷新创建历史', exact: true}).click(); await page.getByRole('alert').filter({hasText: '当前商机查看权限已撤销'}).waitFor();
    assert.equal(await page.getByRole('button', {name: '历史商机 02', exact: true}).count(), 0);
    f.denied = false; await page.getByRole('button', {name: /^重\s*试$/}).click(); await page.getByRole('button', {name: '历史商机 02', exact: true}).waitFor();
    if (process.env.HISTORY_SHOTS) {fs.mkdirSync(process.env.HISTORY_SHOTS, {recursive: true}); await page.screenshot({path: `${process.env.HISTORY_SHOTS}/history-1024.png`});}
    assert.deepEqual(c.errors, []);
  } finally {f.release(); await c.context.close();}
});

test('read-only FDE can reach history while create remains a separately authorized action', {timeout: 30000}, async () => {
  const f = fixture(), c = await client(f, 1440, 'fde');
  try {assert.equal(await c.page.getByRole('button', {name: '新增商机', exact: true}).count(), 0); assert.equal(await c.page.getByRole('menuitem', {name: '商机创建', exact: true}).count(), 1); assert.deepEqual(c.errors, []);}
  finally {await c.context.close();}
});

test('shrunk final page refetches a reachable page under the unchanged search and creator scope', {timeout: 30000}, async () => {
  const f = fixture(), c = await client(f), {page} = c;
  try {
    await search(page, '历史商机'); await page.getByTitle('2', {exact: true}).click(); await page.getByRole('button', {name: '历史商机 20', exact: true}).waitFor();
    f.rows.splice(19, 2);
    await page.getByRole('button', {name: '刷新创建历史', exact: true}).click(); await page.getByRole('button', {name: '历史商机 00', exact: true}).waitFor();
    const last = f.calls.filter(call => !call.path.endsWith('/options')).slice(-2);
    assert.deepEqual(last.map(call => call.offset), ['20', '0']); assert.ok(last.every(call => call.scope === 'self' && call.q === '历史商机'));
    assert.equal(await page.locator('.ant-table-tbody tr[data-row-key]').count(), 19);
    assert.equal(await page.locator('.ant-pagination-item-active').innerText(), '1'); assert.deepEqual(c.errors, []);
  } finally {await c.context.close();}
});

test('removed selected creator shows an unavailable label and preserves the denied scope', {timeout: 30000}, async () => {
  const f = fixture(), c = await client(f), {page} = c;
  try {
    const id = f.members[100].id;
    await selectScope(page, '历史停用同事（已停用）'); await page.getByRole('button', {name: '历史商机 21', exact: true}).waitFor();
    f.members.splice(100, 1); f.denied = true;
    await page.getByRole('button', {name: '刷新创建历史', exact: true}).click(); await page.getByRole('combobox', {name: '创建人范围'}).waitFor();
    await page.getByText('所选创建人已不可用，请重新选择', {exact: true}).waitFor();
    assert.doesNotMatch(await page.locator('.opp-creation-history').innerText(), new RegExp(id));
    const last = f.calls.filter(call => !call.path.endsWith('/options')).at(-1); assert.equal(last.member_id, id); assert.equal(last.scope, 'team');
    assert.equal(await page.getByRole('button', {name: '历史商机 21', exact: true}).count(), 0);
    assert.deepEqual(c.errors, []);
  } finally {await c.context.close();}
});

test('preview creation captures actor and native time and appears in the new history entry', {timeout: 45000}, async () => {
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, serviceWorkers: 'block'}), page = await context.newPage();
  try {
    await page.goto(`${base}/?mode=preview#/pages/opportunity-history/index`);
    await page.getByRole('button', {name: '新增商机', exact: true}).click();
    await page.waitForFunction(() => SalesRuntime.current?.route === 'pages/opportunity-create/index');
    await page.getByRole('button', {name: '创建历史', exact: true}).waitFor();
    const record = await page.evaluate(async () => {
      const api = SalesRuntime.require('utils/apiClient'), customers = await api.listCustomers({pageSize: 20}), c = customers.items.find(row => row.owner_id === SalesRuntime.app.globalData.session.userId);
      const result = await api.createOpportunity(c.id, {action: 'create', name: '创建历史合成验收', amount: '100000', probability: 10, status: 'open', expected_close_date: '2026-12-31', sales_channel: 'direct', fde_member_ids: []});
      return {id: result.id || result.opportunity_id, actor: SalesRuntime.app.globalData.session.userId};
    });
    await page.getByRole('button', {name: '创建历史', exact: true}).click();
    await page.getByRole('button', {name: '创建历史合成验收', exact: true}).waitFor();
    const row = await page.evaluate(async id => (await SalesRuntime.require('utils/apiClient').request({path: '/opportunities/creation-history?q=创建历史合成验收'})).items.find(row => row.id === id), record.id);
    assert.equal(row.creator_id, record.actor); assert.ok(row.created_at); assert.ok(row.business_created_on);
    if (process.env.HISTORY_SHOTS) {fs.mkdirSync(process.env.HISTORY_SHOTS, {recursive: true}); await page.screenshot({path: `${process.env.HISTORY_SHOTS}/history-1440.png`});}
  } finally {await context.close();}
});
