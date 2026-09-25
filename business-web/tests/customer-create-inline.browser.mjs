/** 创建客户内联表单：隔离合成数据、原 Page 处理器；禁止访问真实业务服务。 */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({previewOnly: true});
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); server?.closeAllConnections(); await new Promise(r => server?.close(r));});
async function paint(p) {await p.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));}
const input = (p, key) => p.locator(`#customer-create-${key}`);
const create = p => p.getByRole('button', {name: '创建客户', exact: true});
async function value(p, key) {return p.evaluate(key => SalesRuntime.current.data.fields.find(f => f.key === key), key);}
async function go(p) {
  await p.evaluate(() => SalesRuntime.wx.navigateTo({url: '/pages/customer-create/index'}));
  await p.waitForFunction(() => SalesRuntime.current.route === 'pages/customer-create/index' && SalesRuntime.current.data.fields.length === 10 && SalesRuntime.current.directoryTeams?.length);
  await input(p, 'customer_name').waitFor(); await paint(p);
}
async function fixture(run, {size = [1366, 768], role = 'sales'} = {}) {
  const context = await browser.newContext({viewport: {width: size[0], height: size[1]}, serviceWorkers: 'block'});
  const errors = [], blocked = [];
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), role);
  await context.route('**/*', route => {
    const u = new URL(route.request().url());
    if (u.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(u.pathname)) {blocked.push(u.pathname); return route.abort();}
    return route.continue();
  });
  const p = await context.newPage(); p.setDefaultTimeout(12000); p.on('pageerror', e => errors.push(e.message));
  try {
    await p.goto(base + '/?mode=preview#/pages/index/index', {waitUntil: 'domcontentloaded'});
    await p.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await p.evaluate(() => {
      window.inlineProbe = {requests: [], toasts: [], failCreate: false};
      const preview = SalesPreview, toast = SalesRuntime.wx.showToast;
      SalesRuntime.wx.showToast = o => {inlineProbe.toasts.push(o.title); return toast(o);};
      window.SalesPreview = {...preview, request(o = {}) {
        const path = new URL(o.url, location.href).pathname, method = String(o.method || 'GET').toUpperCase();
        inlineProbe.requests.push({path, method, data: structuredClone(o.data)});
        if (inlineProbe.failCreate && method === 'POST' && /\/customers$/.test(path)) {
          const result = {statusCode: 503, data: {message: '合成建档失败，请重试'}};
          queueMicrotask(() => {o.success?.(result); o.complete?.(result);}); return {abort() {}};
        }
        return preview.request(o);
      }};
    });
    await go(p); await run(p);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    assert.deepEqual(await p.evaluate(() => SalesRuntime.errors), []);
  } catch (e) {
    console.error('INLINE_DIAGNOSTIC', await p.evaluate(() => ({route: SalesRuntime.current?.route, fields: SalesRuntime.current?.data?.fields, probe: window.inlineProbe, text: document.body.innerText.slice(-1800)})));
    throw e;
  } finally {await context.close();}
}
async function choose(p, key, index = 0) {
  await input(p, key).click();
  const listId = await input(p, key).getAttribute('aria-controls');
  const popup = p.locator('.ant-select-dropdown').filter({has: p.locator(`#${listId}`)});
  const option = popup.locator('.ant-select-item-option').nth(index);
  await option.click(); await popup.waitFor({state: 'hidden'}); await paint(p);
}
async function fillRequired(p, name = '合成内联建档验证客户') {
  await input(p, 'customer_name').fill(name);
  await choose(p, 'level_code'); await choose(p, 'lead_source');
  if (!(await value(p, 'target_team')).teamId) await choose(p, 'target_team');
  await input(p, 'contact_name').fill('合成联系人');
  await input(p, 'contact_title').fill('信息化负责人');
  await choose(p, 'contact_role'); await input(p, 'contact_title').focus(); await input(p, 'contact_title').press('Tab');
  await p.waitForFunction(() => SalesRuntime.current.data.missingCount === 0);
  await paint(p); assert.equal(await create(p).isEnabled(), true);
}
async function shot(p, name) {
  if (!process.env.CUSTOMER_INLINE_SHOTS) return;
  await mkdir(process.env.CUSTOMER_INLINE_SHOTS, {recursive: true});
  await paint(p); await p.screenshot({path: `${process.env.CUSTOMER_INLINE_SHOTS}/${name}.png`, animations: 'disabled'});
}
const writes = p => p.evaluate(() => inlineProbe.requests.filter(r => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(r.method) && !/^\/api\/v1\/notifications\/[^/]+\/read$/.test(r.path))); // 排除首页延迟标记已读，与建档无关。

