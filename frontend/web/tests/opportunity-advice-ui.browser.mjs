// Compile the actual React detail UI into an in-memory browser fixture.
// No listener, API transport, model request or shared QA browser is used.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser, script;
before(async () => {
  const result = await build({absWorkingDir: fileURLToPath(new URL('../', import.meta.url)), bundle: true, write: false, format: 'iife', platform: 'browser', loader: {'.css': 'empty'}, define: {'process.env.NODE_ENV': '"production"'}, stdin: {resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'jsx', contents: `
    import React from 'react'; import {createRoot} from 'react-dom/client'; import OpportunityDetail from './department-ui/OpportunityDetail.jsx';
    const root=createRoot(document.getElementById('root'));window.calls=[];window.adviceEntries=OpportunityDetail.subcomponents;
    window.renderAdvice=(advice,readOnly=false,requestable=true)=>root.render(<OpportunityDetail page={{selectAllComponents:()=>[]}} data={{readOnly,opportunityId:'op-synthetic',canReadOpportunityAdvice:true,canOpportunityAdvice:requestable,opportunityTab:'overview',opportunityTabs:[{key:'overview',label:'商机概览'}],opportunityAdvice:{overview:advice},adviceExpanded:{overview:true},detailSummary:{loaded:true},opportunity:{id:'op-synthetic',name:'合成商机',customerName:'合成客户'}}} invoke={(name,payload)=>window.calls.push({name,payload})} invokeOn={()=>{throw Error('Unexpected advice decision')}}/>);
  `}});
  script = result.outputFiles[0].text;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close();});
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  const page = await context.newPage(), unexpected = [], errors = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.href === 'https://advice-ui.test/') return route.fulfill({contentType: 'text/html', body: '<div id="root"></div><script src="/ui.js"></script>'});
    if (url.href === 'https://advice-ui.test/ui.js') return route.fulfill({contentType: 'text/javascript', body: script});
    unexpected.push(url.href); return route.abort();
  });
  page.on('pageerror', error => errors.push(error.message));
  try {await page.goto('https://advice-ui.test/'); await page.waitForFunction(() => window.renderAdvice); await run(page); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);}
  finally {await context.close();}
}

test('business readonly retains explicit AI generation but never enables advice decisions', () => fixture(async page => {
  await page.evaluate(() => renderAdvice({status: 'idle'}, true));
  await page.getByRole('button', {name: '生成建议', exact: true}).waitFor();
  assert.deepEqual(await page.evaluate(() => calls), []);
  await page.evaluate(() => renderAdvice({id: 'analysis-known', status: 'queued'}, true));
  await page.getByRole('button', {name: '刷新已有建议', exact: true}).click();
  assert.deepEqual(await page.evaluate(() => calls), [{name: 'loadOpportunityAdvice', payload: {}}]);
  assert.equal(await page.getByRole('button', {name: /生成建议/}).count(), 0);
  assert.equal(await page.getByText('AI 生成中', {exact: true}).count(), 0);
  await page.evaluate(() => renderAdvice({id:'analysis-known',status:'ready',summary:'已有建议',rows:[{id:'s1',title:'只读建议正文',detail:'内容保持可读',decision:'pending'}]}, true));
  await page.getByText('只读建议正文', {exact: true}).waitFor();
  assert.equal(await page.getByText('正在准备建议操作…', {exact:true}).count(), 0);
  assert.equal(await page.getByRole('button', {name:'采纳并建待办', exact:true}).count(), 0);
  assert.deepEqual(await page.evaluate(() => adviceEntries(null,{readOnly:true,canOpportunityAdvice:true,adviceExpanded:{overview:true},opportunityTab:'overview',opportunityAdvice:{overview:{id:'a1',rows:[{id:'s1'}]}}})), []);
}));

test('generation is an explicit object-bound action; GET checking and POST submission are distinct', () => fixture(async page => {
  await page.evaluate(() => renderAdvice({status: 'idle'}));
  await page.getByRole('button', {name: '生成建议', exact: true}).click();
  assert.deepEqual(await page.evaluate(() => calls), [{name: 'requestOpportunityAdvice', payload: {dataset: {adviceAction: 'generate', opportunityId: 'op-synthetic', section: 'overview'}}}]);
  await page.evaluate(() => renderAdvice({id: 'analysis-known', status: 'ready', checking: true, summary: '原有建议', rows: []}));
  await page.getByText('正在读取已有建议，核验当前查看权限…', {exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: '读取中', exact: true}).isDisabled(), true);
  assert.equal(await page.getByRole('button', {name: /生成建议/}).count(), 0);
  await page.evaluate(() => renderAdvice({status: 'loading'}));
  await page.getByText('正在提交本次生成请求…', {exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: /生成建议/}).count(), 0);
  await page.evaluate(() => renderAdvice({id: 'analysis-known', status: 'ready', summary: '原有建议', rows: []}));
  await page.getByRole('button', {name: '重新生成建议', exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: '刷新已有建议', exact: true}).isEnabled(), true);
}));

 test('read permission without request permission shows existing advice and GET retry only', () => fixture(async page => {
  await page.evaluate(() => renderAdvice({status:'error',error:'读取未完成'},true,false));
  await page.getByRole('alert').waitFor();
  await page.getByRole('button',{name:'刷新已有建议',exact:true}).click();
  assert.deepEqual(await page.evaluate(()=>calls),[{name:'loadOpportunityAdvice',payload:{}}]);
  assert.equal(await page.getByRole('button',{name:/生成建议/}).count(),0);
}));
