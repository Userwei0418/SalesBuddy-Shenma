/** Visit archive/advice UI: synthetic preview only; real API and local credentials are disabled. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const customerId = '00000010-0000-4000-8000-000000000001';
const opportunityId = '00000011-0000-4000-8000-000000000001';
const adviceId = 'synthetic-visit-advice', suggestionId = 'synthetic-visit-suggestion';
const transcript = '沟通内容：双方核对试点验收清单，客户希望补充数据样本范围。\n下一步计划：明天由我整理验收清单并发送给客户。\n跟进日期：2026-09-20\n对接人：合成UI联系人';
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: '', localLogin: null});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
const paint = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));

async function fixture(run, config = {}) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  const forbidden = [], errors = [];
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), config.role || 'sales');
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {
      forbidden.push(url.pathname); return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/index/index' && SalesRuntime.app.globalData.session);
    await page.evaluate(({config, customerId, opportunityId, adviceId, suggestionId}) => {
      const original = SalesPreview;
      window.visitAdviceProbe = {requests: [], mode: config.mode || 'ready', held: null, taskIds: null, advice: {
        id: adviceId, status: 'succeeded', stale: false, subject_kind: 'visit', subject_id: '', section: 'tasks',
        customer_id: customerId, opportunity_id: opportunityId,
        summary: '合成拜访建议，仅验证界面与既有处理流程。', suggestions: [{
          id: suggestionId, title: '补充合成验收清单', evidence: '合成客户提出补充清单。',
          action: '明天整理合成验收清单并提交核对。', decision: config.adopted ? 'adopted' : 'pending', version_no: 1,
          ...(config.adopted ? {task_id: '00000015-0000-4000-8000-000000000001'} : {}),
        }],
      }};
      if (config.suggestionCount) visitAdviceProbe.advice.suggestions = Array.from({length: config.suggestionCount}, (_, index) => ({
        ...visitAdviceProbe.advice.suggestions[0], id: `${suggestionId}-${index + 1}`, title: `补充合成验收清单 ${index + 1}`,
      }));
      window.SalesPreview = {...original, request(options = {}) {
        const path = new URL(options.url, location.href).pathname.replace(/^\/api\/v1/, '');
        const method = (options.method || 'GET').toUpperCase();
        visitAdviceProbe.requests.push({path, method, data: structuredClone(options.data || {})});
        const respond = (data, statusCode = 200) => {
          queueMicrotask(() => {const response = {statusCode, data: structuredClone(data)}; options.success?.(response); options.complete?.(response);});
          return {abort() {}};
        };
        if (path === '/advice' && method === 'POST') {
          visitAdviceProbe.advice.subject_id = options.data.subject_id;
          visitAdviceProbe.advice.opportunity_id = SalesRuntime.current.data.opportunityId || null;
          if (visitAdviceProbe.mode === 'loading') {visitAdviceProbe.held = () => respond(visitAdviceProbe.advice); return {abort() {}};}
          if (visitAdviceProbe.mode === 'error') return respond({detail: '合成建议暂时读取失败'}, 503);
          if (visitAdviceProbe.mode === 'empty') return respond({...visitAdviceProbe.advice, suggestions: []});
          return respond(visitAdviceProbe.advice);
        }
        if (path === `/advice/${adviceId}` && method === 'GET') return respond(visitAdviceProbe.advice);
        if (path === `/advice/suggestions/${suggestionId}/decision` && method === 'POST') {
          const suggestion = visitAdviceProbe.advice.suggestions[0];
          Object.assign(suggestion, {decision: options.data.decision, decision_note: options.data.note, version_no: suggestion.version_no + 1});
          if (options.data.decision === 'adopted') {
            const task = {...options.data.task, id: '90000015-0000-4000-8000-000000000001', status: 'pending_confirm', version_no: 1, data_kind: 'synthetic-test'};
            suggestion.task_id = task.id;
            return respond({suggestion, task});
          }
          return respond(suggestion);
        }
        return original.request(options);
      }};
    }, {config, customerId, opportunityId, adviceId, suggestionId});
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(forbidden, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
    assert.deepEqual(await taskWrites(page), [], 'reviewing or handling advice must not silently create tasks');
    assert.equal(await page.evaluate(() => JSON.stringify(JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).tasks.map(row => row.id)) === JSON.stringify(visitAdviceProbe.taskIds)), true);
  } catch (error) {
    console.error('VISIT_ADVICE_DIAGNOSTIC ' + JSON.stringify(await page.evaluate(() => ({
      route: SalesRuntime.current?.route, archived: SalesRuntime.current?.data?.archived,
      opportunityId: SalesRuntime.current?.data?.opportunityId,
      adviceBusy: SalesRuntime.current?.data?.adviceBusy, adviceError: SalesRuntime.current?.data?.adviceError,
      advice: SalesRuntime.current?.data?.advice, requests: visitAdviceProbe?.requests,
      body: document.body.innerText.slice(-6000),
    })).catch(() => ({}))));
    throw error;
  } finally {await context.close();}
}

async function archiveVisit(page, withOpportunity = false, fde = false) {
  await page.evaluate(() => SalesRuntime.route('/pages/visit-entry/index'));
  await page.locator('.customer-result[data-handler="chooseCustomer"]').filter({hasText: '星河制造'}).click();
  if (fde) {
    await page.locator('.fde-choice-select').first().click();
    await page.waitForFunction(() => SalesRuntime.current.data.fdeOpportunityVerified === true);
  }
  await page.locator('textarea.note-input').fill(transcript);
  await page.locator('button[data-handler="submitTranscript"]').click();
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/visit-confirm/index' && SalesRuntime.current.data.values.contact_name === '合成UI联系人');
  await page.evaluate(() => {visitAdviceProbe.taskIds = JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).tasks.map(row => row.id);});
  if (fde) await page.waitForFunction(() => SalesRuntime.current.data.fdeOpportunityVerified === true);
  else if (withOpportunity) {
    await page.locator('[data-handler="toggleOpportunityPicker"]').click();
    await page.locator(`#web-select-dialog .option[data-value="${opportunityId}"]`).click();
    await page.waitForFunction(id => SalesRuntime.current.data.opportunityId === id && !SalesRuntime.current.data.opportunityLoading, opportunityId);
  }
  await page.locator('[data-handler="review"]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.flowStep === 'result' && SalesRuntime.current.data.canSubmit === true);
  await page.locator('[data-handler="archive"]').click();
  await page.locator('.wx-modal-mask').getByRole('button', {name: '确认归档', exact: true}).click();
  await page.waitForFunction(() => SalesRuntime.current.data.archived === true);
  await paint(page);
  assert.deepEqual(await decisions(page), [], 'archiving must not decide on advice without user confirmation');
}
const queries = page => page.evaluate(() => visitAdviceProbe.requests.filter(row => row.path === '/advice' && row.method === 'POST'));
const taskWrites = page => page.evaluate(() => visitAdviceProbe.requests.filter(row => row.method === 'POST' && /\/tasks(?:$|\/)/.test(row.path)));
const decisions = page => page.evaluate(() => visitAdviceProbe.requests.filter(row => row.method === 'POST' && /\/advice\/suggestions\/[^/]+\/decision$/.test(row.path)));
const panelSelector = '.web-visit-advice-panel';

if (process.env.VISIT_ADVICE_BASELINE === '1') {
  test('baseline: unlinked visit archives with next action but no advice entry or request', () => fixture(async page => {
    await archiveVisit(page);
    assert.match(await page.locator('.success-next').innerText(), /下一步行动/);
    assert.equal(await page.locator('[data-handler="openAdvice"]').count(), 0);
    assert.equal(await page.locator('.advice-sheet').count(), 0);
    assert.deepEqual(await queries(page), []); assert.deepEqual(await taskWrites(page), []);
  }));
  test('baseline: linked visit archives and queries advice exactly once', () => fixture(async page => {
    await archiveVisit(page, true);
    await page.locator('.advice-item').waitFor();
    assert.equal((await queries(page)).length, 1);
    assert.equal(await page.locator('[data-handler="openAdvice"]').count(), 1);
    assert.deepEqual(await taskWrites(page), []);
  }));
} else {
  test('unlinked archive always shows advice explanation and requires an explicit request', () => fixture(async page => {
    await archiveVisit(page);
    const panel = page.locator(panelSelector);
    await panel.waitFor({state: 'visible'});
    assert.match(await panel.innerText(), /尚未获取本次待办建议/);
    assert.match(await page.locator('.success-next').innerText(), /明天由我整理验收清单/);
    assert.deepEqual(await queries(page), []);
    await panel.locator('[data-handler="loadAdvice"]').click();
    await panel.locator('.advice-item').waitFor();
    const [query] = await queries(page), visitId = await page.evaluate(() => SalesRuntime.current.data.visitId);
    assert.equal((await queries(page)).length, 1);
    assert.deepEqual(query.data, {subject_kind: 'visit', subject_id: visitId, section: 'tasks', retry: false});
  }));

  test('linked archive keeps original single advice request and a visible desktop side panel', () => fixture(async page => {
    await archiveVisit(page, true);
    await page.locator(`${panelSelector} .advice-item`).waitFor();
    assert.equal((await queries(page)).length, 1);
    assert.equal(await page.locator('.web-visit-archive-workspace .success-card').count(), 1);
    assert.equal(await page.locator('.advice-mask').count(), 0);
    const left = await page.locator('.success-card').boundingBox(), right = await page.locator(panelSelector).boundingBox();
    assert.ok(right.x >= left.x + left.width - 1, 'desktop advice must sit beside the archive receipt');
    assert.ok(Math.abs(left.y - right.y) < 10, 'both columns start together');
    assert.ok(right.x + right.width <= 1440, 'panel fits in the desktop viewport');
    await page.evaluate(() => SalesRuntime.current.setData({showAdvice: false}));
    await paint(page); await page.locator(panelSelector).waitFor({state: 'visible'});
    assert.equal((await queries(page)).length, 1, 'showing a persistent panel must not query again');
    await page.setViewportSize({width: 1024, height: 900}); await paint(page);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal((await queries(page)).length, 1);
  }));

  test('loading remains inline and repeated calls cannot duplicate an in-flight advice request', () => fixture(async page => {
    await archiveVisit(page, true);
    await page.locator(panelSelector).getByText('正在根据本次拜访整理建议…', {exact: true}).waitFor();
    assert.equal((await queries(page)).length, 1);
    assert.equal(await page.locator('.success-card').isVisible(), true);
    await page.evaluate(() => SalesRuntime.current.loadAdvice());
    assert.equal((await queries(page)).length, 1);
    await page.evaluate(() => {visitAdviceProbe.mode = 'ready'; visitAdviceProbe.held();});
    await page.locator(`${panelSelector} .advice-item`).waitFor();
    assert.equal((await queries(page)).length, 1);
  }, {mode: 'loading'}));

  test('empty advice is explained without presenting fabricated actions', () => fixture(async page => {
    await archiveVisit(page, true);
    await page.locator(panelSelector).getByText('本次没有需要补充的待办建议', {exact: true}).waitFor();
    assert.equal(await page.locator(`${panelSelector} .advice-item`).count(), 0);
    assert.equal(await page.locator(`${panelSelector} [data-handler="adopt"]`).count(), 0);
    assert.equal((await queries(page)).length, 1);
  }, {mode: 'empty'}));

  test('failed advice preserves the archive and retries through the original retry contract', () => fixture(async page => {
    await archiveVisit(page, true);
    await page.locator(panelSelector).getByText('合成建议暂时读取失败', {exact: false}).waitFor();
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.archived), true);
    assert.equal((await queries(page)).length, 1);
    await page.evaluate(() => {visitAdviceProbe.mode = 'ready';});
    await page.locator(panelSelector).getByRole('button', {name: '重新获取建议', exact: true}).click();
    await page.locator(`${panelSelector} .advice-item`).waitFor();
    assert.deepEqual((await queries(page)).map(row => row.data.retry), [false, true]);
  }, {mode: 'error'}));

  test('adopting opens the original task form with advice and suggestion identities without saving a task', () => fixture(async page => {
    await archiveVisit(page, true);
    await page.locator(panelSelector).getByRole('button', {name: '采纳并建待办', exact: true}).click();
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/management-task-create/index' && !!SalesRuntime.current.data.adviceSource);
    const state = await page.evaluate(() => ({url: SalesRuntime.current._url, data: SalesRuntime.current.data}));
    const query = new URL(state.url, base).searchParams;
    assert.equal(query.get('adviceId'), adviceId); assert.equal(query.get('suggestionId'), suggestionId);
    assert.equal(state.data.adviceSource.id, suggestionId); assert.equal(state.data.adviceSource.version, 1);
    assert.equal(state.data.customerId, customerId); assert.equal(state.data.opportunityId, opportunityId);
    assert.equal(state.data.description, '明天整理合成验收清单并提交核对。');
    assert.deepEqual(await decisions(page), []);
  }));

  test('already adopted advice opens its existing task through the original component', () => fixture(async page => {
    await archiveVisit(page, true);
    const taskId = await page.evaluate(() => visitAdviceProbe.advice.suggestions[0].task_id);
    await page.locator(panelSelector).getByRole('button', {name: '已采纳 · 查看待办 ›', exact: true}).click();
    await page.waitForFunction(id => SalesRuntime.current.route === 'pages/task-detail/index' && SalesRuntime.current.data.task?.id === id, taskId);
    assert.equal(new URL(await page.evaluate(() => SalesRuntime.current._url), base).searchParams.get('id'), taskId);
    assert.deepEqual(await decisions(page), []);
  }, {adopted: true}));

  test('confirmed task form sends the original adopted decision with version and task payload', () => fixture(async page => {
    await archiveVisit(page, true);
    await page.locator(panelSelector).getByRole('button', {name: '采纳并建待办', exact: true}).click();
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/management-task-create/index' && SalesRuntime.current.data.linkVerified && !SalesRuntime.current.data.recipientLoading);
    await page.locator('.assignee-picker select').selectOption('0');
    await page.locator('button[data-date-mode="date"]').click();
    await page.locator('#web-date-dialog').getByRole('button', {name: '明天', exact: true}).click();
    const expected = await page.evaluate(() => ({account: SalesRuntime.current.data.selectedMember.account, due: new Date(SalesRuntime.current.getSelectedDueAt()).toISOString()}));
    await page.locator('[data-handler="submitTask"]').click();
    await page.locator('.wx-modal-mask').getByRole('button', {name: '确认下发', exact: true}).waitFor();
    assert.deepEqual(await decisions(page), []); assert.deepEqual(await taskWrites(page), []);
    await page.locator('.wx-modal-mask').getByRole('button', {name: '确认下发', exact: true}).click();
    await page.waitForFunction(() => SalesRuntime.wx.getStorageSync('lastManagementTaskCreated')?.id === '90000015-0000-4000-8000-000000000001');
    const [decision] = await decisions(page);
    assert.equal((await decisions(page)).length, 1);
    assert.deepEqual(decision.data, {decision: 'adopted', version_no: 1, task: {
      description: '明天整理合成验收清单并提交核对。', association_kind: 'customer',
      assignee_account_code: expected.account, target_position: null, due_at: expected.due,
      priority_code: 'medium', customer_id: customerId, opportunity_id: opportunityId,
    }});
    // A synthetic {task} response only verifies the original UI contract, not backend persistence.
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/visit-confirm/index');
    await page.locator(panelSelector).getByRole('button', {name: '已采纳 · 查看待办 ›', exact: true}).waitFor();
  }));

  test('no-task decision requires confirmation, preserves suggestion version, and refreshes the original advice', () => fixture(async page => {
    await archiveVisit(page, true);
    const dismiss = page.locator(panelSelector).getByRole('button', {name: '无需待办', exact: true});
    await dismiss.click();
    await page.locator('.wx-modal-mask').getByText('这条建议无需待办？', {exact: true}).waitFor();
    assert.deepEqual(await decisions(page), []);
    await page.locator('.wx-modal-mask').getByRole('button', {name: '取消', exact: true}).click();
    assert.deepEqual(await decisions(page), []);
    await dismiss.click();
    await page.locator('.wx-modal-mask textarea').fill('合成验收已覆盖');
    await page.locator('.wx-modal-mask').getByRole('button', {name: '确认', exact: true}).click();
    await page.locator(panelSelector).getByText('已处理 · 无需待办 · 合成验收已覆盖', {exact: true}).waitFor();
    assert.deepEqual((await decisions(page)).map(row => row.data), [{decision: 'no_task', version_no: 1, note: '合成验收已覆盖'}]);
    assert.equal((await queries(page)).length, 1, 'decision refresh must read existing advice without regenerating it');
    assert.equal(await page.evaluate(() => visitAdviceProbe.requests.filter(row => row.method === 'GET' && row.path === '/advice/synthetic-visit-advice').length), 1);
  }));

  for (const role of ['fde', 'fde_lead']) test(`${role} archive keeps the existing permission boundary`, () => fixture(async page => {
    await archiveVisit(page, true, true);
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.isFde), true);
    assert.equal(await page.locator(panelSelector).count(), 0);
    assert.equal(await page.locator('[data-handler="adopt"], [data-handler="dismiss"], [data-handler="loadAdvice"]').count(), 0);
    await page.evaluate(() => {SalesRuntime.current.openAdvice(); return SalesRuntime.current.loadAdvice();});
    await paint(page);
    assert.deepEqual(await queries(page), []); assert.deepEqual(await decisions(page), []);
  }, {role}));

  if (process.env.VISIT_ADVICE_SCREENSHOT_DIR) test('three advice cards remain usable at desktop and narrow widths', () => fixture(async page => {
    await page.setViewportSize({width: 1366, height: 768});
    await archiveVisit(page, true);
    await page.locator(`${panelSelector} .advice-item`).last().waitFor();
    assert.equal(await page.locator(`${panelSelector} .advice-item`).count(), 3);
    assert.equal(await page.locator(`${panelSelector} button[data-handler="adopt"]`).count(), 3);
    assert.equal(await page.locator(`${panelSelector} button[data-handler="dismiss"]`).count(), 3);
    await page.screenshot({path: `${process.env.VISIT_ADVICE_SCREENSHOT_DIR}/visit-advice-desktop-1366.png`, fullPage: true});
    for (const width of [1366, 390]) {
      await page.setViewportSize({width, height: width === 390 ? 844 : 768}); await paint(page);
      const dimensions = await page.evaluate(() => ({viewport: innerWidth, page: document.documentElement.scrollWidth,
        panel: (() => {const el = document.querySelector('.web-visit-advice-panel'); return {client: el.clientWidth, scroll: el.scrollWidth};})()}));
      assert.ok(dimensions.page <= width, `page overflow at ${width}: ${JSON.stringify(dimensions)}`);
      assert.ok(dimensions.panel.scroll <= dimensions.panel.client + 1, `advice overflow at ${width}: ${JSON.stringify(dimensions)}`);
      await page.locator(`${panelSelector} button[data-handler="dismiss"]`).last().scrollIntoViewIfNeeded();
      assert.equal(await page.locator(`${panelSelector} button[data-handler="dismiss"]`).last().isVisible(), true);
      assert.equal(await page.locator(`${panelSelector} button[data-handler="dismiss"]`).last().evaluate(button => {
        const rect = button.getBoundingClientRect();
        return button.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
      }), true, `the last advice action remains clickable at ${width}`);
      if (width === 390) {
        await page.screenshot({path: `${process.env.VISIT_ADVICE_SCREENSHOT_DIR}/visit-advice-narrow-actions-390.png`});
        await page.evaluate(() => window.scrollTo(0, 0)); await paint(page);
        await page.screenshot({path: `${process.env.VISIT_ADVICE_SCREENSHOT_DIR}/visit-advice-narrow-390.png`});
      }
    }
  }, {suggestionCount: 3}));
}
