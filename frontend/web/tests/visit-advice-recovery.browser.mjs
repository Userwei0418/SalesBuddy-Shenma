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
      window.visitProbe={calls:[],status:'none',denied:false,summary:'已有拜访建议',suggestions:[]};
      window.SalesPreview={...original,request(options={}) {
        const url=new URL(options.url,location.href),path=url.pathname.replace('/api/v1',''),method=options.method||'GET';
        visitProbe.calls.push({path,method,data:options.data||{},section:url.searchParams.get('section')});
        const respond=(data,statusCode=200)=>{queueMicrotask(()=>{const response={data,statusCode};options.success?.(response);options.complete?.(response);});return {abort(){}};};
        if(path==='/directory/fde-members') {
          const members=Array.from({length:18},(_,i)=>({id:'person-'+(i+1),name:['合成成员 · 张晨','合成成员 · 林悦','合成成员 · 陈思'][i]||'合成成员 '+(i+1),team:'解决方案团队',team_id:i===2?'other-team':'team-1',role:'fde'}));
          const items=members.filter(row=>row.name.includes(url.searchParams.get('q')||''));
          return respond({items,total:items.length});
        }
        if(path==='/visits/visit-synthetic')return respond({id:'visit-synthetic',customer_id:customer,customer_name:'合成客户',visit_date:'2026-10-14',follow_up_record:'本次测试只验证建议恢复',next_action:'讨论下一步',version_no:1});
        if(path.startsWith('/advice/suggestions/')&&path.endsWith('/decision')&&method==='POST') {
          const suggestion=visitProbe.suggestions.find(row=>path===`/advice/suggestions/${row.id}/decision`);
          Object.assign(suggestion,{decision:options.data.decision,decision_note:options.data.note,version_no:suggestion.version_no+1});
          return respond(suggestion);
        }
        if(path==='/advice/latest'||path==='/advice'||path.startsWith('/advice/visit-advice-')) {
          if(visitProbe.denied)return respond({detail:'合成读取失败'},503);
          if(method==='POST')visitProbe.status='queued';
          const section=url.searchParams.get('section')||options.data?.section||'overview';
          const analysis={id:'visit-advice-'+section,subject_kind:'visit',subject_id:'visit-synthetic',customer_id:customer,opportunity_id:null,section,status:visitProbe.status,summary:visitProbe.summary,suggestions:structuredClone(visitProbe.suggestions)};
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

async function openFollowUpAdvice(page) {
  await page.evaluate(customer => {
    visitProbe.status='succeeded';
    visitProbe.summary='本次沟通已保存，以下三项行动可分别处理。';
    visitProbe.suggestions=Array.from({length:3},(_,i)=>({id:'followup-'+(i+1),title:['补充验收清单','确认试点时间','安排方案评审'][i],evidence:'客户在本次沟通中提出下一阶段安排。',action:'与客户确认具体负责人和交付时间，并记录结果。',decision:'pending',version_no:1}));
    SalesRuntime.wx.navigateTo({url:'/pages/visit-confirm/index'});
  },customer);
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index');
  await page.evaluate(customer=>SalesRuntime.current.setData({archived:true,followUpOnly:true,isFde:false,busy:false,visitId:'visit-synthetic',customerId:customer,customerName:'合成客户 · 星河制造',values:{next_action:'确认试点范围和下一次评审时间'},advice:null,adviceStatus:'idle',adviceBusy:false,showAdvice:false}),customer);
  await page.getByRole('button',{name:'查看本次待办建议',exact:true}).click();
  await page.waitForFunction(()=>document.querySelectorAll('.ds-advice-actions').length===3);
}

test('follow-up archive mounts all three actions; adopt opens the bound task form, dismiss reloads, and Drawer keeps context',()=>fixture(async page=>{
  await openFollowUpAdvice(page);
  const drawer=page.locator('.ds-advice-drawer .ant-drawer-content-wrapper');
  await page.getByRole('button',{name:'采纳并建待办',exact:true}).nth(2).waitFor();
  assert.equal(await page.getByText('正在准备建议操作…',{exact:true}).count(),0);
  await page.waitForFunction(()=>{const b=document.querySelector('.ds-advice-drawer .ant-drawer-content-wrapper')?.getBoundingClientRect();return b&&Math.abs(b.right-innerWidth)<2;});
  const box=await drawer.boundingBox();
  assert.ok(box.x>=870&&Math.abs(box.x+box.width-1440)<2&&box.width<=561,'advice is a bounded right Drawer: '+JSON.stringify(box));
  assert.equal(await page.getByRole('heading',{name:'合成客户 · 星河制造'}).count(),1);
  if(process.env.ADVICE_SCREENSHOT_DIR)await page.screenshot({path:process.env.ADVICE_SCREENSHOT_DIR+'/followup-advice-drawer.png'});
  for(let index=0;index<3;index++) {
    await page.getByRole('button',{name:'采纳并建待办',exact:true}).nth(index).click();
    await page.waitForFunction(id=>SalesRuntime.current.route==='pages/management-task-create/index'&&SalesRuntime.current.data.adviceSource?.id===id,'followup-'+(index+1));
    assert.equal(await page.getByRole('textbox',{name:'任务描述',exact:true}).inputValue(),'与客户确认具体负责人和交付时间，并记录结果。');
    await page.evaluate(()=>SalesRuntime.wx.navigateBack());
    await page.getByRole('button',{name:'采纳并建待办',exact:true}).nth(2).waitFor();
  }
  const second=page.locator('.ds-vc-advice-list > li').nth(1);
  await second.getByRole('button',{name:'无需待办',exact:true}).click();
  await page.locator('.wx-modal-mask').getByRole('button',{name:'确认',exact:true}).click();
  await second.getByText(/^已处理 · 无需待办/).waitFor();
  assert.equal(await page.getByRole('button',{name:'采纳并建待办',exact:true}).count(),2);
  const decisions=await page.evaluate(()=>visitProbe.calls.filter(r=>r.path.includes('/decision')));
  assert.deepEqual(decisions.map(r=>({path:r.path,decision:r.data.decision,version:r.data.version_no})),[{path:'/advice/suggestions/followup-2/decision',decision:'no_task',version:1}]);
  assert.equal(await page.evaluate(()=>visitProbe.calls.filter(r=>r.method==='POST'&&r.path==='/tasks').length),0,'opening task confirmation does not silently create tasks');
  await page.keyboard.press('Escape');
  await drawer.waitFor({state:'hidden'});
  await page.getByRole('button',{name:'查看本次待办建议',exact:true}).click();
  await second.getByText(/^已处理 · 无需待办/).waitFor();
  assert.equal((await posts(page)).length,0,'opening and reentering never generates advice');
  await page.setViewportSize({width:390,height:844});await paint(page);
  const small=await drawer.boundingBox();assert.ok(small.x>=-1&&small.width<=391,'Drawer fits mobile width');
}));

test('follow-up advice honors decision permission, stale status and refreshed suggestion identity',()=>fixture(async page=>{
  await openFollowUpAdvice(page);
  await page.evaluate(()=>{
    const app=SalesRuntime.app,can=app.can.bind(app);app.can=key=>key==='advice.decide'?false:can(key);
    SalesRuntime.current.selectAllComponents('advice-actions').forEach(component=>component.refreshPermissions());
  });
  await page.getByText('建议供协作参考，由有权负责人决定。',{exact:true}).nth(2).waitFor();
  assert.equal(await page.getByRole('button',{name:'采纳并建待办',exact:true}).count(),0);
  await page.evaluate(()=>SalesRuntime.current.setData({advice:{...SalesRuntime.current.data.advice,stale:true}}));
  await page.getByText('建议已失效，请重新获取后再决定是否创建待办。',{exact:true}).nth(2).waitFor();
  await page.evaluate(()=>{visitProbe.suggestions=[{...visitProbe.suggestions[0],id:'replacement',title:'更新后的建议'}];});
  await page.getByRole('button',{name:'刷新状态',exact:true}).click();
  await page.getByText('更新后的建议',{exact:true}).waitFor();
  assert.equal(await page.getByText('正在准备建议操作…',{exact:true}).count(),0);
  assert.equal(await page.getByText('建议供协作参考，由有权负责人决定。',{exact:true}).count(),1);
  assert.equal(await page.evaluate(()=>visitProbe.calls.filter(r=>r.method==='POST'&&/^\/(advice|tasks)/.test(r.path)).length),0);
}));

test('FDE personnel dialog is centered, searches and drafts selections; cancel preserves parent and confirm applies once',()=>fixture(async page=>{
  await page.evaluate(()=>SalesRuntime.wx.navigateTo({url:'/pages/visit-confirm/index'}));
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index');
  await page.evaluate(customer=>SalesRuntime.current.setData({archived:false,followUpOnly:false,isFde:false,busy:false,editing:false,customerId:customer,customerName:'合成客户 · 星河制造',customerConfirmed:true,opportunityEditing:false,opportunityDraft:{visit_fde_members:[],visit_fde_member_ids:[]},values:{follow_up_record:'合成沟通记录，用于预览人员选择。'}}),customer);
  const opener=page.locator('.ds-vc-visit-participants').getByRole('button',{name:/^选\s*择$/});
  await opener.click();
  const dialog=page.getByRole('dialog',{name:'本次出席 FDE',exact:true});
  await dialog.getByRole('checkbox',{name:'合成成员 · 张晨',exact:true}).waitFor();
  await page.evaluate(()=>{
    const picker=SalesRuntime.current.selectComponent('fde-picker');
    picker.setData({restrictToTeams:true,allowedTeamIds:['team-1'],excludedIds:['person-2']});picker.decorate();
  });
  await paint(page);
  assert.equal(await dialog.getByRole('checkbox',{name:'合成成员 · 陈思',exact:true}).isDisabled(),true);
  assert.equal(await dialog.getByRole('checkbox',{name:'合成成员 · 林悦',exact:true}).isDisabled(),true);
  await dialog.getByRole('checkbox',{name:'合成成员 · 张晨',exact:true}).click();
  await dialog.getByText('已选 1 人',{exact:true}).waitFor();
  await dialog.getByRole('button',{name:'确认选择',exact:true}).hover();await paint(page);const box=await dialog.boundingBox();
  assert.ok(Math.abs(box.x+(box.width/2)-720)<2&&box.width===560,'compact centered dialog');
  assert.ok(Math.abs(box.y+(box.height/2)-500)<4,'vertically centered dialog');
  const metrics=await dialog.locator('.ant-modal-body').evaluate(el=>({client:el.clientHeight,scroll:el.scrollHeight}));
  assert.ok(metrics.scroll>metrics.client,'long directory scrolls in body');
  if(process.env.ADVICE_SCREENSHOT_DIR)await page.screenshot({path:process.env.ADVICE_SCREENSHOT_DIR+'/people-dialog.png'});
  await dialog.getByPlaceholder('搜索姓名，支持多选').fill('张晨');
  await page.waitForFunction(()=>SalesRuntime.current.selectComponent('fde-picker').data.rows.length===1);
  await dialog.getByRole('button',{name:/^取\s*消$/}).click();await dialog.waitFor({state:'hidden'});
  assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.opportunityDraft.visit_fde_member_ids),[]);
  await opener.click();await dialog.getByRole('checkbox',{name:'合成成员 · 张晨',exact:true}).click();
  await dialog.getByText('已选 1 人',{exact:true}).waitFor();
  await dialog.getByRole('button',{name:'确认选择',exact:true}).click();await dialog.waitFor({state:'hidden'});
  assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.opportunityDraft.visit_fde_member_ids),['person-1']);
  await opener.click();await dialog.getByText('已选 1 人',{exact:true}).waitFor();
  await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
  assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.opportunityDraft.visit_fde_member_ids),['person-1']);
  assert.equal(await page.evaluate(()=>visitProbe.calls.some(row=>row.method!=='GET'&&/^\/(advice|tasks|directory)/.test(row.path))),false);
}));
