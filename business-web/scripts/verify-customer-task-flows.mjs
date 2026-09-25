// Isolated local example data only. No route interception, event dispatch, or business-state injection.
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const output = path.join(root, '.runtime/flow-verification'); fs.mkdirSync(output, {recursive: true});
const base = process.env.APP_URL || process.env.SALES_WEB_URL || 'http://127.0.0.1:5186';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname), 'Verification is restricted to a local example server');
const browser = await chromium.launch({headless: true, channel: 'chrome'});
const context = await browser.newContext({viewport: {width: 1440, height: 1100}});
const page = await context.newPage(), records = [], errors = [];
page.on('pageerror', e => errors.push(e.message));
const note = (step, data = {}) => {records.push({step, at: new Date().toISOString(), url: page.url(), ...data}); console.log(JSON.stringify(records.at(-1)));};
const settle = () => page.waitForTimeout(450);
const confirm = async () => {await page.locator('.wx-modal-buttons button').last().click(); await settle();};
const state = () => page.evaluate(() => JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1') || 'null'));
const switchRole = async role => {await page.locator('#preview-role').selectOption(role); await page.waitForLoadState('domcontentloaded'); await settle(); assert.equal(await page.evaluate(() => SalesRuntime.app.globalData.session.role), role);};
const nav = async route => {await page.locator(`#desktop-nav [data-path="${route}"], #quick-nav [data-path="${route}"]`).first().click(); await settle();};
const taskList = async tab => {await nav('pages/tasks/index'); if (tab) {await page.locator(`.task-tab[data-key="${tab}"]`).click(); await settle();} await page.locator('.sort-toolbar select').selectOption({label: '按创建时间倒序'}); await settle();};
const field = async (key, value) => {
  const index = await page.evaluate(key => SalesRuntime.current.data.fields.findIndex(f => f.key === key), key);
  await page.locator(`.field-row[data-index="${index}"]`).click();
  const kind = await page.evaluate(() => SalesRuntime.current.data.editorType);
  if (kind === 'select') await page.locator('.editor-sheet .option-item').filter({hasText: value}).first().click();
  else await page.locator('.editor-sheet ' + (kind === 'textarea' ? 'textarea' : 'input')).fill(value);
  await page.locator('.editor-sheet .editor-save').click();
};
const openCustomerEdit = async name => {
  await nav('pages/customers/index'); await page.locator('.map-search input').fill(name); await settle();
  await page.locator('.customer-card').filter({hasText: name}).click(); await settle();
  await page.locator('.manual-edit-entry').click(); await settle();
};
try {
  await page.addInitScript(() => {if (!sessionStorage.getItem('sales-web:preview-role')) sessionStorage.setItem('sales-web:preview-role', 'sales');});
  const entryUrl = new URL(base); entryUrl.searchParams.set('mode', 'preview');
  await page.goto(entryUrl.href);
  await page.waitForFunction(() => window.SalesRuntime?.current && window.SALES_MODE);
  assert.equal(await page.evaluate(() => SALES_MODE), 'preview', 'Business verification must use the explicit example workspace');
  await settle();
  await nav('pages/customer-create/index');
  const customerName = '【示例】Web建档维护逐操作验收';
  for (const [key, value] of Object.entries({customer_name: customerName, industry: '企业软件', level_code: 'Tier-2', lead_source: '市场活动', partner_name: '示例合作伙伴', contact_name: '示例联系人', contact_title: '示例采购总监', contact_role: '决策者'})) await field(key, value);
  await page.getByRole('button', {name: '创建客户', exact: true}).click(); await confirm(); await page.waitForTimeout(850);
  const createdCustomer = (await state()).customers.find(c => c.name === customerName); assert.ok(createdCustomer);
  await openCustomerEdit(customerName);
  assert.equal(await page.locator('input[data-key="contact_title"]').inputValue(), '示例采购总监');
  await page.locator('.form-card select').first().selectOption({label: '人工智能'});
  await page.locator('input[data-key="contact_name"]').fill('维护后的示例联系人');
  await page.locator('input[data-key="contact_title"]').fill('维护后的示例技术总监');
  await page.locator('input[data-key="partner_name"]').fill('维护后的示例伙伴');
  await page.getByRole('button', {name: /保存修改/}).click(); await page.waitForTimeout(850);
  await openCustomerEdit(customerName);
  assert.equal(await page.locator('input[data-key="contact_name"]').inputValue(), '维护后的示例联系人');
  assert.equal(await page.locator('input[data-key="contact_title"]').inputValue(), '维护后的示例技术总监');
  assert.equal(await page.locator('input[data-key="partner_name"]').inputValue(), '维护后的示例伙伴');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.form.industry), '人工智能');
  await page.screenshot({path: path.join(output, 'customer-edit-readback.png'), fullPage: true});
  note('customer-create-edit-pass', {customerId: createdCustomer.id, titleReadback: '维护后的示例技术总监', owner: createdCustomer.owner_name});
  // Company directory -> pending application, with ownership checked independently after the real clicks.
  await nav('pages/customers/index'); await page.locator('.map-claim-entry').click(); await settle();
  await page.locator('.claim-search input').fill('待认领企业'); await settle();
  await page.locator('.claim-card').filter({hasText: '【示例】待认领企业'}).click();
  await page.getByRole('button', {name: '提交认领申请', exact: true}).click();
  note('claim-confirmation', {text: await page.locator('.wx-modal-content').innerText()}); await confirm();
  assert.match(await page.locator('.claim-result').innerText(), /等待运营审批/);
  assert.match(await page.locator('.claim-owner').innerText(), /申请待审批/);
  let saved = await state(); const claim = saved.claims.find(c => c.status === 'pending'); assert.ok(claim);
  assert.equal(saved.customers.find(c => c.id === claim.customer_id).owner_id, null);
  await page.screenshot({path: path.join(output, 'customer-claim-pending.png'), fullPage: true});
  note('claim-pending-pass', {customerId: claim.customer_id, claimId: claim.id, status: claim.status, ownerUnchanged: true});

  // Manager creates manually -> receiving role switches using the visible account selector -> accepts -> completes.
  await switchRole('supervisor'); await nav('pages/management-task-create/index');
  const title = '【示例】Web逐操作验收：整理试点清单并确认负责人';
  await page.locator('.description-card textarea').fill(title);
  await page.locator('.assignee-picker select').selectOption({index: 1}); await settle();
  const selected = await page.evaluate(() => SalesRuntime.current.data.selectedMember);
  assert.equal(selected.account_code, 'PREVIEW_SALES');
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  await page.locator('input[type="date"]').fill(tomorrow); await page.locator('input[type="time"]').fill('17:00');
  await page.getByRole('button', {name: /发送任务/}).click(); await confirm(); await page.waitForTimeout(850);
  saved = await state(); const task = saved.tasks.find(t => t.description === title); assert.ok(task); assert.equal(task.status, 'pending_confirm');
  note('task-created-pass', {taskId: task.id, owner: task.owner_name, due: task.due_at});
  await switchRole('sales'); await taskList();
  await page.locator('.task-title').filter({hasText: title}).click(); await settle();
  assert.match(await page.locator('.hero-title').innerText(), /整理试点清单/);
  await page.getByRole('button', {name: '接受任务', exact: true}).click(); await confirm();
  assert.equal((await state()).tasks.find(t => t.id === task.id).status, 'pending_execution');
  await page.locator('.completion-card textarea').fill('【示例验收】已整理清单并经负责人确认');
  await page.getByRole('button', {name: /标记为已完成/}).click(); await confirm(); await page.waitForTimeout(850);
  // Reload through a visible navigation and open completed item, independently of the success toast.
  await nav('pages/tasks/index'); await page.locator('[data-key="completed"]').first().click(); await settle();
  await page.locator('.task-title').filter({hasText: title}).click(); await settle();
  const completed = (await state()).tasks.find(t => t.id === task.id);
  assert.equal(completed.status, 'completed'); assert.equal(completed.version_no, 3); assert.equal(completed.events.length, 2);
  assert.match(await page.locator('#page-root').innerText(), /已整理清单并经负责人确认/);
  await page.screenshot({path: path.join(output, 'task-completed-readback.png'), fullPage: true});
  note('task-completed-pass', {taskId: task.id, version: completed.version_no, events: completed.events.map(e => e.event_type), completionNote: completed.completion_note});
  await switchRole('supervisor'); await nav('pages/tasks/index'); await page.locator('[data-key="completed"]').first().click(); await settle();
  await page.locator('.task-title').filter({hasText: title}).click(); await settle();
  assert.match(await page.locator('#page-root').innerText(), /已整理清单并经负责人确认/);
  assert.ok((await state()).notifications.some(n => n.object_id === task.id && n.template_code === 'task_completed'));
  await page.screenshot({path: path.join(output, 'task-creator-readback.png'), fullPage: true});
  note('task-creator-readback-pass', {taskId: task.id, creatorRole: 'supervisor'});
  assert.deepEqual(errors, []); note('all-pass', {pageErrors: errors, scope: 'local preview only; operations approval and live backend not connected'});
} catch (e) {note('failed', {error: e.message}); await page.screenshot({path: path.join(output, 'customer-task-failure.png'), fullPage: true}); throw e;}
finally {fs.writeFileSync(path.join(output, 'customer-task-results.json'), JSON.stringify({base, checkedAt: new Date().toISOString(), records, errors}, null, 2)); await browser.close();}
