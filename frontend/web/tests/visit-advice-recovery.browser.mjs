// Actual built Web runtime, imported Mini handlers and React detail together.
// All resources/transport are fulfilled in memory; no service is started.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const root = fileURLToPath(new URL('../', import.meta.url));
const staticRoot = path.resolve(root, process.env.SALES_WEB_DIST_DIR || 'dist');
const base = 'https://advice-runtime.test';
const customer = '00000010-0000-4000-8000-000000000001', opportunity = '00000011-0000-4000-8000-000000000001';
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});
const paint = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'}), errors = [], unexpected = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || url.pathname.startsWith('/api/')) {unexpected.push(url.pathname); return route.abort();}
    if (url.pathname === '/web-capabilities') return route.fulfill({json: {previewOnly: true}});
    const file = path.resolve(staticRoot, url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).slice(1));
    if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
    const contentType = ({'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png'})[path.extname(file)] || 'application/octet-stream';
    return route.fulfill({body: fs.readFileSync(file), contentType});
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight && SalesRuntime.current?.data?.initializedRole);
    await page.evaluate(({customer, opportunity}) => {
      const original=SalesPreview;
      window.visitProbe={calls:[],status:'none',denied:false,summary:'已有拜访建议'};
      window.SalesPreview={...original,request(options={}) {
        const url=new URL(options.url,location.href),path=url.pathname.replace('/api/v1',''),method=options.method||'GET';
        visitProbe.calls.push({path,method,data:options.data||{},section:url.searchParams.get('section')});
        const respond=(data,statusCode=200)=>{queueMicrotask(()=>{const response={data,statusCode};options.success?.(response);options.complete?.(response);});return {abort(){}};};
        if(path==='/visits/visit-synthetic')return respond({id:'visit-synthetic',customer_id:customer,customer_name:'合成客户',visit_date:'2026-10-14',follow_up_record:'本次测试只验证建议恢复',next_action:'讨论下一步',version_no:1});
        if(path==='/advice/latest'||path==='/advice') {
          if(visitProbe.denied)return respond({detail:'合成读取失败'},503);
          if(method==='POST')visitProbe.status='queued';
          const section=url.searchParams.get('section')||options.data?.section||'overview';
          const analysis={id:'visit-advice-'+section,subject_kind:'visit',subject_id:'visit-synthetic',customer_id:customer,opportunity_id:null,section,status:visitProbe.status,summary:visitProbe.summary,suggestions:[]};
          return respond(path.endsWith('/latest')?{state:visitProbe.status,analysis:visitProbe.status==='none'?null:analysis}:analysis);
        }
        return original.request(options);
      }};
    }, {customer, opportunity});
    await run(page);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
  } catch(error) {console.error(JSON.stringify(await page.evaluate(()=>({route:SalesRuntime.current?.route,error:SalesRuntime.current?.data?.opportunityError,body:document.body.innerText.slice(-1500),errors:SalesRuntime.errors,calls:visitProbe.calls}))).slice(0,10000));throw error;} finally {await context.close();}
}
const posts=page=>page.evaluate(()=>visitProbe.calls.filter(r=>r.path==='/advice'&&r.method==='POST'));
async function openVisit(page){
  await page.evaluate(customer=>SalesRuntime.wx.navigateTo({url:'/pages/visit-detail/index?customer_id='+customer+'&visit_id=visit-synthetic'}),customer);
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-detail/index'&&SalesRuntime.current.data.visit&&!SalesRuntime.current.data.loading);
  await page.getByRole('button',{name:'生成建议',exact:true}).waitFor();
}

test('visit detail and task tab discover with GET, explicit generation returns queued then cold reentry restores',()=>fixture(async page=>{
  await openVisit(page);
  await page.getByText('待办建议',{exact:true}).click();
  await page.waitForFunction(()=>SalesRuntime.current.data.visitAdviceSection==='tasks'&&SalesRuntime.current.data.visitAdvice.status==='idle');
  assert.equal((await posts(page)).length,0);
  await page.getByRole('button',{name:'生成建议',exact:true}).click();
  await page.getByText('建议正在后台生成，可刷新进度或稍后回来查看。',{exact:true}).waitFor();
  assert.equal((await posts(page)).length,1);
  assert.equal((await posts(page))[0].data.section,'tasks');
  await page.evaluate(()=>{visitProbe.status='succeeded';visitProbe.summary='恢复的归档待办建议';});
  await page.getByRole('button',{name:'刷新已有建议',exact:true}).click();
  await page.getByText('恢复的归档待办建议',{exact:true}).waitFor();
  await page.evaluate(()=>SalesRuntime.wx.navigateTo({url:'/pages/index/index'}));
  await page.evaluate(()=>{visitProbe.status='none';});
  await openVisit(page);
  await page.evaluate(()=>{visitProbe.status='succeeded';});
  await page.getByText('待办建议',{exact:true}).click();
  await page.getByText('恢复的归档待办建议',{exact:true}).waitFor();
  assert.equal((await posts(page)).length,1);
  if(process.env.ADVICE_SCREENSHOT_DIR)await page.screenshot({path:process.env.ADVICE_SCREENSHOT_DIR+'/visit-detail.png',fullPage:true});
}));

test('archived screen keeps empty advice entry, reopens and refreshes without POST, then explicitly generates',()=>fixture(async page=>{
  await page.evaluate(()=>SalesRuntime.wx.navigateTo({url:'/pages/visit-confirm/index'}));
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index');
  await page.evaluate(customer=>SalesRuntime.current.setData({archived:true,isFde:false,busy:false,visitId:'visit-synthetic',customerId:customer,customerName:'合成客户',values:{next_action:'核对后续计划'},advice:null,adviceStatus:'idle',adviceBusy:false,showAdvice:false}),customer);
  await page.getByRole('button',{name:'查看本次待办建议',exact:true}).click();
  await page.getByText('还没有生成待办建议。',{exact:true}).waitFor();
  assert.equal((await posts(page)).length,0);
  await page.getByRole('button',{name:'生成建议',exact:true}).click();
  await page.getByText('建议正在后台生成，可刷新进度，也可稍后从拜访详情的“待办建议”查看。',{exact:true}).waitFor();
  assert.equal((await posts(page)).length,1);
  await page.evaluate(()=>{visitProbe.status='succeeded';visitProbe.summary='归档建议恢复成功';});
  await page.getByRole('button',{name:'刷新状态',exact:true}).click();
  await page.getByText('归档建议恢复成功',{exact:true}).waitFor();
  await page.getByRole('button',{name:'完成，稍后再看',exact:true}).click();
  await page.getByRole('button',{name:'查看本次待办建议',exact:true}).click();
  await page.getByText('归档建议恢复成功',{exact:true}).waitFor();
  assert.equal((await posts(page)).length,1);
}));
