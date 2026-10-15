/** Four native 1.0.6 forms in the Web shell. Isolated synthetic data; business network is blocked. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const customer = '00000010-0000-4000-8000-000000000001', opportunity = '00000011-0000-4000-8000-000000000001';
const names = {'customer-create': '创建客户', 'customer-edit': '维护客户', 'customer-assign-confirm': '建档下发', 'demo-create': 'Demo场景'};
let server, browser, base;
before(async () => {server = createSalesWebServer({target: ''}); await new Promise(r => server.listen(0, '127.0.0.1', r)); base = `http://127.0.0.1:${server.address().port}`; browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close(); await new Promise(r => server?.close(r));});
async function paint(page) {await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));}
async function fixture(run, {size = [1366, 768], role = 'manager'} = {}) {
  const context = await browser.newContext({viewport: {width: size[0], height: size[1]}, serviceWorkers: 'block'}), errors = [], blocked = [];
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), role);
  await context.route('**/*', route => {const u = new URL(route.request().url()); if (u.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(u.pathname)) {blocked.push(u.pathname); return route.abort();} return route.continue();});
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(base + '/?mode=preview'); await page.waitForFunction(() => SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {
      window.formProbe = {requests: [], toasts: [], sceneReadonly: false, denyDemo: false, assignmentFail: false};
      const preview = SalesPreview, toast = SalesRuntime.wx.showToast;
      SalesRuntime.wx.showToast = options => {formProbe.toasts.push(options.title); return toast(options);};
      window.SalesPreview = {...preview, request(options = {}) {
        const path = new URL(options.url, location.href).pathname, method = String(options.method || 'GET').toUpperCase();
        formProbe.requests.push({path, method, data: structuredClone(options.data)});
        if (formProbe.assignmentFail && method === 'POST' && /\/assignments$/.test(path)) {
          const result = {statusCode: 503, data: {message: '合成下发暂不可用'}}; queueMicrotask(() => {options.success?.(result); options.complete?.(result);}); return {abort() {}};
        }
        return preview.request({...options, success(response) {
          const next = structuredClone(response);
          if (/\/demo-scenes$/.test(path) && method === 'GET' && formProbe.denyDemo) next.data.editable = false;
          if (/\/demo-scenes\/[^/]+$/.test(path) && method === 'GET' && formProbe.sceneReadonly) next.data.can_edit = false;
          options.success?.(next);
        }});
      }};
    });
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } catch (error) {
    console.error('EXTENDED_FORM_DIAGNOSTIC', await page.evaluate(() => ({route: SalesRuntime.current.route, missing: SalesRuntime.current.data.missingCount, error: SalesRuntime.current.data.demoError, eligible: SalesRuntime.current.data.eligible, toasts: formProbe.toasts, boxes: [...document.querySelectorAll('#page-root,.web-extended-form,.web-extended-scroll,.web-extended-footer,.editor-sheet')].map(e => {const b = e.getBoundingClientRect(); return {class: e.className, x: b.x, y: b.y, w: b.width, h: b.height, scroll: e.scrollHeight, client: e.clientHeight};})}))); throw error;
  } finally {await context.close();}
}
async function go(page, route, extra = '') {
  await page.evaluate(({route, extra, customer, opportunity}) => SalesRuntime.wx.navigateTo({url: `/pages/${route}/index?customerId=${customer}&customer_id=${customer}&opportunity_id=${opportunity}${extra}`}), {route, extra, customer, opportunity});
  await page.waitForFunction(route => SalesRuntime.current.route === `pages/${route}/index` && (route === 'demo-create' ? SalesRuntime.current.data.eligible : route === 'customer-edit' ? !SalesRuntime.current.data.loading && !!SalesRuntime.current.data.form.name : SalesRuntime.current.data.fields.length > 0), route);
  if (route === 'customer-create') await page.waitForFunction(() => SalesRuntime.current.directoryTeams?.length);
  if (route === 'customer-assign-confirm') await page.waitForFunction(() => SalesRuntime.current.members?.length);
  await paint(page);
}
async function writes(page) {return page.evaluate(() => formProbe.requests.filter(r => ['POST', 'PATCH', 'DELETE', 'PUT'].includes(r.method)));}
async function fitted(page) {
  await paint(page);
  const d = await page.evaluate(() => {const e = document.querySelector('#page-root'); return {horizontal: document.documentElement.scrollWidth - innerWidth, client: e.clientHeight, scroll: e.scrollHeight, bottom: e.getBoundingClientRect().bottom, window: innerHeight};});
  assert.ok(d.horizontal <= 1 && d.scroll <= d.client + 2 && d.bottom <= d.window + 1, JSON.stringify(d));
}
async function visible(page, selector, hit = false) {
  const rows = await page.locator(selector).evaluateAll(els => els.map(e => {const b = e.getBoundingClientRect(), target = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); return {x: b.x, y: b.y, w: b.width, h: b.height, iw: innerWidth, ih: !e.closest('.editor-layer') && document.querySelector('#mobile-nav')?.getBoundingClientRect().height > 0 ? document.querySelector('#mobile-nav').getBoundingClientRect().top : innerHeight, hit: e === target || e.contains(target), target: target?.className};}));
  assert.ok(rows.length, selector); for (const b of rows) assert.ok(b.w > 0 && b.h > 0 && b.x >= 0 && b.y >= 0 && b.x + b.w <= b.iw + 1 && b.y + b.h <= b.ih + 1 && (!hit || b.hit), selector + JSON.stringify(b));
}
async function shot(page, name) {if (!process.env.EXTENDED_FORM_SHOTS) return; await mkdir(process.env.EXTENDED_FORM_SHOTS, {recursive: true}); await page.screenshot({path: `${process.env.EXTENDED_FORM_SHOTS}/全页-表单-${name}.png`});}
async function editField(page, key, value, cancel = false) {
  const index = await page.evaluate(key => SalesRuntime.current.data.fields.findIndex(f => f.key === key), key); assert.ok(index >= 0);
  if (await page.locator('.review-customer-create').count()) {
    const row = page.locator(`.field-row[data-field-key="${key}"]`), input = row.locator('.review-customer-input');
    if (await input.count()) {
      assert.equal(cancel, false, 'text fields now commit directly on blur');
      await input.fill(value); await input.press('Tab');
      await page.waitForFunction(({key, value}) => SalesRuntime.current.data.fields.find(f => f.key === key).value === value.trim(), {key, value});
    } else {
      await row.locator('.review-customer-choice').click(); await page.locator('#web-select-dialog').waitFor();
      if (cancel) await page.keyboard.press('Escape');
      else {const options = page.locator('#web-select-dialog .option[data-selectable]'); await (typeof value === 'string' ? options.filter({hasText: value}).first() : options.nth(value || 0)).click();}
      await page.locator('#web-select-dialog').waitFor({state: 'detached'});
    }
    await paint(page); return;
  }
  await page.locator(`[data-handler=openEditor][data-index="${index}"]`).click(); await page.locator('.editor-sheet').waitFor();
  const type = await page.evaluate(() => SalesRuntime.current.data.editorType);
  if (type === 'select') {const options = page.locator('.option-item'); await (typeof value === 'string' ? options.filter({hasText: value}).first() : options.nth(value || 0)).click();}
  else await page.locator(type === 'textarea' ? '.editor-textarea textarea' : '.editor-control input').fill(value);
  await page.locator(cancel ? '.editor-cancel' : '.editor-save').click(); await page.locator('.editor-sheet').waitFor({state: 'detached'});
}
async function fillRequired(page, name) {
  const fields = await page.evaluate(() => SalesRuntime.current.data.fields.map(({key, required, readonly, value}) => ({key, required, readonly, value})));
  for (const f of fields.filter(f => f.required && !f.readonly && (!f.value || f.key === 'customer_name'))) {
    const value = {customer_name: name, contact_name: '合成联系人', contact_title: '合成职位', first_action: '合成首次跟进：核实需求和拜访时间'}[f.key];
    await editField(page, f.key, value === undefined ? 0 : value);
  }
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.missingCount), 0);
}

