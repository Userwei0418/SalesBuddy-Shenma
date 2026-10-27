const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assets=path.resolve(__dirname,'../../backend/src/sales_backend/web/assets');
const modules=async()=>({core:await import(pathToFileURL(path.join(assets,'core.js')).href),visits:await import(pathToFileURL(path.join(assets,'visits.js')).href)});
const record=(extra={})=>({id:'v1',status:'confirmed',subject_kind:'customer',subject_name:'演示客户甲',customer_id:'c1',customer_name:'演示客户甲',partner_id:null,partner_name:'演示伙伴',linked_opportunities:[{id:'op1',name:'智能应用项目'}],recorder_id:'me',recorder_name:'当前跟进人',recorder_active:true,creator_name:'录入人',original_recorder_name:null,manager_name:null,interaction_at:'2026-09-27T16:00:00Z',visit_date:'2026-09-28',created_date:'2026-09-28',created_at:'2026-09-28T02:25:00Z',interaction_mode_code:'online_meeting',contact_name:'联系人',contact_title:'产品负责人',visit_goal:'明确试点范围',visit_location:'线上',is_first_visit:false,follow_up_record:'已讨论一期范围。\n客户关注交付时间及接口兼容。',next_action:'10月3日前提交接口清单并确认试点负责人。',expectation_code:'met',follow_up_score:85.5,quality_review:{follow_up_score:85.5,grade:'良好',next_action_passed:true},version_no:3,collaborators:[{name:'协同同事'}],fde_participants:[{name:'协助工程师'}],can_supplement:true,supplement_fields:['partner_name','contact_name','contact_title','interaction_at','created_date','interaction_mode','visit_location'],original_materials:[{id:'file1',filename:'会议纪要.txt',can_download:true,download_url:'/api/v1/visit-imports/file1/original'}],...extra});
const result=(extra={})=>({items:[record()],summary:{total:117,subject_count:26,scored_count:94},has_more:true,next_offset:50,as_of:'2026-09-29T02:00:00Z',...extra});
function configure(core,filters={},permissions={}){
  core.clearSession();Object.assign(core.state,{actor:{workspace_id:'w',user_id:'me',role:'sales'},token:'fixture',company:{id:'w'},page:'visits',filters,offset:0,permissions:{'visit.read':true,...permissions}});
}
test('visit calendar and timestamp retain Beijing business dates independently from system recorded time',async()=>{
  const {visits:v}=await modules();
  assert.equal(v.visitDay('2026-09-27T16:00:00Z'),'2026-09-28');
  assert.equal(v.visitDay('2026-09-28 00:00:00'),'2026-09-28');
  assert.equal(v.visitDay('2026-09-28'),'2026-09-28');
  assert.equal(v.visitDay(null),'');assert.equal(v.visitTimestamp('2026-09-27T16:00:00Z'),'2026-09-28 00:00');
});
test('management filter contract preserves zero, rejects invalid dates/ranges, and excludes UI-only fields',async()=>{
  const {visits:v}=await modules();
  assert.deepEqual(v.visitFilters({scope:'mine',q:'  客户  ',recorder_id:'p1',recorder_name:'姓名',date_basis:'created',score_min:'0',score_max:'100',view:'secret'}),{scope:'mine',q:'客户',recorder_id:'p1',date_basis:'created',score_min:0,score_max:100});
  for(const filters of [{date_from:'2026-02-30'},{date_from:'2026-10-02',date_to:'2026-10-01'},{score_min:-1},{score_max:101},{score_min:90,score_max:80},{q:'x'.repeat(101)}])assert.throws(()=>v.visitFilters(filters));
  assert.deepEqual(v.visitFilters({}),{scope:'all',date_basis:'interaction'});
});
test('list uses complete server totals; historical partner/unknown records, null and decimal scores remain visible and escaped',async()=>{
  const {core,visits:v}=await modules();configure(core);let requested;
  global.fetch=async(url,options)=>{requested={url,options};return {ok:true,status:200,json:async()=>result({items:[record({subject_kind:'partner',subject_name:'伙伴 <标签>',customer_id:null,customer_name:null,partner_id:'p',original_recorder_name:'历史跟进人',manager_name:'现管理人'}),record({id:'v2',subject_kind:'unknown',subject_name:'',customer_id:null,customer_name:null,partner_name:null,linked_opportunities:[],follow_up_score:null}),record({id:'v3',follow_up_score:0})]})};};
  const page=await v.visits();const query=new URL(requested.url,'http://test').searchParams;
  assert.equal(query.get('scope'),'all');assert.equal(query.get('page_size'),'50');assert.equal(query.has('customer_id'),false);
  assert.match(page.html,/>117<\/strong>/);assert.match(page.html,/>26<\/strong>/);assert.match(page.html,/本页 3 条/);
  assert.match(page.html,/伙伴 &lt;标签&gt;/);assert.match(page.html,/原始跟进人/);assert.match(page.html,/当前管理人：现管理人/);
  assert.match(page.html,/历史关联未记录/);assert.match(page.html,/未评分/);assert.match(page.html,/>85\.5<small>/);assert.match(page.html,/>0<small>/);
  assert.match(page.html,/2026-09-28 10:25/);assert.equal(requested.options.method,'GET');
});
test('list response pagination fails closed and current read permission is required',async()=>{
  const {core,visits:v}=await modules();configure(core);
  global.fetch=async()=>({ok:true,status:200,json:async()=>result({next_offset:0})});await assert.rejects(()=>v.visits(),/响应不完整/);
  core.state.permissions['visit.read']=false;await assert.rejects(()=>v.visits(),/未获/);
  core.state.permissions['visit.read']=true;
  global.fetch=async()=>{core.state.permissions['visit.read']=false;return {ok:true,status:200,json:async()=>result()};};
  assert.deepEqual(await v.visits(),{html:''});
});
test('supplement sends only actually changed allowed auxiliary fields and current version, never archive prose or associations',async()=>{
  const {core,visits:v}=await modules();configure(core,{}, {'visit.supplement':true});
  const r=record({supplement_fields:[...record().supplement_fields,'follow_up_record','next_action','customer_id','recorder_id','collaborator_ids']});
  const form=new Map([['contact_name',' 新联系人 '],['interaction_at','2026-09-28'],['partner_name','演示伙伴'],['follow_up_record','试图重写正文'],['next_action','试图重写计划'],['customer_id','other'],['recorder_id','other'],['collaborator_ids','other']]);
  assert.deepEqual(v.supplementPayload(r,form),{version_no:3,contact_name:'新联系人'});
  assert.throws(()=>v.supplementPayload(r,new Map([['interaction_at','2026-09-28']])),/尚未修改/);
});
test('supplement gates object permission, identity, live feature permission and strict dates',async()=>{
  const {core,visits:v}=await modules();configure(core,{}, {'visit.supplement':true});const form=new Map([['contact_name','新联系人']]);
  assert.throws(()=>v.supplementPayload(record({can_supplement:false}),form),/不可补充/);
  assert.throws(()=>v.supplementPayload(record({recorder_id:'other'}),form),/不可补充/);
  assert.throws(()=>v.supplementPayload(record({version_no:null}),form),/版本/);
  assert.throws(()=>v.supplementPayload(record(),new Map([['interaction_at','']])) ,/不能清空/);
  assert.throws(()=>v.supplementPayload(record(),new Map([['created_date','2026-02-30']])) ,/有效日期/);
  core.state.permissions['visit.supplement']=false;assert.throws(()=>v.supplementPayload(record(),form),/未获/);
});
test('first-visit supplements retain type and require the allowed saved first-visit fields',async()=>{
  const {core,visits:v}=await modules();configure(core,{}, {'visit.supplement':true});
  const r=record({is_first_visit:true,customer_needs:'原需求',supplement_fields:['customer_needs','is_first_visit']});
  assert.deepEqual(v.supplementPayload(r,new Map([['customer_needs','补充后的需求'],['is_first_visit','false']])),{version_no:3,customer_needs:'补充后的需求'});
  assert.throws(()=>v.supplementPayload(r,new Map([['customer_needs','']])),/不能为空/);
  assert.throws(()=>v.supplementPayload({...r,is_first_visit:false},new Map([['customer_needs','其他']])),/不可补充/);
});
test('historical unknown dates are not fabricated from created_date when only a contact is supplemented',async()=>{
  const {core,visits:v}=await modules();configure(core,{}, {'visit.supplement':true});
  const r=record({interaction_at:null,visit_date:null,recorded_on:null,created_date:'2026-09-28',partner_name:'当前伙伴名称',partner_name_snapshot:'原伙伴快照'});
  assert.deepEqual(v.supplementPayload(r,new Map([['contact_name','新联系人'],['interaction_at',''],['created_date',''],['partner_name','原伙伴快照']])),{version_no:3,contact_name:'新联系人'});
  assert.deepEqual(v.supplementPayload(r,new Map([['created_date','2026-09-20']])),{version_no:3,created_date:'2026-09-20'});
});