test('直接连续录入、键盘顺序、中文合成事件及动态下拉选项', () => fixture(async p => {
  assert.equal(await create(p).isDisabled(), true);
  await input(p, 'customer_name').pressSequentially('Rapid input 123', {delay: 5});
  assert.equal(await input(p, 'customer_name').inputValue(), 'Rapid input 123');
  await input(p, 'customer_name').dispatchEvent('compositionstart');
  await input(p, 'customer_name').fill('合成中文客户名称');
  await input(p, 'customer_name').dispatchEvent('compositionend', {data: '名称'});
  await input(p, 'customer_name').press('Tab');
  assert.equal(await p.evaluate(() => document.activeElement.id), 'customer-create-industry');
  assert.equal((await value(p, 'customer_name')).value, '合成中文客户名称');
  await choose(p, 'industry');
  assert.ok(await p.evaluate(() => SalesRuntime.businessOptions().customer.industry.includes(SalesRuntime.current.data.fields.find(f => f.key === 'industry').value)));
  assert.equal(await p.getByRole('button', {name: '保存修改', exact: true}).count(), 0);
  assert.equal(await p.evaluate(() => SalesRuntime.current.data.editorVisible), false);
  assert.deepEqual(await writes(p), []);
}));

test('必填清空即时阻止创建、就地提示、保存并恢复空值和其他输入', () => fixture(async p => {
  await fillRequired(p);
  await input(p, 'customer_name').fill(''); await input(p, 'customer_name').press('Tab');
  await p.getByText('请填写客户名称', {exact: true}).waitFor();
  assert.equal(await create(p).isDisabled(), true);
  assert.equal((await value(p, 'customer_name')).value, '');
  await shot(p, 'required-error');
  await p.getByRole('button', {name: '保存草稿', exact: true}).click();
  await p.waitForFunction(() => inlineProbe.toasts.includes('草稿已保存'));
  await p.evaluate(() => SalesRuntime.wx.navigateBack()); await go(p);
  assert.equal(await input(p, 'customer_name').inputValue(), '');
  assert.equal(await input(p, 'contact_name').inputValue(), '合成联系人');
  assert.equal(await create(p).isDisabled(), true);
  assert.equal(await p.evaluate(() => SalesRuntime.current.data.draftRestored), true);
  assert.deepEqual(await writes(p), []);
  await shot(p, 'draft-restored');
}));

