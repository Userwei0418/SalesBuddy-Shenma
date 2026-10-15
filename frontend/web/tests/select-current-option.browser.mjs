/** Isolated selector regression: local source and synthetic values, no API or stored account. */
import test, {before, after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const read = path => fs.readFile(new URL('../' + path, import.meta.url), 'utf8');
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 900, height: 750}, serviceWorkers: 'block'});
  const page = await context.newPage(), errors = [], requests = [];
  page.setDefaultTimeout(2500); page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {requests.push(route.request().url()); return route.abort();});
  try {
    await page.setContent('<html><body><select id="native" aria-label="合成业务选项"><option value="0">直销</option><option value="1">合作伙伴</option><option value="2" disabled>已停用</option></select><button id="outside">取消位置</button><button id="anchor">合成成员</button></body></html>');
    await page.addStyleTag({content: await read('assets/vendor/tom-select-2.6.2/tom-select.default.min.css')});
    await page.addStyleTag({content: await read('select-components.css')});
    await page.addScriptTag({content: await read('assets/vendor/tom-select-2.6.2/tom-select.complete.min.js')});
    await page.addScriptTag({content: await read('select-components.js')});
    await page.evaluate(() => {
      window.probe = {commits: [], changes: 0, emits: 0};
      window.require = () => ({formFor: () => ({}), STAGES: []});
      window.Component = value => {window.opportunityDefinition = value;};
      window.Page = value => {window.taskDefinition = value;};
      window.wx = {vibrateShort() {}};
      document.querySelector('#native').addEventListener('change', event => {probe.changes++; window.onNativeChange?.(event);});
    });
    // Actual source handlers are loaded without page lifecycle/API calls.
    await page.addScriptTag({content: await read('source/miniprogram/components/opportunity-form/index.js')});
    await page.addScriptTag({content: await read('source/miniprogram/pages/management-task-create/index.js')});
    await run(page); assert.deepEqual(errors, []); assert.deepEqual(requests, []);
  } finally {await context.close();}
}
async function ready(page) {await page.waitForFunction(() => document.querySelector('#web-select-input')?.tomselect?.isOpen);}
async function closed(page) {await page.locator('#web-select-dialog').waitFor({state: 'detached'});}
const option = (page, value) => page.locator('#web-select-dialog .web-select-main .option[data-value="' + value + '"]');
async function openNative(page) {await page.locator('#native').click(); await ready(page);}
async function openCustom(page, config = {}) {
  await page.evaluate(config => SalesSelect.open({anchor: document.querySelector('#anchor'), title: '合成选择', options: [{value: 'a', text: '成员甲'}, {value: 'b', text: '成员乙'}], selected: ['a'], ...config, commit: values => probe.commits.push(values)}), config);
  await ready(page);
}
test('unknown opportunity channel confirms direct sales when native cursor already equals zero', () => fixture(async page => {
  await page.evaluate(() => {
    window.owner = {_tag: 'opportunity-form', properties: {disabled: false}, data: {partnerIndex: 0, form: {partner_mode: 'unknown', partner_id: null, partner_name: ''}},
      setData(values) {for (const [key, value] of Object.entries(values)) {if (key.startsWith('form.')) this.data.form[key.slice(5)] = value; else this.data[key] = value;}},
      emit() {probe.emits++;}, partnerMode: opportunityDefinition.methods.partnerMode};
    window.onNativeChange = event => SalesSelect.intercept(owner, 'partnerMode', {}, event, {value: event.target.value}, 'change');
  });
  await openNative(page); assert.equal(await page.evaluate(() => owner.data.form.partner_mode), 'unknown');
  await option(page, '0').click(); await closed(page);
  assert.deepEqual(await page.evaluate(() => ({form: owner.data.form, changes: probe.changes, emits: probe.emits})), {form: {partner_mode: 'direct', partner_id: null, partner_name: '直销'}, changes: 1, emits: 1});
}));
test('unconfirmed task recipient confirms first member without changing the picker cursor', () => fixture(async page => {
  await page.evaluate(() => {
    document.querySelector('#native').options[0].textContent = '合成接收人';
    window.owner = {data: {members: [{id: 'synthetic-first', name: '合成接收人'}], selectedAssigneeIndex: -1, selectedMember: null}, setData(values) {Object.assign(this.data, values);}};
    window.onNativeChange = event => taskDefinition.changeAssignee.call(owner, {detail: {value: event.target.value}});
  });
  await openNative(page); await option(page, '0').click(); await closed(page);
  assert.deepEqual(await page.evaluate(() => [owner.data.selectedAssigneeIndex, owner.data.selectedMember.id, probe.changes]), [0, 'synthetic-first', 1]);
}));
test('current and changed single options each commit exactly once', () => fixture(async page => {
  await openNative(page); await option(page, '0').click(); await closed(page);
  assert.equal(await page.evaluate(() => probe.changes), 1);
  await openNative(page); await option(page, '1').click(); await closed(page);
  assert.equal(await page.evaluate(() => probe.changes), 2);
  await openNative(page); await option(page, '1').click(); await closed(page);
  assert.equal(await page.evaluate(() => probe.changes), 3);
}));
test('Enter confirms the current active single option once', () => fixture(async page => {
  await page.locator('#native').focus(); await page.keyboard.press('Enter'); await ready(page);
  await page.locator('#web-select-dialog .ts-control input').focus();
  await page.evaluate(() => {const picker = document.querySelector('#web-select-input').tomselect; picker.setActiveOption(picker.getOption('0'));});
  await page.keyboard.press('Enter'); await closed(page);
  assert.equal(await page.evaluate(() => probe.changes), 1);
}));
test('Escape, close button and outside cancellation never confirm the displayed current option', () => fixture(async page => {
  for (const action of ['escape', 'close', 'outside']) {
    await openNative(page);
    if (action === 'escape') await page.keyboard.press('Escape');
    else if (action === 'close') await page.getByRole('button', {name: '关闭选择'}).click();
    else await page.locator('#outside').click();
    await closed(page); assert.equal(await page.evaluate(() => probe.changes), 0);
  }
}));
test('multiple selection keeps click and Enter changes pending until Apply', () => fixture(async page => {
  await openCustom(page, {multiple: true}); await option(page, 'a').click();
  assert.equal(await page.locator('#web-select-dialog').count(), 1);
  await page.locator('#web-select-dialog .ts-control input').focus();
  await page.evaluate(() => {const picker = document.querySelector('#web-select-input').tomselect; picker.setActiveOption(picker.getOption('b'));});
  await page.keyboard.press('Enter'); assert.deepEqual(await page.evaluate(() => probe.commits), []);
  await page.locator('#web-select-apply').click(); await closed(page);
  assert.deepEqual(await page.evaluate(() => probe.commits), [['b']]);
}));
test('disabled selections and locked selected members stay protected', () => fixture(async page => {
  await page.locator('#native').evaluate(select => {select.disabled = true; select.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}));});
  assert.equal(await page.locator('#web-select-dialog').count(), 0);
  await page.locator('#native').evaluate(select => {select.disabled = false;});
  await openNative(page); await option(page, '2').click({force: true});
  assert.equal(await page.locator('#web-select-dialog').count(), 1); assert.equal(await page.evaluate(() => probe.changes), 0);
  await page.keyboard.press('Escape');
  await openCustom(page, {multiple: true, options: [{value: 'a', text: '关联成员', disabled: true, locked: true}, {value: 'b', text: '成员乙'}]});
  await option(page, 'a').click({force: true}); await page.locator('.web-select-clear').click();
  assert.deepEqual(await page.evaluate(() => document.querySelector('#web-select-input').tomselect.items), ['a']);
  assert.deepEqual(await page.evaluate(() => probe.commits), []);
  await page.locator('#web-select-apply').click(); await closed(page);
  assert.deepEqual(await page.evaluate(() => probe.commits), [['a']]);
}));
test('a changed source guard or a locked picker cannot confirm through the new single-option path', () => fixture(async page => {
  await openNative(page); await page.locator('#native').evaluate(select => {select.options[0].textContent = '已更新';});
  await option(page, '0').click(); await closed(page); assert.equal(await page.evaluate(() => probe.changes), 0);
  await openCustom(page); await page.evaluate(() => document.querySelector('#web-select-input').tomselect.lock());
  await option(page, 'a').click(); assert.deepEqual(await page.evaluate(() => probe.commits), []);
  assert.equal(await page.locator('#web-select-dialog').count(), 1);
}));