async function browserHarness(check) {
  const http=require('node:http'),fs=require('node:fs/promises');
  const {chromium}=require(process.env.SALESBUDDY_PLAYWRIGHT_MODULE || 'playwright');
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><link rel="stylesheet" href="/assets/console.css"><main id="content" class="content"></main><dialog id="dialog" aria-labelledby="dialog-title"></dialog><div id="toast"></div></html>');return;}
    const name=req.url.replace('/assets/','');if(!/^[a-z-]+\.(js|css)$/.test(name)){res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'application/javascript');try{res.end(await fs.readFile(path.join(assets,name)));}catch(_){res.writeHead(404);res.end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  const requests=[],deferred={},overrides={};let failureOnce=false;
  try{
    browser=await chromium.launch({headless:true});const page=await browser.newPage({viewport:{width:1440,height:1100}});
    await page.route('**/api/**',async route=>{
      const request=route.request(),url=new URL(request.url()),body=request.postDataJSON();requests.push({path:url.pathname,query:Object.fromEntries(url.searchParams),method:request.method(),body});
      let payload;
      if(url.pathname.endsWith('/recorders')){
        if(url.searchParams.get('recorder_q')==='迟到'){deferred.recorder=route;return;}
        payload=url.searchParams.get('offset')==='100'?{items:[{id:'old',name:'历史同事',account_code:'OLD',active:false,record_count:8}],unmapped_count:2,has_more:false}:{items:[{id:'me',name:'当前跟进人',account_code:'SELF',active:true,record_count:20}],unmapped_count:2,has_more:true,next_offset:100};
      }else if(url.pathname==='/api/v1/console/visits')payload=result({next_offset:Number(url.searchParams.get('offset')||0)+50,items:[record(),record({id:'v2',subject_kind:'partner',subject_name:'演示伙伴乙',customer_id:null,customer_name:null,partner_id:'p2',partner_name:'演示伙伴乙',original_recorder_name:'历史同事',manager_name:'当前管理人',follow_up_score:null})]});
      else if(url.pathname==='/api/v1/console/visits/slow'){deferred.detail=route;return;}
      else if(url.pathname==='/api/v1/console/visits/v2')payload=record({id:'v2',subject_kind:'partner',subject_name:'演示伙伴乙',customer_id:null,customer_name:null,partner_id:'p2',partner_name:'演示伙伴乙',original_recorder_name:'历史同事',manager_name:'当前管理人',can_supplement:false,...overrides.v2});
      else if(url.pathname==='/api/v1/console/visits/v1')payload=record(overrides.v1);
      else if(url.pathname==='/api/v1/visits/v1' && request.method()==='PATCH'){
        if(failureOnce){failureOnce=false;await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'记录已更新，请刷新后再补充'})});return;}
        payload=record({...body,version_no:4});
      }else if(url.pathname==='/api/v1/visit-imports/file1/original'){deferred.download=route;return;}
      else throw Error('Unexpected route: '+url.pathname);
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async()=>{
      const core=await import('/assets/core.js'),visits=await import('/assets/visits.js');window.core=core;window.visitsModule=visits;window.renders=0;
      Object.assign(core.state,{actor:{workspace_id:'w',user_id:'me',role:'administrator'},token:'fixture',company:{id:'w'},page:'visits',offset:0,filters:{},permissions:{'visit.read':true,'visit.supplement':true,'visit.download_original':true},refresh:async()=>{
        const generation=++window.renders,view=await visits.visits({isCurrent:()=>generation===window.renders});if(generation!==window.renders)return;const root=document.querySelector('#content');root.innerHTML=view.html;view.bind(root);
      }});await core.state.refresh();
    });
    await check(page,requests,{deferred,failPatch:()=>{failureOnce=true;},setRecord:(id,values)=>{overrides[id]=values;}});
  }finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
}
const browserOptions={skip:process.env.SALESBUDDY_BROWSER_DOM_TESTS!=='1'};
test('real DOM: follow-up filters and recorder pagination preserve full query, reset page, and reject reversed ranges',browserOptions,async()=>browserHarness(async(page,requests)=>{
  await page.getByRole('button',{name:'我的跟进',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-visit-scope=mine]').getAttribute('aria-pressed')==='true');
  await page.locator('input[name=q]').fill('接口');await page.locator('select[name=date_basis]').selectOption('created');await page.locator('input[name=score_min]').fill('0');await page.getByRole('button',{name:'筛选',exact:true}).click();await page.waitForFunction(()=>window.core.state.filters.q==='接口' && document.querySelector('input[name=q]').value==='接口');
  await page.locator('#visits-next').click();await page.waitForFunction(()=>document.querySelector('.pager').textContent.includes('第 2 页'));assert.equal(requests.at(-1).query.offset,'50');
  await page.locator('#visit-recorder-filter').click();await page.locator('#visit-recorder-next').click();await page.getByRole('button',{name:/历史同事/}).click();await page.waitForFunction(()=>document.querySelector('#visit-recorder-filter').textContent==='历史同事');
  assert.equal(requests.at(-1).query.recorder_id,'old');assert.equal(requests.at(-1).query.q,'接口');assert.equal(requests.at(-1).query.score_min,'0');assert.equal(requests.at(-1).query.date_basis,'created');assert.equal(requests.at(-1).query.offset,'0');
  await page.locator('input[name=date_from]').fill('2026-10-10');await page.locator('input[name=date_to]').fill('2026-10-01');const before=requests.length;await page.getByRole('button',{name:'筛选',exact:true}).click();assert.match(await page.locator('#visits-filter-error').textContent(),/不能晚于/);assert.equal(requests.length,before);
  await page.locator('#visits-reset').click();await page.waitForFunction(()=>Object.keys(window.core.state.filters).length===0 && document.querySelector('#visit-recorder-filter').textContent==='全部跟进人');assert.equal(requests.at(-1).query.scope,'all');
  assert.equal(requests.some(r=>r.method!=='GET'),false);
  if(process.env.SALESBUDDY_VISITS_UI_DIR)await page.screenshot({path:path.join(process.env.SALESBUDDY_VISITS_UI_DIR,'visits-list.png'),fullPage:true});
}));
test('real DOM: complete saved detail needs only visit.read, does not infer ownership or invoke AI, and slow detail cannot replace a newer one',browserOptions,async()=>browserHarness(async(page,requests,{deferred})=>{
  await page.locator('[data-id=v1]').click();await page.getByRole('heading',{name:'跟进详情',exact:true}).waitFor();
  const dialog=page.locator('#dialog');assert.match(await dialog.textContent(),/已讨论一期范围/);assert.match(await dialog.textContent(),/10月3日前/);assert.match(await dialog.textContent(),/下一步审核通过/);
  assert.equal(requests.some(r=>r.path.includes('advice') || r.method!=='GET'),false);
  if(process.env.SALESBUDDY_VISITS_UI_DIR)await page.screenshot({path:path.join(process.env.SALESBUDDY_VISITS_UI_DIR,'visits-detail.png'),fullPage:true});
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();
  await page.evaluate(()=>{window.core.state.permissions={'visit.read':true};});
  await page.locator('[data-id=v2]').click();await page.getByRole('heading',{name:'演示伙伴乙',exact:true}).waitFor();
  assert.match(await dialog.textContent(),/原始跟进人历史同事/);assert.match(await dialog.textContent(),/当前管理人当前管理人/);
  assert.equal(await page.locator('#visit-supplement').count(),0);assert.equal(await page.locator('[data-visit-original]').count(),0);
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();
  await page.evaluate(()=>{window.pendingDetail=window.visitsModule.showVisit('slow');});await page.waitForTimeout(20);
  await page.locator('[data-id=v2]').click();await page.getByRole('heading',{name:'演示伙伴乙',exact:true}).waitFor();
  await deferred.detail.fulfill({status:200,contentType:'application/json',body:JSON.stringify(record({subject_name:'不应覆盖'}))});await page.evaluate(()=>window.pendingDetail);
  assert.equal(await page.getByText('不应覆盖',{exact:true}).count(),0);
}));
test('real DOM: auxiliary edit retains input on conflict, sends a narrow keyed PATCH only on explicit save, and rechecks revoked permissions',browserOptions,async()=>browserHarness(async(page,requests,{failPatch})=>{
  await page.locator('[data-id=v1]').click();await page.locator('#visit-supplement').click();
  assert.equal(await page.locator('textarea[name=follow_up_record],textarea[name=next_action],input[name=customer_id]').count(),0);
  await page.locator('input[name=contact_name]').fill('新联系人');failPatch();await page.getByRole('button',{name:'保存补充',exact:true}).click();await page.locator('.error-box').waitFor();
  assert.equal(await page.locator('input[name=contact_name]').inputValue(),'新联系人');assert.match(await page.locator('.form-error').textContent(),/记录已更新/);
  const patch=requests.find(r=>r.method==='PATCH');assert.deepEqual(patch.body,{version_no:3,contact_name:'新联系人'});
  const before=requests.length;await page.evaluate(()=>{window.core.state.permissions['visit.supplement']=false;});await page.getByRole('button',{name:'保存补充',exact:true}).click();assert.equal(requests.length,before);assert.match(await page.locator('.form-error').textContent(),/未获/);
}));
test('real DOM: stale tenant recorder and original download responses produce no cross-company UI or download',browserOptions,async()=>browserHarness(async(page,requests,{deferred})=>{
  await page.locator('#visit-recorder-filter').click();await page.locator('#visit-recorder-search').fill('迟到');await page.locator('#visit-recorder-find').click();await page.waitForTimeout(20);
  await page.evaluate(()=>window.core.setCompany({id:'other'}));await deferred.recorder.fulfill({status:200,contentType:'application/json',body:JSON.stringify({items:[{id:'other',name:'不应显示',account_code:'X',record_count:1}],has_more:false})});await page.waitForTimeout(30);assert.equal(await page.getByText('不应显示',{exact:true}).count(),0);
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();
  await page.evaluate(async()=>{window.core.setCompany({id:'w'});await window.core.state.refresh();});await page.locator('[data-id=v1]').click();
  let downloaded=false;page.on('download',()=>{downloaded=true;});await page.locator('[data-visit-original]').click();await page.waitForTimeout(20);
  await page.evaluate(()=>window.core.clearSession());await deferred.download.fulfill({status:200,contentType:'application/octet-stream',body:'synthetic original'});await page.waitForTimeout(40);
  assert.equal(downloaded,false);assert.equal(requests.some(r=>r.method!=='GET'),false);
}));
test('real DOM: historical unknowns, human-entered date, principal-only opportunity and readonly reasons are explicit',browserOptions,async()=>browserHarness(async(page,requests,{setRecord})=>{
  setRecord('v2',{is_first_visit:null,recorded_on:null,created_date:'2026-09-28',interaction_at:null,visit_date:null,linked_opportunities:[],opportunity_id:'op1',opportunity_name:'仅主关联商机',original_materials:[],original_material_status:'unavailable',supplement_unavailable_reason:'仅原跟进人可补充辅助信息'});
  await page.locator('[data-id=v2]').click();const dialog=page.locator('#dialog');
  await page.getByRole('heading',{name:'演示伙伴乙',exact:true}).waitFor();
  assert.match(await dialog.textContent(),/是否首次拜访未记录/);
  assert.match(await dialog.textContent(),/销售填写日期未记录/);
  assert.match(await dialog.textContent(),/关联商机仅主关联商机/);
  assert.match(await dialog.textContent(),/原始材料当前不可下载/);
  assert.match(await dialog.textContent(),/仅原跟进人可补充辅助信息/);
  assert.equal(await page.locator('#visit-supplement,[data-visit-original]').count(),0);
  await page.getByRole('button',{name:'关闭',exact:true}).last().click();
  setRecord('v1',{recorded_on:null,created_date:'2026-09-28',interaction_at:null,visit_date:null,original_materials:[],original_material_status:'none',partner_name:'实时伙伴名',partner_name_snapshot:'历史伙伴快照',is_first_visit:true,customer_needs:null,supplement_fields:[...record().supplement_fields,'customer_needs']});
  await page.locator('[data-id=v1]').click();await page.getByText('此记录未关联原始材料。',{exact:true}).waitFor();
  await page.locator('#visit-supplement').click();
  assert.equal(await page.locator('input[name=created_date]').inputValue(),'');
  assert.equal(await page.locator('input[name=interaction_at]').inputValue(),'');
  assert.equal(await page.locator('input[name=partner_name]').inputValue(),'历史伙伴快照');
  assert.equal(await page.locator('input[type=date][required]').count(),0);
  assert.equal(await page.locator('textarea[name=customer_needs]').inputValue(),'');
  assert.equal(await page.locator('textarea[name=customer_needs][required]').count(),0);
  await page.locator('input[name=contact_name]').fill('仅补充联系人');
  await page.getByRole('button',{name:'保存补充',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('#dialog').open);
  assert.deepEqual(requests.find(r=>r.method==='PATCH').body,{version_no:3,contact_name:'仅补充联系人'});
}));
