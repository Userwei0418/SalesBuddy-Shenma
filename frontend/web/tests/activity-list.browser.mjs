/** Activity disclosures and asset strips: synthetic data, no real services. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: ''});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
async function fixture(run, width = 1366) {
  const context = await browser.newContext({viewport: {width, height: 768}, serviceWorkers: 'block'});
  const blocked = [], errors = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)/.test(url.pathname) || url.pathname === '/local-login') {
      blocked.push(url.pathname); return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/index/index' && !SalesRuntime.app._capabilityFlight && !SalesRuntime.current.notificationLoading);
    await run(page);
    assert.deepEqual(blocked, []); assert.deepEqual(errors, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
const event = id => ({id, from: 'agent', kind: 'data-card', time: '9月15日 10:00', card: {
  title: '收到任务', subtitle: '原始完整通知说明', tone: 'yellow',
  metrics: [{label: '任务状态', value: '待接受', action: 'pending'}, {label: '优先级', value: '中'}],
  rows: [{title: '跟进客户并核对验收记录', meta: '截止9月16日，原始完整任务备注', taskId: 'synthetic-task', tag: '待接受'},
    {title: '第二条完整信息', meta: '该项必须在展开后保留', visitId: 'synthetic-visit', customerId: 'synthetic-customer'}],
  footer: '原始业务口径说明', action: {code: 'open_task_detail', label: '查看任务详情', taskId: 'synthetic-task'}
}});
async function seed(page, messages) {
  await page.evaluate(messages => {
    SalesRuntime.current.stopNotificationPolling?.();
    SalesRuntime.current.setData({messages});
  }, messages);
  await page.waitForFunction(length => document.querySelectorAll('.business-card').length === length, messages.length);
}
async function painted(page) {await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}

for (const width of [1366, 676]) test(`${width}px activity list is compact and preserves all original handlers and fields`, () => fixture(async page => {
  const messages = Array.from({length: 8}, (_, i) => event('activity-' + i));
  await seed(page, messages);
  const row = page.locator('#chat-message-activity-0');
  assert.equal(await page.locator('.web-activity-detail[open]').count(), 0);
  assert.equal(await row.locator('.web-activity-body').isVisible(), false);
  assert.equal(await row.locator('>.message-stack>.business-card>.business-action').isVisible(), true);
  const heights = await page.locator('.web-activity-row').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
  assert.ok(heights.every(height => height <= 100), JSON.stringify(heights));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.messages), messages);
  await page.evaluate(() => {
    window.__activityActions = [];
    for (const name of ['handleMetricAction', 'handleCardRow', 'handleCardAction']) {
      SalesRuntime.current[name] = event => window.__activityActions.push({handler: name, data: event.currentTarget.dataset});
    }
  });
  await row.locator('summary').focus(); await page.keyboard.press('Enter');
  assert.equal(await row.locator('.web-activity-body').isVisible(), true);
  const original = await row.locator('.web-activity-body').innerText();
  for (const content of ['原始完整通知说明','第二条完整信息','该项必须在展开后保留','原始业务口径说明']) assert.ok(original.includes(content), content);
  await row.locator('.business-metrics [data-action="pending"]').click();
  await row.locator('.business-row[data-visit-id="synthetic-visit"]').click();
  await row.locator('>.message-stack>.business-card>.business-action').click();
  const calls = await page.evaluate(() => window.__activityActions);
  assert.deepEqual(calls.map(call => call.handler), ['handleMetricAction','handleCardRow','handleCardAction']);
  assert.equal(calls[0].data.action, 'pending');
  assert.equal(calls[1].data.visitId, 'synthetic-visit'); assert.equal(calls[1].data.customerId, 'synthetic-customer');
  assert.equal(calls[2].data.taskId, 'synthetic-task'); assert.equal(calls[2].data.action, 'open_task_detail');
  await row.locator('summary').focus(); await page.keyboard.press('Space');
  assert.equal(await row.locator('.web-activity-body').isVisible(), false);
}, width));

test('disclosure state follows the event key through refresh and is reset for removed rows or a new page', () => fixture(async page => {
  await seed(page, [event('a'), event('b')]);
  await page.locator('#chat-message-b summary').click();
  const updated = event('b'); updated.card.subtitle = '刷新后仍应可读的详情';
  await seed(page, [event('new'), updated, event('a')]); await painted(page);
  assert.equal(await page.locator('#chat-message-b details').getAttribute('open'), '');
  assert.equal(await page.locator('#chat-message-new details').getAttribute('open'), null);
  assert.ok((await page.locator('#chat-message-b .web-activity-body').innerText()).includes('刷新后仍应可读的详情'));
  await seed(page, [event('a')]); await seed(page, [event('a'), event('b')]);
  assert.equal(await page.locator('#chat-message-b details').getAttribute('open'), null);
  await page.locator('#chat-message-b summary').click();
  await page.evaluate(() => SalesRuntime.refresh());
  await page.waitForFunction(() => SalesRuntime.current?.data.messages?.length > 0);
  await seed(page, [event('a'), event('b')]);
  assert.equal(await page.locator('#chat-message-b details').getAttribute('open'), null);
}));

test('phone retains the complete original card and resizing changes only presentation', () => fixture(async page => {
  const messages = [event('phone')]; await seed(page, messages);
  await page.setViewportSize({width: 390, height: 844});
  await page.waitForFunction(() => !document.querySelector('.web-activity-row'));
  assert.equal(await page.locator('.business-subtitle').isVisible(), true);
  assert.equal(await page.locator('.business-row[data-visit-id="synthetic-visit"]').isVisible(), true);
  assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.messages), messages);
  await page.setViewportSize({width: 1366, height: 768});
  await page.locator('.web-activity-row').waitFor();
  assert.equal(await page.locator('.web-activity-detail[open]').count(), 0);
  assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.messages), messages);
}));

test('category and text filters operate on loaded receipts without changing source facts, counts or order',()=>fixture(async page=>{
 const claim=name=>({id:name,kind:'data-card',from:'agent',time:'9月16日 13:45',card:{eyebrow:'客户认领',title:'客户认领已通过',subtitle:name,tone:'green',metrics:[{label:'认领结果',value:'已通过'}],rows:[{title:'审批说明',meta:'已确认交接范围'}],action:{code:'open_assigned_customer',customerId:'synthetic-customer',label:'查看客户档案'}}});
 const messages=[claim('合成甲公司'),event('synthetic-task'),claim('合成乙公司')];await seed(page,messages);
 const metrics=await page.evaluate(()=>JSON.stringify(SalesRuntime.current.data.overviewMetrics));
 assert.equal(await page.locator('#chat-message-合成甲公司 .web-activity-title').innerText(),'合成甲公司');
 await page.locator('.web-activity-filter[data-key=customer]').click();await page.locator('.web-activity-filter.active[data-key=customer]').waitFor();
 assert.deepEqual(await page.locator('.chat-stream>.message-row').evaluateAll(n=>n.map(e=>e.id)),['chat-message-合成甲公司','chat-message-合成乙公司']);
 const search=page.locator('.web-activity-search input');await search.pressSequentially('合成乙公司');await painted(page);
 assert.equal(await search.inputValue(),'合成乙公司');assert.equal(await page.locator('.web-activity-row').count(),1);
 await page.locator('#chat-message-合成乙公司 summary').click();assert.ok((await page.locator('#chat-message-合成乙公司 .web-activity-body').innerText()).includes('已确认交接范围'));
 await search.fill('没有这样的动态');await page.getByText('没有匹配的动态',{exact:true}).waitFor();await page.getByRole('button',{name:'查看全部动态',exact:true}).click();await page.locator('.web-activity-filter.active[data-key=all]').waitFor();
 assert.deepEqual(await page.locator('.chat-stream>.message-row').evaluateAll(n=>n.map(e=>e.id)),messages.map(m=>'chat-message-'+m.id));
 assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.messages),messages);assert.equal(await page.evaluate(()=>JSON.stringify(SalesRuntime.current.data.overviewMetrics)),metrics);
 await page.locator('.web-activity-filter[data-key=task]').click();
 const newest=event('task-new');await page.evaluate(messages=>SalesRuntime.current.setData({messages}),[newest,...messages]);await painted(page);
 assert.equal(await page.locator('.web-activity-filter[data-key=task]').getAttribute('aria-pressed'),'true');
 assert.deepEqual(await page.locator('.chat-stream>.message-row').evaluateAll(n=>n.map(e=>e.id)),['chat-message-task-new','chat-message-synthetic-task']);
 assert.ok((await page.locator('.web-activity-count').innerText()).includes('已加载 4 条'));
}));

test('red yellow green and pending lights retain shared business judgments and explain them on expansion',()=>fixture(async page=>{
 const messages=await page.evaluate(()=>{
  const home=SalesRuntime.current,session=SalesRuntime.app.globalData.session;
  return ['green','yellow','red','pending'].map(color=>home.buildRemoteNotificationMessage({id:'light-'+color,template_code:'business_changed',title:'合成商机变化',body:'合成项目：验证变化摘要',created_at:'2026-09-16T05:45:00Z',payload:{opportunity_id:'synthetic-opp',customer_id:'synthetic-customer',change_review:color==='pending'?{status:'pending'}:{status:'completed',color,title:'合成变化 '+color,summary:'合成判断依据 '+color},changes:[{label:'商机阶段',before:'意向沟通',after:'方案沟通'}]}},session));
 });await seed(page,messages);
 for(const [color,tone,label] of [['green','green','向好'],['yellow','yellow','需关注'],['red','red','转差'],['pending','gray','待评估']]){
  const row=page.locator('#chat-message-remote_light-'+color),light=row.locator('.web-activity-light');
  assert.equal(await light.innerText(),label);assert.ok((await light.getAttribute('class')).includes('signal-'+tone));assert.equal(await light.locator('.web-activity-dot').count(),1);
  await row.locator('summary').click();assert.ok((await row.locator('.web-activity-body').innerText()).includes('意向沟通 → 方案沟通'));
  if(color!=='pending')assert.ok((await row.locator('.web-activity-body').innerText()).includes('合成判断依据 '+color));
 }
 const colors=await page.locator('.web-activity-light').evaluateAll(nodes=>nodes.map(el=>getComputedStyle(el).color));assert.equal(new Set(colors).size,4);
 assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.messages),messages);
 await page.setViewportSize({width:390,height:844});await page.waitForFunction(()=>!document.querySelector('.web-activity-row'));
 assert.deepEqual(await page.locator('.change-status').allTextContents(),['向好','需关注','转差','待评估']);
}));

test('customer asset strip retains period switching, units, long values and drilldown datasets', () => fixture(async page => {
  await page.evaluate(() => SalesRuntime.wx.reLaunch({url: '/pages/customers/index'}));
  await page.waitForFunction(() => SalesRuntime.current?.route === 'pages/customers/index' && !SalesRuntime.current.data.loading && !SalesRuntime.current.data.acvLoading && !SalesRuntime.current.data.assetLoading);
  await page.evaluate(() => SalesRuntime.current.setData({acvText: '9,999,999.9', recognizedText: '0', collectionText: '…'}));
  for (const width of [1366, 676, 601]) {
    await page.setViewportSize({width, height: 768}); await painted(page);
    const strip = await page.locator('.asset-overview').evaluate(el => ({h: el.getBoundingClientRect().height, overflow: el.scrollWidth > el.clientWidth}));
    assert.ok(strip.h < 150); assert.equal(strip.overflow, false);
    assert.equal(await page.locator('.asset-number').count(), 3);
    assert.ok((await page.locator('.asset-overview').innerText()).includes('ACV 为当前在推商机总额'));
  }
  for (const period of ['all', 'year']) {
    await page.locator(`.asset-period [data-period="${period}"]`).click();
    await page.waitForFunction(period => SalesRuntime.current.data.assetPeriod === period, period);
    await page.locator(`.asset-period [data-period="${period}"].selected`).waitFor();
    assert.ok((await page.locator(`.asset-period [data-period="${period}"]`).getAttribute('class')).includes('selected'));
  }
  await page.evaluate(() => {window.__assetActions = []; SalesRuntime.current.openAssets = event => window.__assetActions.push(event.currentTarget.dataset.kind);});
  await page.locator('.asset-metric[data-kind="recognized"]').click();
  await page.locator('.asset-metric[data-kind="collection"]').click();
  assert.deepEqual(await page.evaluate(() => window.__assetActions), ['recognized','collection']);
}));

test('attention view highlights only red/yellow business judgments and keeps long instructions out of headings',()=>fixture(async page=>{
 const long='【示例】评审会后联系采购负责人，核对采购流程及报价依据，汇总各部门意见并反馈试点验收要求，与客户共同确认下一阶段推进计划。';
 const task=event('long-task');task.card.title='任务已完成';task.card.rows[0].title=long;task.card.metrics[0].value='已完成';task.card.tone='green';
 const pending=event('pending-task'); // An orange task receipt is not a business-health warning.
 const lights=['red','yellow','green','gray'].map(tone=>({id:'focus-'+tone,from:'agent',kind:'data-card',time:'9月16日 12:00',card:{eyebrow:'业务动态',title:'合成变化通知',subtitle:'【示例】客户与项目 '+tone,tone,statusLabel:{red:'转差',yellow:'需关注',green:'向好',gray:'待评估'}[tone],metrics:[],rows:[{title:'商机阶段',meta:'意向沟通 → 方案沟通'},{title:'变化判断',meta:'合成依据 '+tone}],action:{label:'查看商机',code:'open_changed_business',opportunityId:'synthetic-opportunity'}}}));
 const messages=[lights[0],task,lights[1],pending,lights[2],lights[3]];await seed(page,messages);await painted(page);
 assert.equal(await page.locator('#chat-message-long-task .web-activity-title').innerText(),'任务已完成');
 assert.equal(await page.locator('#chat-message-long-task .web-activity-preview').innerText(),long);
 assert.equal(await page.locator('#chat-message-focus-yellow .web-activity-change').innerText(),'商机阶段：意向沟通 → 方案沟通');
 const colors=await page.locator('#chat-message-focus-red .web-activity-row,#chat-message-focus-yellow .web-activity-row,#chat-message-long-task .web-activity-row').evaluateAll(ns=>ns.map(n=>getComputedStyle(n).backgroundColor));assert.equal(new Set(colors).size,3);
 const position=await page.locator('#chat-message-focus-yellow .web-activity-summary').evaluate(el=>{const b=s=>el.querySelector(s).getBoundingClientRect();return {title:b('.web-activity-main').right,status:b('.web-activity-status').left,time:b('.web-activity-time').left,statusRight:b('.web-activity-status').right};});assert.ok(position.status>=position.title&&position.time>=position.statusRight);
 const metrics=await page.evaluate(()=>SalesRuntime.current.data.overviewMetrics);
 await page.locator('.web-activity-filter[data-key=attention]').click();await painted(page);
 assert.deepEqual(await page.locator('.chat-stream>.message-row').evaluateAll(ns=>ns.map(n=>n.id)),['chat-message-focus-red','chat-message-focus-yellow']);
 assert.equal(await page.locator('.web-activity-filter[data-key=attention]>span').innerText(),'2');
 await page.locator('.web-activity-search input').fill('合成依据 yellow');await painted(page);assert.equal(await page.locator('.web-activity-row').count(),1);
 await page.locator('#chat-message-focus-yellow summary').click();assert.match(await page.locator('#chat-message-focus-yellow .web-activity-body').innerText(),/合成依据 yellow/);
 assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.messages),messages);assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.overviewMetrics),metrics);
 await page.locator('.web-activity-search button').click();await painted(page);assert.equal(await page.locator('.web-activity-row').count(),6);
 await page.locator('#chat-message-long-task summary').click();assert.ok((await page.locator('#chat-message-long-task .web-activity-body').innerText()).includes(long));
}));
