const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const assets = path.resolve(__dirname, '../../backend/src/sales_backend/web/assets');
const modules = async () => ({core:await import(pathToFileURL(path.join(assets,'core.js')).href), management:await import(pathToFileURL(path.join(assets,'task-management.js')).href)});
const task = (extra={}) => ({id:'t1', title:'整理项目推进计划', status:'pending_confirm', created_at:'2026-09-20T02:00:00Z', schedule_start_at:'2026-09-28T02:00:00Z', start_kind:'dispatched', due_at:'2026-10-02T10:30:00Z', accepted_at:'2026-10-01T06:00:00Z', owner_id:'p1', owner_name:'负责人甲', owner_active:true, owners:[{id:'p1',name:'负责人甲',active:true}], creator_name:'发起人', batch_id:'b1', batch_name:'第 40 周周任务', assignees:[], ...extra});
const summary = {total:137, pending_count:98, pending_review_count:17, overdue_count:31, completed_count:35, cancelled_count:4, undated_count:0};
const pageData = (extra={}) => ({items:[task()], summary, has_more:true, next_offset:50, as_of:'2026-09-30T03:00:00Z', ...extra});
async function render(filters={}, data=pageData(), permissions={'task.read':true}) {
  const {core,management}=await modules();core.clearSession();
  Object.assign(core.state,{page:'tasks', actor:{workspace_id:'workspace',user_id:'me',role:'sales'},token:'fixture', company:{id:'workspace'},permissions,filters,offset:0});
  const requests=[];global.fetch=async(url,options)=>{requests.push({url,options});return {ok:true,status:200,json:async()=>data};};
  const view=await management.taskManagement({showTask:async()=>{},newTaskImport:()=>{}});
  return {core,management,requests,view};
}

test('Beijing calendar windows cover Monday/Sunday, year change, and leap February',async()=>{
  const {management:m}=await modules();
  assert.deepEqual(m.calendarWindow('2026-09-27').days,['2026-09-21','2026-09-22','2026-09-23','2026-09-24','2026-09-25','2026-09-26','2026-09-27']);
  assert.equal(m.calendarWindow('2026-01-01').from,'2025-12-29');
  assert.equal(m.calendarWindow('2028-02-29','month').days.length,29);
  assert.throws(()=>m.calendarWindow('2026-02-30'),/有效/);
});
test('queries default to all visible; only contract filters pass and timeline date anchor supersedes old readonly end',async()=>{
  const {management:m}=await modules();
  assert.deepEqual(m.managementFilters({layout:'list',view:'untrusted',member_name:'UI only'}),{scope:'all',undated:'include'});
  assert.deepEqual(m.managementFilters({scope:'mine',member_id:'p',unassigned:'true',overdue:'true',undated:'only'}),{scope:'mine',unassigned:true,overdue:true,undated:'only'});
  const next=m.managementFilters({layout:'timeline',period:'month',date_from:'2026-10-07',date_to:'2026-09-30'});
  assert.equal(next.date_from,'2026-10-01');assert.equal(next.date_to,'2026-10-31');
  assert.throws(()=>m.managementFilters({date_from:'2026-10-07',date_to:'2026-09-30'}),/不能晚于/);
});
test('planned interval clips with honest continuation; acceptance never changes the plan',async()=>{
  const {management:m}=await modules(),window=m.calendarWindow('2026-09-28');
  const early=task({schedule_start_at:'2026-09-25T10:00:00Z',due_at:'2026-10-09T10:00:00Z'});
  const range=m.plannedInterval(early,window);
  assert.equal(range.kind,'scheduled');assert.equal(range.column,1);assert.equal(range.span,7);assert.equal(range.before,true);assert.equal(range.after,true);
  assert.deepEqual(m.plannedInterval({...early,accepted_at:'2026-10-04T00:00:00Z',completed_at:'2026-10-05T00:00:00Z'},window),range);
  assert.equal(m.plannedInterval(task({schedule_start_at:'2026-09-27T16:00:00Z',due_at:'2026-09-28T16:00:00Z'}),window).span,2);
});
test('no deadline and invalid intervals stay separate and never become fabricated bars',async()=>{
  const {management:m}=await modules(),window=m.calendarWindow('2026-09-28');
  assert.equal(m.plannedInterval(task({due_at:null}),window).kind,'undated');
  assert.equal(m.plannedInterval(task({due_at:'2026-09-27T10:00:00Z'}),window).kind,'invalid');
  assert.equal(m.plannedInterval(task({schedule_invalid:true}),window).kind,'invalid');
  assert.equal(m.plannedInterval(task({schedule_start_at:'2026-10-08T10:00:00Z',due_at:'2026-10-09T10:00:00Z'}),window).kind,'outside');
});
test('full summary remains server supplied even with a single visible page; timestamps show Beijing year and time',async()=>{
  const h=await render();const q=new URL(h.requests[0].url,'http://test').searchParams;
  assert.equal(q.get('scope'),'all');assert.equal(q.get('page_size'),'50');assert.equal(q.has('inbox'),false);
  assert.match(h.view.html,/全部筛选结果 137 项/);assert.match(h.view.html,/>98<\/strong>/);assert.match(h.view.html,/>17<\/strong>/);
  assert.match(h.view.html,/2026-10-02 18:30 截止/);assert.match(h.view.html,/2026-09-28 下发/);
  assert.doesNotMatch(h.view.html,/id="new-task-import"/);assert.match(h.view.html,/批次与进度/);
  assert(h.requests.every(r=>r.options.method==='GET'));
});
test('timeline groups all current owners, labels inactive/unclaimed/date anomalies, escapes input, and discloses pagination',async()=>{
  const h=await render({layout:'timeline',date_from:'2026-09-28'},pageData({items:[
    task({title:'<img src=x onerror=alert(1)>',owners:[{id:'new',name:'接收人甲',active:true},{id:'old',name:'停用负责人',active:false}]}),
    task({id:'t2',title:'无截止任务',owner_id:null,owner_name:null,owners:[],due_at:null}),
    task({id:'t3',title:'旧日期任务',schedule_invalid:true}),
  ]}));
  assert.match(h.view.html,/接收人甲/);assert.match(h.view.html,/停用负责人/);assert.match(h.view.html,/账号停用/);
  assert.equal((h.view.html.match(/class="tm-bar tm-bar-pending_confirm"/g)||[]).length,2);
  assert.match(h.view.html,/待领取 · 未分配负责人/);assert.match(h.view.html,/未设置截止日期/);assert.match(h.view.html,/日期待核对/);
  assert.doesNotMatch(h.view.html,/<img src=x/);assert.match(h.view.html,/&lt;img/);
  assert.match(h.view.html,/下方仅绘制本页任务/);assert.match(h.view.html,/统计只计一项/);assert.doesNotMatch(h.view.html,/\d+%/);
});
test('invalid pagination is rejected, read permission fails closed, and stale render has no returned content',async()=>{
  await assert.rejects(()=>render({},pageData({next_offset:0})),/列表已变化/);
  await assert.rejects(()=>render({},pageData(),{}),/未获/);
  const h=await render();const view=await h.management.taskManagement({},{isCurrent:()=>false});assert.equal(view.html,'');
});

