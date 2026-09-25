import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({previewOnly:true,environment:'神舟数码 Web UI · 前端演示'});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();if(server)await new Promise(r=>{server.close(r);server.closeAllConnections();});});
async function fixture(run,{width=1440,height=900,role='sales'}={}) {
  const context=await browser.newContext({viewport:{width,height},serviceWorkers:'block'}), page=await context.newPage(), errors=[], requests=[];
  await context.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
  page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||u.pathname.startsWith('/api/')||u.pathname==='/local-login'){requests.push(u.pathname);return r.abort();}return r.continue();});
  const go=async key=>{await page.goto(`${base}/?mode=preview#/pages/${key}/index`);await page.locator(`[data-department-page="pages/${key}/index"]`).waitFor();};
  try {await run({page,go});assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);await fits(page);}
  catch(e){await page.screenshot({path:`验收/failure-${width}-${Date.now()}.png`,fullPage:true});throw e;}
  finally{await context.close();}
}
async function fits(page) {assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'no document horizontal overflow');}
async function enterVisit(page,go) {
  await go('visit-entry');await page.locator('.ds-ve-results').getByRole('button').filter({hasText:'星河'}).click();
  await page.getByPlaceholder('可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……').fill('沟通内容：双方核对试点验收清单，客户希望补充数据样本范围。\n下一步计划：2026年9月30日由我整理验收清单并发送给客户。\n跟进日期：2026-09-24\n对接人：合成演示联系人');
  await page.getByRole('button',{name:'提交结构化',exact:true}).click();
  await page.locator('[data-department-page="pages/visit-confirm/index"]').waitFor();
  await page.waitForFunction(()=>SalesRuntime.current.data.core?.length>0);
}
async function selectType(page) {
  await page.getByRole('combobox',{name:'跟进类型',exact:true}).click();
  await page.getByText('商机推进',{exact:true}).last().click();
}
async function selectRecords(page) {
  await page.getByRole('button',{name:'添加业务数据',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'选择关联业务数据'});
  await dialog.getByRole('checkbox',{name:'选择 DEMO-001',exact:true}).check();
  await dialog.getByPlaceholder('搜索业务名称、编号或说明').fill('DEMO-005');
  await dialog.getByRole('checkbox',{name:'选择 DEMO-005',exact:true}).check();
  assert.match(await dialog.innerText(),/已选 2 条/);
  await dialog.getByRole('button',{name:'确认关联'}).click();
  await dialog.waitFor({state:'hidden'});
}
for (const size of [{width:1440,height:900},{width:1366,height:768},{width:1024,height:600},{width:390,height:844}]) {
  test(`${size.width}px opportunity list click, eight fields, navigation and edit`,{timeout:45000},()=>fixture(async({page,go})=>{
    await go('workbench');await page.locator('.ds-opp-groups .ant-table-row').first().waitFor();
    assert.equal(await page.getByRole('button',{name:'新增商机',exact:true}).count(),0);
    assert.ok(await page.getByText('新增商机',{exact:true}).count()>0,'statistic remains');
    if(size.width===1440) await page.screenshot({path:'验收/01-Web商机列表.png',fullPage:true});
    await page.locator('.ds-opp-groups .ant-table-row').first().click();
    const facts=page.getByRole('definition');
    await page.locator('.ds-od-appearance').waitFor();
    const labels=await page.locator('.ds-od-appearance dt').allTextContents();
    assert.deepEqual(labels,['商机名称','商机金额（元/人民币）','代理商名称','最终用户名称','预计签约日期','商机来源','项目背景','是否框架商机']);
    const values=await page.locator('.ds-od-appearance dd').allTextContents();
    for (const i of [2,3,5,6,7]) assert.equal(values[i],'未填写');
    assert.match(values[1],/^[\d,]+\.\d{2}$/);
    await fits(page);
    if(size.width===1440)await page.screenshot({path:'验收/02-Web商机八字段.png',fullPage:true});
    if(size.width===390)await page.screenshot({path:'验收/05-Web窄屏详情.png',fullPage:true});
    await page.getByRole('button',{name:'编辑商机',exact:true}).click();
    await page.locator('[data-department-page="pages/opportunity-create/index"]').waitFor();
  },{...size,role:'manager'}));
  test(`${size.width}px follow-up select, search multi-select, cancel/remove and reload`,{timeout:60000},()=>fixture(async({page,go})=>{
    await enterVisit(page,go);
    assert.equal(await page.getByText('伙伴名称',{exact:true}).count(),0);
    await page.getByRole('button',{name:'下一步 · AI 质检'}).click();
    await page.getByRole('alert').filter({hasText:'请选择跟进类型'}).waitFor();
    await selectType(page);await selectRecords(page);
    assert.equal(await page.locator('.ds-vc-business-chips li').count(),2);
    await page.getByRole('button',{name:'添加业务数据',exact:true}).click();
    let dialog=page.getByRole('dialog',{name:'选择关联业务数据'});
    await dialog.getByRole('checkbox',{name:'选择 DEMO-002',exact:true}).check();
    await dialog.getByRole('button',{name:/取\s*消/}).click();await dialog.waitFor({state:'hidden'});
    assert.equal(await page.locator('.ds-vc-business-chips li').count(),2);
    await page.getByRole('button',{name:'移除 DEMO-001',exact:true}).click();
    await page.getByRole('button',{name:'保存草稿',exact:true}).click();
    await page.reload();await page.locator('.ds-vc-business-chips li').waitFor();
    assert.equal(await page.locator('.ds-vc-business-chips li').count(),1);
    assert.match(await page.locator('.ds-vc-followup-select').innerText(),/商机推进/);
    assert.ok(!(await page.evaluate(()=>SalesRuntime.current.data.values)).followUpType,'not written to legacy values');
    await fits(page);
    if(size.width===1440)await page.screenshot({path:'验收/03-Web跟进表单.png',fullPage:true});
    if(size.width===390)await page.screenshot({path:'验收/06-Web窄屏跟进.png',fullPage:true});
    await page.getByRole('button',{name:'添加业务数据',exact:true}).click();
    dialog=page.getByRole('dialog',{name:'选择关联业务数据'});
    await dialog.getByRole('checkbox',{name:'选择 DEMO-005',exact:true}).waitFor();
    if(size.width===1440) {
      await page.waitForFunction(()=>{const modal=document.querySelector('.ant-modal');return modal && getComputedStyle(modal).opacity==='1' && !modal.getAnimations({subtree:true}).some(a=>a.playState==='running');});
      await page.screenshot({path:'验收/04-Web业务多选.png',fullPage:true});
    }
    await dialog.getByPlaceholder('搜索业务名称、编号或说明').fill('无此条目');
    await dialog.getByText('没有匹配的业务数据，请更换关键词').waitFor();
    await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});
    assert.equal(await page.getByRole('button',{name:'添加业务数据',exact:true}).evaluate(el=>el===document.activeElement),true);
  },size));
}
test('keyboard selection, quota failure/retry, review and archive remain usable',{timeout:60000},()=>fixture(async({page,go})=>{
  await enterVisit(page,go);
  const select=page.getByRole('combobox',{name:'跟进类型',exact:true});await select.focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
  assert.match(await page.locator('.ds-vc-followup-select').innerText(),/客户拜访/);
  await selectRecords(page);
  await page.evaluate(()=>{window.originalSetItem=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith('shenzhou:web-demo:'))throw new Error('quota');return window.originalSetItem.call(this,k,v);};});
  await page.getByRole('button',{name:'移除 DEMO-001',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'演示草稿未能保存'}).waitFor();
  assert.equal(await page.locator('.ds-vc-business-chips li').count(),1);
  await page.evaluate(()=>{Storage.prototype.setItem=window.originalSetItem;});await page.getByRole('button',{name:'重试保存',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'演示草稿未能保存'}).waitFor({state:'hidden'});
  await page.getByRole('button',{name:'下一步 · AI 质检'}).click();
  await page.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result');
  await page.getByRole('button',{name:'返回完善',exact:true}).click();
  const before=await page.evaluate(()=>SalesRuntime.current.data.reviewRunId);
  await page.locator('.ds-vc-form textarea').last().fill('2026年10月1日由我发送验收文档并请客户确认。');
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.reviewStale),true);
  await page.getByRole('button',{name:'下一步 · AI 质检'}).click();
  await page.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result'&&SalesRuntime.current.data.canSubmit);
  assert.notEqual(await page.evaluate(()=>SalesRuntime.current.data.reviewRunId),before);
  await page.getByRole('button',{name:'确认保存',exact:true}).click();
  await page.getByRole('button',{name:'确认归档',exact:true}).click();
  await page.getByRole('region',{name:'拜访已保存'}).waitFor();
  assert.match(await page.getByRole('region',{name:'跟进演示信息'}).innerText(),/客户拜访/);
  assert.match(await page.getByRole('region',{name:'跟进演示信息'}).innerText(),/多区域协同/);
  await page.getByRole('region',{name:'跟进演示信息'}).waitFor();
}));
