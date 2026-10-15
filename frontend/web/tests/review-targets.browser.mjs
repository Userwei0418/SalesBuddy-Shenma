import {before, after, test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '/Users/zhouwei5/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs');
const built = await build({stdin: {resolveDir: process.cwd(), loader: 'jsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';import ReviewTargets from './department-ui/review/targets/ReviewTargets.jsx';
window.actor={workspaceId:'workspace-a',userId:'user-a',permissionVersion:1};window.permissions=['target.read','target.submit','target.manage'];
window.SalesRuntime={app:{globalData:{get session(){return actor;}},can:p=>permissions.includes(p)},require:name=>({identity:s=>JSON.stringify(s),scopeFor:()=>({allowed:true,scopes:['workspace']})})};
window.calls=[];window.mode='pending';window.saved=[];
window.metric={code:'collection',name:'预测回款',target_kind:'collection',unit:'元',decimal_places:2,source_kind:'forecast',definition:'高信心季度预计回款',definition_version:1,version_no:1,active:true,goal_selectable:true};
window.client={targets:async params=>{calls.push({method:'GET',params});return {items:saved,total:saved.length,editable:permissions.includes('target.manage')||permissions.includes('target.submit')};},
metrics:async()=>({items:[metric]}), options:async()=>({people:[{id:'user-a',name:'本人',can_manage:true}],teams:[{id:'team-a',name:'南区',can_manage:true}],departments:[]}),
saveTarget:async body=>{calls.push({method:'POST',body});if(mode==='conflict')throw Object.assign(Error('stale'),{statusCode:409});
saved=[{row_key:'target-1',id:mode==='pending'?null:'target-1',scope_type:body.scope,team_id:body.team_id,team_name:'南区',period_type:body.period_type,period_start:body.anchor_date,kind:'collection',metric,editable:true,can_history:false,version_no:mode==='pending'?null:4,effective_amount:mode==='pending'?null:body.amount,state:mode==='pending'?'pending_new':'effective',pending_change:mode==='pending'?{request_id:'pending-1',proposed_amount:body.amount,applicant_name:'本人'}:null}];return {status:mode==='pending'?'pending':'effective',target:mode==='pending'?null:saved[0],items:mode==='pending'?[]:saved};},
createMetric:async()=>({id:'metric'}),updateMetric:async()=>({id:'metric'})};
const root=createRoot(document.getElementById('root'));window.left=0;window.editorRef=React.createRef();window.show=()=>root.render(<ReviewTargets ref={editorRef} client={client} onBack={()=>editorRef.current.leave(()=>{window.left++;})}/>);show();
`}, bundle: true, write: false, loader: {'.svg': 'dataurl'}, outdir: '.runtime/review-targets/browser', define: {'process.env.NODE_ENV': '"production"'}});
const js = built.outputFiles.find(file => file.path.endsWith('.js')).text;
const css = built.outputFiles.find(file => file.path.endsWith('.css')).text;
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1440, height: 900}}), page = await context.newPage(), errors = [];
  page.setDefaultTimeout(7000); page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => route.request().isNavigationRequest() ? route.fulfill({contentType: 'text/html', body: `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body><div id="root" style="padding:24px"></div><script>${js}</script></body></html>`}) : route.abort());
  try {await page.goto('https://review-targets.test/'); await page.getByText('当前周期还没有目标', {exact:true}).waitFor(); await run(page); assert.deepEqual(errors, []);} finally {await context.close();}
}
const posts = page => page.evaluate(() => calls.filter(call => call.method === 'POST'));
async function choose(page, name, label) {await page.getByRole('combobox', {name, exact: true}).click(); await page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)').getByText(label, {exact:true}).click();}
async function fill(page, amount = '123456.78') {
  await page.getByRole('button', {name:'新增目标',exact:true}).click();
  await choose(page, '目标对象', '南区'); await choose(page, '目标指标', '预测回款');
  await page.getByRole('textbox', {name:'目标值',exact:true}).fill(amount); await page.getByRole('textbox', {name:'调整原因',exact:true}).fill('与负责人确认季度目标');
}
test('new row is at top; cancel writes nothing', () => fixture(async page => {
  await fill(page); assert.equal(await page.locator('tbody tr').first().getAttribute('data-testid'), 'target-edit-row');
  if (process.env.REVIEW_TARGET_SCREENSHOT) await page.screenshot({path:process.env.REVIEW_TARGET_SCREENSHOT,fullPage:true});
  await page.getByRole('button',{name:'取消',exact:true}).click(); assert.equal(await page.getByTestId('target-edit-row').count(),0); assert.deepEqual(await posts(page),[]);
}));
test('save pending new target and refresh keeps it with no effective denominator', () => fixture(async page => {
  await fill(page,'9007199254740991.29'); await page.getByRole('button',{name:'保存目标',exact:true}).click();
  await page.getByText('新目标待审批',{exact:true}).waitFor(); assert.equal(await page.getByTestId('target-effective').innerText(),'未设目标');
  assert.equal(await page.getByTestId('target-pending').innerText(),'9,007,199,254,740,991.29');
  assert.equal((await posts(page))[0].body.amount,'9007199254740991.29'); assert.equal(Object.hasOwn((await posts(page))[0].body,'version_no'),false);
  await page.getByRole('button',{name:'刷新',exact:true}).click(); await page.getByText('新目标待审批',{exact:true}).waitFor(); assert.equal((await posts(page)).length,1);
}));
test('version conflict preserves draft and does not silently rebase or resubmit', () => fixture(async page => {
  await page.evaluate(()=>{mode='conflict';}); await fill(page,'88.29'); await page.getByRole('button',{name:'保存目标',exact:true}).click();
  await page.getByText(/目标已被更新，或已有同范围的待审申请/).waitFor();
  assert.equal(await page.getByRole('textbox',{name:'目标值',exact:true}).inputValue(),'88.29'); assert.equal(await page.getByRole('button',{name:'保存目标',exact:true}).isDisabled(),true);
  await page.getByRole('button',{name:'刷新核对',exact:true}).click(); assert.equal(await page.getByRole('textbox',{name:'目标值',exact:true}).inputValue(),'88.29'); assert.equal((await posts(page)).length,1);
}));
test('effective target editing passes the exact version and decimal string', () => fixture(async page => {
  await page.evaluate(()=>{mode='effective';});await fill(page,'100.99'); await page.getByRole('button',{name:'保存目标',exact:true}).click(); await page.getByText('生效中',{exact:true}).waitFor();
  await page.getByRole('button',{name:'编辑',exact:true}).click(); await page.getByRole('textbox',{name:'目标值',exact:true}).fill('101.01');await page.getByRole('textbox',{name:'调整原因',exact:true}).fill('调整计划');
  await page.getByRole('button',{name:'保存目标',exact:true}).click();await page.getByText('生效中',{exact:true}).waitFor();assert.equal((await posts(page))[1].body.version_no,4);assert.equal((await posts(page))[1].body.amount,'101.01');
}));
test('read-only viewer has no target mutation path and no dictionary maintenance', () => fixture(async page => {
  await page.evaluate(()=>{permissions=['target.read'];actor={...actor,permissionVersion:2};show();});
  await page.getByText('当前周期还没有目标',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'新增目标',exact:true}).isDisabled(),true);
  await page.getByRole('tab',{name:'指标字典',exact:true}).click();assert.equal(await page.getByRole('button',{name:'新增手工指标',exact:true}).count(),0);assert.deepEqual(await posts(page),[]);
}));

test('leaving target or dictionary edits asks before discarding; keep editing preserves input', () => fixture(async page => {
  await fill(page,'77.29');await page.getByRole('button',{name:'返回本期概览',exact:true}).click();await page.locator('.ant-modal-confirm-title').filter({hasText:'还有未保存的内容'}).waitFor();
  await page.getByRole('button',{name:'继续编辑',exact:true}).click();await page.getByRole('button',{name:'继续编辑',exact:true}).waitFor({state:'hidden'});assert.equal(await page.getByRole('textbox',{name:'目标值',exact:true}).inputValue(),'77.29');assert.equal(await page.evaluate(()=>left),0);
  await page.getByRole('button',{name:'取消',exact:true}).click();await page.getByRole('tab',{name:'指标字典',exact:true}).click();await page.getByRole('button',{name:'新增手工指标',exact:true}).click();
  await page.getByRole('textbox',{name:'名称',exact:true}).fill('活动报名');await page.waitForFunction(()=>editorRef.current.hasUnsavedChanges());await page.evaluate(()=>editorRef.current.leave(()=>{left++;}));await page.locator('.ant-modal-confirm-title').filter({hasText:'还有未保存的内容'}).waitFor();
  await page.getByRole('button',{name:'放弃并离开',exact:true}).click();assert.equal(await page.evaluate(()=>left),1);assert.deepEqual(await posts(page),[]);
}));