async function browserHarness(check) {
  const http=require('node:http'),fs=require('node:fs/promises');
  const {chromium}=require(process.env.SALESBUDDY_PLAYWRIGHT_MODULE || 'playwright');
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/') {res.setHeader('Content-Type','text/html');res.end('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><link rel="stylesheet" href="/assets/console.css"><main id="content" class="content"></main><dialog id="dialog"></dialog><div id="toast"></div></html>');return;}
    const name=req.url.replace('/assets/','');if(!/^[a-z-]+\.(js|css)$/.test(name)){res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'application/javascript');
    try{res.end(await fs.readFile(path.join(assets,name)));}catch(_){res.writeHead(404);res.end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  try {
    browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100}}),requests=[];
    let ownerDeferred;
    await page.route('**/api/**',async route=>{
      const request=route.request(),url=new URL(request.url());requests.push({path:url.pathname,query:Object.fromEntries(url.searchParams),method:request.method()});
      let payload;
      if(url.pathname.endsWith('/owners')) {
        if(url.searchParams.get('q')==='迟到') {ownerDeferred=route;return;}
        payload=url.searchParams.get('offset')==='100'?{items:[{id:'p101',name:'后页负责人',account_code:'LATE',active:false,task_count:2}],has_more:false,unassigned_count:0}:{items:[{id:'p1',name:'负责人甲',account_code:'A',active:true,task_count:8}],has_more:true,next_offset:100,unassigned_count:1};
      } else if(url.pathname==='/api/v1/task-imports')payload={items:[{id:'batch2',batch_name:'第二周已下发',meeting_date:'2026-09-28'}],has_more:false};
      else payload=pageData({next_offset:Number(url.searchParams.get('offset') || 0)+50,items:[task(),task({id:'t2',title:'核对回款进展',status:'pending_review'}),task({id:'t3',title:'安排联合拜访',status:'in_progress',owners:[{id:'p2',name:'负责人乙',active:true}],owner_id:'p2'}),task({id:'t4',title:'补充项目资料',status:'deferred',schedule_invalid:true,owners:[{id:'p3',name:'停用负责人',active:false}],owner_id:'p3',owner_active:false})]});
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async()=>{
      const core=await import('/assets/core.js'),m=await import('/assets/task-management.js');window.core=core;window.details=[];window.newImports=0;window.renders=0;
      Object.assign(core.state,{actor:{workspace_id:'w',user_id:'me',role:'administrator'},token:'fixture',company:{id:'w'},page:'tasks',offset:0,filters:{date_from:'2026-09-28',date_to:'2026-10-04'},permissions:{'task.read':true,'task.create_daily':true},refresh:async()=>{
        const generation=++window.renders;const v=await m.taskManagement({showTask:async id=>window.details.push(id),newTaskImport:()=>{window.newImports++;}},{isCurrent:()=>generation===window.renders});
        if(generation!==window.renders)return;const root=document.querySelector('#content');root.innerHTML=v.html;v.bind(root);
      }});await core.state.refresh();
    });
    await check(page,requests,async()=>{assert(ownerDeferred);await ownerDeferred.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[{id:'wrong',name:'不应显示',account_code:'NO',task_count:1}],has_more:false})});});
  } finally {await browser?.close();await new Promise(resolve=>server.close(resolve));}
}
const browserOptions={skip:process.env.SALESBUDDY_BROWSER_DOM_TESTS!=='1'};
test('real DOM: management filters, directory pagination, timeline windows, task details and stale tenant protections',browserOptions,async()=>browserHarness(async(page,requests,replyLate)=>{
  await page.getByRole('button',{name:'与我有关',exact:true}).click();await page.waitForFunction(()=>window.core.state.filters.scope==='mine');
  await page.waitForFunction(()=>document.querySelector('[data-tm-scope="mine"]').getAttribute('aria-pressed')==='true');
  assert.equal(requests.at(-1).query.scope,'mine');
  await page.locator('#task-next').click();await page.waitForFunction(()=>window.core.state.offset===50);await page.waitForFunction(()=>document.querySelector('.pager').textContent.includes('第 2 页'));
  assert.equal(requests.at(-1).query.offset,'50');
  await page.locator('#tm-owner-filter').click();await page.locator('#tm-picker-next').click();await page.getByRole('button',{name:/后页负责人/}).click();
  await page.waitForFunction(()=>window.core.state.filters.member_id==='p101' && document.querySelector('#tm-owner-filter').textContent.includes('后页负责人'));
  assert.equal(requests.at(-1).query.member_id,'p101');assert.equal(requests.at(-1).query.offset,'0');
  await page.locator('#tm-batch-filter').click();await page.getByRole('button',{name:/第二周已下发/}).click();
  await page.waitForFunction(()=>window.core.state.filters.batch_id==='batch2' && document.querySelector('#tm-batch-filter').textContent.includes('第二周'));
  assert.equal(requests.find(r=>r.path==='/api/v1/task-imports').query.status,'confirmed');
  assert.equal(requests.some(r=>/\/task-imports\/[^?]+/.test(r.path)),false);
  await page.getByRole('button',{name:'按负责人时间线',exact:true}).click();await page.locator('.tm-timeline').waitFor();
  await page.locator('input[name=date_from]').fill('2026-10-08');await page.getByRole('button',{name:'筛选',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('input[name=date_to]').value==='2026-10-11');
  assert.equal(requests.at(-1).query.date_from,'2026-10-05');
  await page.getByRole('button',{name:'月',exact:true}).click();await page.waitForFunction(()=>document.querySelector('input[name=date_to]').value==='2026-10-31');
  await page.getByRole('button',{name:'上月',exact:true}).click();await page.waitForFunction(()=>document.querySelector('input[name=date_to]').value==='2026-09-30');
  await page.locator('.tm-bar').first().click();assert.deepEqual(await page.evaluate(()=>window.details),['t1']);
  assert.equal(requests.some(r=>r.method!=='GET'),false);
  if(process.env.SALESBUDDY_TASK_UI_SCREENSHOT)await page.screenshot({path:process.env.SALESBUDDY_TASK_UI_SCREENSHOT,fullPage:true});
  await page.locator('#tm-owner-filter').click();await page.locator('#tm-owner-search').fill('迟到');await page.locator('#tm-picker-search').click();
  await page.waitForTimeout(20);await page.evaluate(()=>window.core.setCompany({id:'other'}));await replyLate();
  await page.waitForTimeout(30);assert.equal(await page.getByText('不应显示',{exact:true}).count(),0);
  await page.evaluate(()=>document.querySelector('#dialog').close());const previous=requests.length;
  await page.locator('#new-task-import').click();assert.equal(await page.evaluate(()=>window.newImports),0);assert.equal(requests.length,previous);
}));
test('real DOM: filter submission preserves context and rejects reversed list dates; reset and import navigation stay explicit',browserOptions,async()=>browserHarness(async(page,requests)=>{
  await page.locator('input[name=date_from]').fill('2026-11-01');await page.getByRole('button',{name:'筛选',exact:true}).click();
  assert.match(await page.locator('#tm-filter-error').textContent(),/不能晚于/);assert.equal(requests.length,1);
  await page.locator('input[name=date_to]').fill('2026-11-30');await page.locator('select[name=status]').selectOption('pending_review');await page.locator('input[name=overdue]').check();await page.getByRole('button',{name:'筛选',exact:true}).click();
  await page.waitForFunction(()=>window.core.state.filters.status==='pending_review' && !document.querySelector('#tm-filter-error').textContent);
  assert.equal(requests.at(-1).query.overdue,'true');
  await page.locator('#tm-reset').click();await page.waitForFunction(()=>Object.keys(window.core.state.filters).length===0 && document.querySelector('input[name=date_from]').value==='');
  await page.locator('#new-task-import').click();assert.equal(await page.evaluate(()=>window.newImports),1);
  await page.locator('#task-import-batches').click();await page.waitForFunction(()=>window.core.state.filters.view==='imports');
  assert.equal(requests.some(r=>r.method!=='GET'),false);
}));
