// Actual React detail and styles, synthetic formatted view data, no external transport.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {build} from 'esbuild';
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const root = fileURLToPath(new URL('../', import.meta.url));
const base = 'https://change-history-ui.test';
let browser, assets;
before(async () => {
  const result = await build({absWorkingDir: root, bundle: true, write: false, outfile: 'ui.js', format: 'iife', platform: 'browser', define: {'process.env.NODE_ENV': '"production"'}, stdin: {resolveDir: root, loader: 'jsx', contents: `
    import React from 'react'; import {createRoot} from 'react-dom/client'; import {flushSync} from 'react-dom';
    import '@shandiant/tokens/css'; import '@shandiant/ui-react/style.css'; import './design-tokens.css';
    import './department-ui/shared.css'; import OpportunityDetail from './department-ui/OpportunityDetail.jsx';
    import './department-ui/pc/tokens.css';
    const root=createRoot(document.getElementById('root')); window.calls=[];
    window.renderHistory=(history,patch={})=>flushSync(()=>root.render(<OpportunityDetail
      page={{selectAllComponents:()=>[]}}
      data={{readOnly:true,canReadOpportunityAdvice:false,canOpportunityAdvice:false,opportunityId:'synthetic-op',
        opportunityTab:'opportunity',opportunityTabs:[{key:'overview',label:'商机概览'},{key:'opportunity',label:'商机进展'}],
        detailSummary:{loaded:true},progressEvents:[{key:'activity',title:'已归档拜访',date:'2026-10-20',detail:'已有动态仍保留'}],
        opportunity:{id:'synthetic-op',customer_id:'synthetic-customer',name:'合成商机',customerName:'合成客户',stageText:'方案交流',statusLabel:'推进中',probability:40,amount:'¥ 120,000',ownerLabel:'销售甲',teamLabel:'南区'},
        changeHistory:history,...patch}}
      invoke={(name,payload)=>calls.push({name,payload})} invokeOn={()=>{throw Error('Unexpected AI action')}}/>));
  `}});
  assets = Object.fromEntries(result.outputFiles.map(file => [file.path.split('/').pop(), file.text]));
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => browser?.close());
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1366, height: 900}, serviceWorkers: 'block'});
  const page = await context.newPage(), errors = [], unexpected = [];
  page.setDefaultTimeout(5000); page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.href === base + '/') return route.fulfill({contentType: 'text/html', body: '<link rel="stylesheet" href="/ui.css"><style>body{margin:0;padding:20px;background:var(--ui-background);font:14px/1.65 system-ui}*{box-sizing:border-box}#root{height:calc(100vh - 40px)}</style><div id="root" class="department-ui"></div><script src="/ui.js"></script>'});
    const asset = assets[url.pathname.slice(1)];
    if (url.origin === base && asset) return route.fulfill({contentType: url.pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: asset});
    unexpected.push(url.href); return route.abort();
  });
  try {await page.goto(base); await page.waitForFunction(() => window.renderHistory); await run(page); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);}
  finally {await context.close();}
}
const value = name => ({rows: [{name}], emptyLabel: '', status: 'recorded', truncated: false});
const item = (id = 'saved-2') => ({
  id, actor: '销售甲', at: '2026-10-20 16:02', versionLabel: '商机版本 12', reason: '客户确认预算',
  changes: [
    {key: 'name', label: '商机名称', before: value('旧商机'), after: value('更新后的商机')},
    {key: 'amount', label: '商机金额', before: value('¥ 0.00'), after: value('¥ 120,000.00')},
    {key: 'owner', label: '负责人', before: {rows: [{name: '历史名称未记录', id: 'person-7', missingName: true, idLabel: '人员ID'}]}, after: {rows: [{name: '销售乙', id: 'person-8', missingName: false}]}},
    {key: 'intent', label: '意向产品线', before: {rows: [], emptyLabel: '未填写'}, after: {rows: [{name: '产品甲'}, {name: '产品乙'}]}},
  ], unavailable: false,
});
const loaded = (items = [item()], patch = {}) => ({items, loaded: true, loading: false, hasMore: false, ...patch});
const render = (page, history, patch = {}) => page.evaluate(({history, patch}) => renderHistory(history, patch), {history, patch});