for (const size of [[1366, 768], [1024, 600], [630, 800], [390, 844]]) for (const route of Object.keys(names)) test(`${route} ${size.join('x')} fits the shell, retains an unobstructed action bar and scrolls only content`, () => fixture(async page => {
  await go(page, route); await fitted(page); await visible(page, '.web-extended-footer button', true); await shot(page, names[route] + '-' + size[0]);
  if (route === 'demo-create') {
    await page.locator('.demo-description').fill('合成长描述：'.repeat(150));
    for (let i = 0; i < 4; i++) {await page.locator('[data-handler=addDemoScene]').click(); await page.waitForFunction(count => document.querySelectorAll('.demo-scene').length === count, i + 2);}
    assert.equal(await page.locator('.demo-scene').count(), 5);
  } else if (route !== 'customer-edit') {
    await editField(page, 'customer_name', '合成长客户名称'.repeat(24));
    assert.equal(await page.locator('.field-row').first().getAttribute('title'), '合成长客户名称'.repeat(24));
  }
  const scroll = page.locator('.web-extended-scroll').first(); assert.ok(await scroll.evaluate(e => e.clientHeight) >= 140, 'long headings must leave usable form space'); await scroll.evaluate(e => e.scrollTop = e.scrollHeight); await fitted(page); await visible(page, '.web-extended-footer button', true);
  await scroll.evaluate(e => e.scrollTop = 0); await paint(page);
  assert.deepEqual(await writes(page), []);
}, {size, role: route === 'demo-create' ? 'fde' : 'manager'}));