for (const role of ['sales', 'manager']) test(`${role} 保留团队稳定 ID、草稿、创建确认、失败后输入及合成创建结果`, () => fixture(async p => {
  await fillRequired(p, '  合成客户 ' + role + '  ');
  await choose(p, 'target_team', role === 'manager' ? 1 : 0);
  const team = await value(p, 'target_team'); assert.ok(team.teamId);
  await p.getByRole('button', {name: '保存草稿', exact: true}).click();
  await p.waitForFunction(() => inlineProbe.toasts.includes('草稿已保存'));
  await p.evaluate(() => SalesRuntime.wx.navigateBack()); await go(p);
  assert.equal((await value(p, 'target_team')).teamId, team.teamId);
  assert.equal(await input(p, 'customer_name').inputValue(), '合成客户 ' + role);
  await create(p).click(); await p.locator('.wx-modal-mask').getByRole('button', {name: '取消', exact: true}).click();
  assert.deepEqual(await writes(p), []);
  await p.evaluate(() => inlineProbe.failCreate = true);
  await create(p).click(); await p.locator('.wx-modal-mask').getByRole('button', {name: '确认创建', exact: true}).click();
  await p.waitForFunction(() => inlineProbe.toasts.includes('合成建档失败，请重试'));
  assert.equal(await input(p, 'customer_name').inputValue(), '合成客户 ' + role);
  assert.equal(await input(p, 'contact_title').inputValue(), '信息化负责人');
  await p.evaluate(() => inlineProbe.failCreate = false);
  await create(p).click(); await p.locator('.wx-modal-mask').getByRole('button', {name: '确认创建', exact: true}).click();
  await p.waitForFunction(() => inlineProbe.toasts.includes('客户创建成功'));
  const posts = (await writes(p)).filter(r => /\/customers$/.test(r.path));
  assert.equal(posts.length, 2);
  assert.equal(posts[1].data.name, '合成客户 ' + role);
  assert.equal(posts[1].data.target_team_id, team.teamId);
  assert.equal(posts[1].data.target_team, team.value);
  assert.equal(posts[1].data.contact_name, '合成联系人');
  assert.equal(posts[1].data.contact_title, '信息化负责人');
}, {role}));

test('沿用语音草案回填，处理时禁用编辑；只读字段保持只读', () => fixture(async p => {
  await input(p, 'partner_name').fill('手工填写伙伴'); await input(p, 'partner_name').press('Tab');
  await p.evaluate(() => SalesRuntime.current.setData({isParsing: true})); await paint(p);
  assert.equal(await input(p, 'customer_name').isDisabled(), true);
  assert.equal(await p.getByRole('button', {name: '保存草稿', exact: true}).isDisabled(), true);
  await p.evaluate(() => SalesRuntime.current.applyVoiceDraft({customer_name: '合成语音草案客户', contact_name: '语音联系人'}));
  await p.waitForFunction(() => document.querySelector('#customer-create-customer_name')?.value === '合成语音草案客户');
  assert.equal(await input(p, 'partner_name').inputValue(), '手工填写伙伴');
  assert.equal(await input(p, 'contact_name').inputValue(), '语音联系人');
  assert.match(await p.locator('.ds-cc-notice').innerText(), /AI 生成 · 待核对/);
  await p.evaluate(() => {const page = SalesRuntime.current; page.refresh(page.data.fields.map(f => f.key === 'customer_name' ? {...f, readonly: true} : f));}); await paint(p);
  assert.equal(await input(p, 'customer_name').getAttribute('readonly'), '');
  assert.deepEqual(await writes(p), []);
}));

for (const size of [[1440, 900], [1366, 768], [1024, 600], [390, 844]]) test(`${size.join('x')} 页面无横向溢出，全部字段及底部操作可达`, () => fixture(async p => {
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  assert.ok(overflow <= 1, 'horizontal overflow: ' + overflow);
  for (const key of ['customer_name', 'industry', 'contact_name', 'contact_title', 'contact_role']) {
    await input(p, key).scrollIntoViewIfNeeded();
    const box = await input(p, key).boundingBox(); assert.ok(box.width > 20 && box.x >= 0 && box.x + box.width <= size[0] + 1);
  }
  const save = p.getByRole('button', {name: '保存草稿', exact: true}); await save.scrollIntoViewIfNeeded();
  const reachable = await save.evaluate(e => {const b = e.getBoundingClientRect(), t = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); return e === t || e.contains(t);});
  assert.equal(reachable, true);
  if (size[0] > 900) assert.ok((await save.boundingBox()).y < size[1]);
  await input(p, 'customer_name').scrollIntoViewIfNeeded(); await shot(p, 'customer-create-' + size.join('x'));
} , {size}));