test('history precedes stage, preserves server order, formatted values and existing activity without AI permission', () => fixture(async page => {
  await render(page, loaded([item(), {...item('saved-1'), versionLabel: '商机版本 11', changes: []}]));
  const history = page.getByRole('region', {name: '变更历史'});
  assert.equal(await history.isVisible(), true);
  assert.deepEqual(await history.locator('article').allTextContents().then(rows => rows.map(s => s.match(/商机版本 \d+/)[0])), ['商机版本 12', '商机版本 11']);
  const amount = history.getByRole('row').filter({has: page.getByRole('rowheader', {name: '商机金额', exact: true})});
  assert.deepEqual(await amount.getByRole('cell').allTextContents(), ['变更前¥ 0.00', '变更后¥ 120,000.00']);
  assert.equal(await history.getByText('人员ID：person-7', {exact: true}).count(), 1);
  assert.doesNotMatch(await history.innerText(), /person-8|\[object Object\]|"rows"/);
  assert.equal(await page.getByText('已有动态仍保留', {exact: true}).count(), 1);
  assert.equal(await page.getByRole('button', {name: /生成建议/}).count(), 0);
  assert.equal(await page.evaluate(() => Boolean(document.querySelector('.ds-och').compareDocumentPosition(document.querySelector('.ds-od-stage')) & Node.DOCUMENT_POSITION_FOLLOWING)), true);
  assert.deepEqual(await page.evaluate(() => calls), []);
  await page.getByRole('button', {name: '刷新历史', exact: true}).click();
  assert.deepEqual(await page.evaluate(() => calls), [{name: 'refreshChangeHistory', payload: {}}]);
}));

test('initial, loading, empty and incomplete states are explicit and do not claim missing data is zero', () => fixture(async page => {
  await render(page, {});
  await page.getByRole('button', {name: '读取变更历史', exact: true}).click();
  assert.deepEqual(await page.evaluate(() => calls), [{name: 'loadChangeHistory', payload: {}}]);
  await render(page, {loading: true});
  assert.equal(await page.getByRole('status').innerText(), '正在加载变更历史…');
  assert.equal(await page.getByRole('button', {name: /读取变更历史/}).count(), 0);
  await render(page, loaded([], {coverageNote: '历史版本未保存字段前后值。'}));
  assert.equal(await page.getByText('暂无可展示的变更历史', {exact: true}).count(), 1);
  assert.equal(await page.getByText('历史版本未保存字段前后值。', {exact: true}).count(), 1);
  await render(page, loaded([{id: 'partial', actor: '操作人未记录', at: '时间未记录', versionLabel: '版本未记录', changes: [], unavailable: true}]));
  assert.equal(await page.getByText('本条历史没有可展示的字段明细。', {exact: true}).count(), 1);
  assert.equal(await page.getByRole('region', {name: '变更历史'}).getByRole('table').count(), 0);
}));

test('partial, truncated and restricted values remain distinct; row details and actor provenance are visible', () => fixture(async page => {
  const row = {...item(), actor: '销售甲（当前姓名）', partial: true, unavailableFieldCount: 2, changes: [
    {key: 'people', label: '协作人', before: {rows: [], status: 'unavailable', emptyLabel: '历史名单未记录'}, after: {
      rows: [{name: '销售乙', detail: '协作角色：项目支持\n所属团队：南区'}], status: 'recorded', emptyLabel: '', truncated: true}},
    {key: 'actual', label: '运营实绩', before: {status: 'restricted'}, after: {status: 'restricted', emptyLabel: '无权查看此字段'}},
  ]};
  await render(page, loaded([row, {id: 'unknown-actor', actor: '操作人姓名未记录（用户 ID：person-9）', changes: [], unavailable: true}]));
  const history = page.getByRole('region', {name: '变更历史'});
  assert.match(await history.innerText(), /销售甲（当前姓名）/);
  assert.match(await history.innerText(), /用户 ID：person-9/);
  assert.equal(await history.getByText('协作角色：项目支持\n所属团队：南区', {exact: true}).count(), 1);
  assert.equal(await history.getByText('此历史值较长，已显示部分内容。', {exact: true}).count(), 1);
  assert.equal(await history.getByText('2 个字段信息不完整，仅展示已保存的内容。', {exact: true}).count(), 1);
  assert.equal(await history.getByText('无权查看此字段', {exact: true}).count(), 2);
  assert.equal(await history.getByText('历史名单未记录', {exact: true}).count(), 1);
}));