test('create customer keeps required validation, selector cancel, scoped draft, confirm cancel and saved readback', () => fixture(async page => {
  await go(page, 'customer-create'); await page.locator('[data-handler=submitCustomer]').click(); assert.deepEqual(await writes(page), []);
  assert.ok(await page.evaluate(() => formProbe.toasts.some(t => /必填|补充/.test(t))));
  await editField(page, 'lead_source', 0, true); assert.equal(await page.evaluate(() => SalesRuntime.current.data.fields.find(f => f.key === 'lead_source').value), '');
  await fillRequired(page, '合成Web建档客户'); await page.locator('[data-handler=saveDraft]').click();
  await page.evaluate(() => SalesRuntime.wx.navigateBack()); await go(page, 'customer-create');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.draftRestored), true); assert.equal(await page.evaluate(() => SalesRuntime.current.data.fields[0].value), '合成Web建档客户');
  const team = await page.evaluate(() => SalesRuntime.current.data.fields.find(f => f.key === 'target_team').teamId); assert.ok(team);
  await page.locator('[data-handler=submitCustomer]').click(); await page.locator('.wx-modal-mask').getByRole('button', {name: '取消', exact: true}).click(); assert.deepEqual(await writes(page), []);
  await page.locator('[data-handler=submitCustomer]').click(); await page.locator('.wx-modal-mask').getByRole('button', {name: '确认创建', exact: true}).click();
  await page.waitForFunction(() => !!SalesRuntime.wx.getStorageSync('lastCreatedCustomer')?.id);
  const id = await page.evaluate(() => SalesRuntime.wx.getStorageSync('lastCreatedCustomer').id);
  await page.waitForFunction(() => SalesRuntime.current.route !== 'pages/customer-create/index');
  const record = await page.evaluate(id => JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).customers.find(c => c.id === id), id);
  assert.equal(record.name, '合成Web建档客户'); assert.equal(record.owner_id, null);
  const sent = (await writes(page)).find(r => /\/customers$/.test(r.path)); assert.equal(sent.data.target_team_id, team);
  await page.evaluate(id => SalesRuntime.wx.navigateTo({url: '/pages/customer-edit/index?customerId=' + id}), id); await page.waitForFunction(() => !SalesRuntime.current.data.loading && SalesRuntime.current.data.form?.name === '合成Web建档客户'); assert.ok(await page.locator('.customer-name-readonly').innerText().then(text => text.includes('合成Web建档客户')));
}));