test('pagination error retains events; retry and conflict reload invoke distinct shared handlers', () => fixture(async page => {
  await render(page, loaded(undefined, {hasMore: true}));
  await page.getByRole('button', {name: '加载更早变更', exact: true}).click();
  await render(page, loaded(undefined, {loading: true, hasMore: true}));
  assert.equal(await page.getByRole('button', {name: '刷新历史', exact: true}).isDisabled(), true);
  assert.equal(await page.getByRole('button', {name: '加载更早变更', exact: true}).count(), 0);
  await render(page, loaded(undefined, {error: '更早记录读取失败', hasMore: true}));
  assert.equal(await page.getByText('更新后的商机', {exact: true}).count(), 1);
  await page.getByRole('button', {name: '重试读取历史', exact: true}).click();
  await render(page, loaded(undefined, {conflict: true, hasMore: true}));
  assert.equal(await page.getByRole('alert').innerText(), '变更历史已有更新，请重新读取。');
  assert.equal(await page.getByRole('button', {name: '加载更早变更', exact: true}).count(), 0);
  await page.getByRole('button', {name: '重新读取历史', exact: true}).click();
  assert.deepEqual(await page.evaluate(() => calls.map(row => row.name)), ['moreChangeHistory', 'retryChangeHistory', 'refreshChangeHistory']);
}));

test('long text, identifiers and field values remain fully readable at desktop and narrow widths', () => fixture(async page => {
  const long = '连续长名称'.repeat(55) + 'END', identifier = 'unbroken'.repeat(35);
  const row = item(); row.reason = long; row.changes.push({key: 'long', label: '后续计划', before: value('第一行\n第二行'), after: {rows: [{name: long, id: identifier, missingName: true, idLabel: '人员ID'}]}});
  await render(page, loaded([row], {coverageNote: '早期历史可能缺少字段快照。'}));
  for (const width of [1366, 1024, 390]) {
    await page.setViewportSize({width, height: 900});
    const measurements = await page.locator('.ds-och').evaluate(el => ({
      width: el.clientWidth, scroll: el.scrollWidth,
      overflow: [...el.querySelectorAll('td,th,p,li')].filter(node => node.scrollWidth > node.clientWidth + 1).map(node => node.tagName),
      hidden: [...el.querySelectorAll('td,p')].some(node => ['hidden', 'clip'].includes(getComputedStyle(node).overflowY)),
    }));
    assert.ok(measurements.scroll <= measurements.width + 1, JSON.stringify({width, measurements}));
    assert.deepEqual(measurements.overflow, [], `width ${width}`); assert.equal(measurements.hidden, false);
    assert.ok((await page.locator('.ds-och-fields').innerText()).includes(long));
  }
  if (process.env.SALES_HISTORY_SCREENSHOTS) {
    fs.mkdirSync(process.env.SALES_HISTORY_SCREENSHOTS, {recursive: true});
    await page.screenshot({path: `${process.env.SALES_HISTORY_SCREENSHOTS}/history-narrow.png`, fullPage: true});
    await page.setViewportSize({width: 1366, height: 900});
    await page.screenshot({path: `${process.env.SALES_HISTORY_SCREENSHOTS}/history-desktop.png`});
  }
}));