test('customer edit keeps Agent facts/name read-only, validates required data and saves only manual fields', () => fixture(async page => {
  await go(page, 'customer-edit'); const initial = await page.evaluate(() => ({agent: SalesRuntime.current.data.agentView, name: SalesRuntime.current.data.form.name}));
  assert.equal(await page.locator('[data-key=name]').count(), 0); assert.match(await page.locator('.agent-card').innerText(), /只读/);
  await page.locator('input[data-key=partner_name]').fill(''); await page.locator('[data-handler=submit]').click(); assert.deepEqual(await writes(page), []);
  await page.locator('input[data-key=partner_name]').fill('合成维护伙伴'); await page.locator('input[data-key=contact_name]').fill('合成维护联系人');
  await page.locator('[data-handler=submit]').click(); await page.waitForFunction(() => SalesRuntime.current.route !== 'pages/customer-edit/index');
  const sent = (await writes(page)).find(r => r.method === 'PATCH'); assert.ok(sent); assert.equal(sent.data.partner_name, '合成维护伙伴');
  for (const key of ['name', 'demand_summary', 'next_action', 'potential', 'relationship', 'quadrant']) assert.equal(key in sent.data, false);
  await go(page, 'customer-edit'); assert.equal(await page.locator('input[data-key=partner_name]').inputValue(), '合成维护伙伴'); assert.equal(await page.locator('input[data-key=contact_name]').inputValue(), '合成维护联系人');
  assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.agentView), initial.agent); assert.equal(await page.evaluate(() => SalesRuntime.current.data.form.name), initial.name);
}));

test('manager customer assignment retains readonly team and two-phase retry without duplicate creation', () => fixture(async page => {
  await go(page, 'customer-assign-confirm'); await page.locator('[data-handler=confirmArchive]').click(); assert.deepEqual(await writes(page), []);
  const teamIndex = await page.evaluate(() => SalesRuntime.current.data.fields.findIndex(f => f.key === 'assigned_team'));
  await page.locator(`[data-handler=openEditor][data-index="${teamIndex}"]`).click(); assert.equal(await page.locator('.editor-sheet').count(), 0);
  await fillRequired(page, '合成建档下发客户'); await page.locator('[data-handler=confirmArchive]').click(); await page.locator('.wx-modal-mask').getByRole('button', {name: '取消', exact: true}).click(); assert.deepEqual(await writes(page), []);
  await page.evaluate(() => formProbe.assignmentFail = true); await page.locator('[data-handler=confirmArchive]').click(); await page.locator('.wx-modal-mask').getByRole('button', {name: '确认建档', exact: true}).click();
  await page.waitForFunction(() => !!SalesRuntime.current.createdCustomerId && !SalesRuntime.current.data.submitting);
  const id = await page.evaluate(() => SalesRuntime.current.createdCustomerId); assert.equal((await writes(page)).filter(r => /\/customers$/.test(r.path)).length, 1);
  await page.locator('[data-handler=openEditor][data-index="0"]').click(); assert.equal(await page.locator('.editor-sheet').count(), 0);
  await page.evaluate(() => formProbe.assignmentFail = false); await page.locator('[data-handler=confirmArchive]').click(); await page.locator('.wx-modal-mask').getByRole('button', {name: '确认建档', exact: true}).click();
  await page.waitForFunction(() => !!SalesRuntime.wx.getStorageSync('lastManagementCustomerSuccess')?.id);
  assert.equal((await writes(page)).filter(r => /\/customers$/.test(r.path)).length, 1); assert.equal((await writes(page)).filter(r => /\/assignments$/.test(r.path)).length, 2);
  const record = await page.evaluate(id => JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).customers.find(c => c.id === id), id); assert.ok(record.owner_id); assert.equal(record.name, '合成建档下发客户');
}));

test('Demo retains validation, add/remove, save readback, authorized detail edit and delete cancel', () => fixture(async page => {
  await go(page, 'demo-create'); await page.locator('[data-handler=submitDemoScenes]').click(); assert.match(await page.locator('.demo-error').innerText(), /名称和描述/); assert.deepEqual(await writes(page), []);
  await page.locator('[data-handler=addDemoScene]').click(); await page.waitForFunction(() => document.querySelectorAll('.demo-scene').length === 2); await page.locator('.demo-remove').last().click(); await page.waitForFunction(() => document.querySelectorAll('.demo-scene').length === 1);
  await page.locator('.demo-input').fill('合成场景'); await page.locator('.demo-description').fill('合成业务问题、演示内容和预期效果'); await page.locator('[data-handler=submitDemoScenes]').click();
  await page.waitForFunction(() => SalesRuntime.current.route !== 'pages/demo-create/index');
  const record = await page.evaluate(() => JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).demoScenes.find(s => s.name === '合成场景')); assert.equal(record.opportunity_id, opportunity); assert.equal(record.version_no, 1);
  await go(page, 'demo-create', '&demo_id=' + record.id + '&view=1'); await fitted(page); assert.equal(await page.locator('.demo-detail-copy').innerText(), record.description);
  await page.locator('[data-handler=deleteScene]').click(); await page.locator('.wx-modal-mask').getByRole('button', {name: '取消', exact: true}).click(); assert.equal((await writes(page)).filter(r => r.method === 'DELETE').length, 0);
  await page.locator('[data-handler=startEditing]').click(); await page.locator('.demo-input').fill('合成编辑场景'); await page.locator('[data-handler=submitDemoScenes]').click();
  await page.waitForFunction(() => SalesRuntime.current.route !== 'pages/demo-create/index');
  const after = await page.evaluate(id => JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).demoScenes.find(s => s.id === id), record.id); assert.equal(after.version_no, 2); assert.equal(after.name, '合成编辑场景');
  await page.evaluate(() => formProbe.sceneReadonly = true); await go(page, 'demo-create', '&demo_id=' + record.id + '&view=1');
  assert.equal(await page.locator('[data-handler=startEditing],[data-handler=deleteScene]').count(), 0); await page.evaluate(() => SalesRuntime.current.startEditing()); assert.equal(await page.evaluate(() => SalesRuntime.current.data.viewing), true);
}, {role: 'fde'}));

test('missing customer capabilities and Demo object permission never produce editable forms or writes', () => fixture(async page => {
  for (const route of ['customer-create', 'customer-edit', 'customer-assign-confirm']) {
    await page.evaluate(({route, customer}) => SalesRuntime.wx.navigateTo({url: `/pages/${route}/index?customerId=${customer}`}), {route, customer});
    await page.locator('.access-empty').waitFor(); assert.equal(await page.locator('.web-extended-footer').count(), 0);
  }
  await page.evaluate(() => formProbe.denyDemo = true); await page.evaluate(({customer, opportunity}) => SalesRuntime.wx.navigateTo({url: `/pages/demo-create/index?customer_id=${customer}&opportunity_id=${opportunity}`}), {customer, opportunity});
  await page.waitForFunction(() => SalesRuntime.current.data.accessMessage !== '正在加载关联商机…'); assert.equal(await page.evaluate(() => SalesRuntime.current.data.eligible), false); assert.equal(await page.locator('[data-handler=submitDemoScenes]').count(), 0); assert.deepEqual(await writes(page), []);
}, {role: 'fde'}));

test('long customer selector and follow-up editor retain searchable options and cancel/save access', () => fixture(async page => {
  await go(page, 'customer-create');
  await page.evaluate(() => {SalesRuntime.current.directoryTeams = Array.from({length: 80}, (_, i) => ({id: 'synthetic-team-' + i, name: '合成超长团队名称选项' + i}));});
  await page.locator('.field-row[data-field-key="target_team"] .review-customer-choice').click();
  await page.locator('#web-select-dialog').waitFor(); await paint(page);
  await visible(page, '#web-select-dialog h2,#web-select-dialog .web-select-close', true);
  const pane = page.locator('#web-select-dialog .ts-dropdown-content'); assert.ok(await pane.evaluate(e => e.scrollHeight > e.clientHeight)); await pane.evaluate(e => e.scrollTop = e.scrollHeight);
  await page.keyboard.press('Escape'); await page.locator('#web-select-dialog').waitFor({state: 'detached'});
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.fields.find(f => f.key === 'target_team').teamId || ''), '');
  await go(page, 'customer-assign-confirm'); const text = '合成首次跟进说明'.repeat(55);
  await editField(page, 'first_action', text); assert.equal(await page.evaluate(() => SalesRuntime.current.data.fields.find(f => f.key === 'first_action').value), text);
  await fitted(page); await visible(page, '.web-extended-footer button', true); assert.deepEqual(await writes(page), []);
}, {size: [390, 844]}));
