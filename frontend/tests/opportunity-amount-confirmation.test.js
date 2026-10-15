require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const opportunity = require('../miniprogram/utils/opportunity');
const amounts = require('../miniprogram/utils/opportunityAmount');
const ai = require('../miniprogram/utils/visitOpportunityAI');
const {draftScope} = require('../miniprogram/utils/draftScope');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {let resolve, reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const policy = (threshold=1000) => ({opportunity_amount:{id:null,version:null,source:'baseline',definition:{schema_version:1,warning_threshold_wan:threshold}}});
function setData(values) {
  for (const [key,value] of Object.entries(values)) {
    const parts=key.split('.');let target=this.data;
    for (const part of parts.slice(0,-1)) target=target[part] || (target[part]={});
    target[parts.at(-1)]=value;
  }
}
function harness(name, overrides={}) {
  const session={workspaceId:'amount-company',userId:'seller',userName:'测试销售',role:'sales',permissionVersion:1,loginAt:'one'};
  const app={ensureLogin:()=>true,guardPage:()=>true,globalData:{session}};
  const modals=[],storage=new Map(),posts=[],quality=[],policies=[];
  const api={
    getPendingOpportunitySave:()=>null,canRetryOpportunitySave:()=>false,
    request:async()=>({items:[]}), listOpportunities:async()=>({items:[],has_more:false,next_offset:null}),
    checkOpportunityName:async()=>({available:true}),
    getOpportunityMaterials:async id=>({opportunity_id:id,version_no:1,items:[],retired_items:[]}),
    getCompanyPresentation:async()=>{policies.push(session.workspaceId);return policy();},
    submitVisitStage:async(stage,payload)=>{quality.push(structuredClone(payload));return {run_id:'quality'};},
    waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:{follow_up_score:85,next_action:{passed:true}}}}),
    createVisit:async(customer,fields)=>{posts.push({customer,fields:structuredClone(fields)});return {id:'visit',fields:{}};},
    createOpportunity:async(customer,payload)=>{posts.push({customer,payload:structuredClone(payload)});return {id:payload.opportunity_id||'00000000-0000-4000-8000-000000000001',changed:true,event_id:'00000000-0000-4000-8000-000000000002',version_no:1};},
    requestBusinessAdvice:async()=>({id:'advice'}), ...overrides,
  };
  const wx={getStorageSync:k=>structuredClone(storage.get(k)),setStorageSync:(k,v)=>storage.set(k,structuredClone(v)),removeStorageSync:k=>storage.delete(k),
    showModal:value=>modals.push(value),showToast(){},setNavigationBarTitle(){},pageScrollTo(){},navigateBack(){}};
  function load(relative,component=false) {
    const filename=path.resolve(__dirname,'../miniprogram',relative);let definition;
    vm.runInNewContext(fs.readFileSync(filename,'utf8'),{
      Page:d=>definition=d,Component:d=>definition=d,getApp:()=>app,wx,
      require:n=>n.endsWith('/apiClient')?api:require(path.resolve(path.dirname(filename),n)),
      setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},
    });
    return component ? {...definition.methods,data:structuredClone(definition.data),setData} : {...definition,data:structuredClone(definition.data),setData};
  }
  const page=load(`pages/${name}/index.js`);
  function component(existing=null,savedDraft=null,visit=false) {
    const form=load('components/opportunity-form/index.js',true);
    form.properties={customerId:'customer',existing,savedDraft,visitContext:visit};
    form.triggerEvent=(_,detail)=>{if(visit)page.changeOpportunityForm({detail});};
    if(existing)api.getOpportunityMaterials=async id=>({opportunity_id:id,version_no:existing.version_no,items:[],retired_items:[]});
    form.reset();page.selectComponent=()=>form;return form;
  }
  return {page,component,app,session,modals,storage,posts,quality,policies,api};
}
function standalone(wan='1000',overrides={},existing=null) {
  const h=harness('opportunity-create',overrides);
  h.page.setData({customerId:'customer',customerName:'测试客户',existing});h.page.opportunityId=existing ? existing.id : '';
  h.form=h.component(existing);Object.assign(h.form.data.form,{name:'测试商机',amount:wan,stageIndex:0,expected_close_date:'2026-12-31',partner_mode:'direct'});
  return h;
}
async function visit(rawAmount, overrides={}) {
  const h=harness('visit-confirm',overrides),scope=draftScope(h.session);
  h.storage.set(`visitStructuredV2:${scope}`,{draftId:'entry',runId:'structure',customerHintId:'customer',customerHint:'测试客户',
    result:{fields:{follow_up_record:'确认试点',next_action:'9月30日销售提交方案',contact_name:'客户经理'}},
    opportunitySuggestion:{opportunity_draft:{action:'create',name:'测试商机',amount:rawAmount,probability:10,status:'open',expected_close_date:'2026-12-31',partner_name:'直销'}}});
  h.page.onLoad({});await tick();
  assert.equal(h.page.data.opportunityId,'__new__');
  h.form=h.component(null,h.page.data.opportunityDraft,true);
  return h;
}

test('万元按十进制移位生成同一元值，六位小数精确到分，完整元与量级展示不丢精度',()=>{
  for(const [wan,yuan] of [['0.000001',0.01],['0.0001',1],['0.12',1200],['1.234567',12345.67],['999.999999',9999999.99],['999.9999',9999999],['1000',10000000],['100000',1000000000],['900719925474.0991',9007199254740991]]) {
    assert.equal(opportunity.amount(wan,true),yuan);assert.equal(opportunity.wan(yuan),wan);
  }
  assert.match(amounts.previewWan('0.12'),/1,200 元（0.12 万元）/);
  assert.match(amounts.previewWan('100000'),/1,000,000,000 元（10 亿元）/);
  assert.match(amounts.previewWan('900719925474.0991'),/9,007,199,254,740,991 元（90,071,992.54740991 亿元）/);
  assert.equal(amounts.previewWan(''), '');assert.equal(amounts.previewWan('bad'),'');
  assert.throws(()=>opportunity.amount('900719925474.0992'),/无法精确保存/);
  assert.throws(()=>opportunity.amount('900719925474.099101'),/无法精确保存/);
  assert.throws(()=>opportunity.amount('1.2345678'),/最多六位小数/);
  assert.match(amounts.previewWan('1.234567'),/12,345.67 元（1.234567 万元）/);
});

test('提醒包含阈值本身并使用公司策略，历史原金额不需要策略或旧确认',async()=>{
  let reads=0;const api={getCompanyPresentation:async()=>{reads++;return policy(1000);}};
  assert.equal((await amounts.assess(api,{action:'create',amount:9999999})).required,false);
  assert.equal((await amounts.assess(api,{action:'create',amount:10000000})).required,true);
  assert.equal((await amounts.assess({getCompanyPresentation:async()=>policy(2000)},{action:'create',amount:10000000})).required,false);
  const old={id:'00000000-0000-4000-8000-000000000003',amount:'10000000.00'};
  const unchanged=await amounts.assess(api,{action:'update',opportunity_id:'00000000-0000-4000-8000-000000000003',amount:10000000,amount_confirmed_value:10000000},old);
  assert.equal(reads,2);assert.equal(unchanged.required,false);assert(!('amount_confirmed_value' in amounts.acknowledged(unchanged)));
  assert.equal((await amounts.assess(api,{action:'update',opportunity_id:'00000000-0000-4000-8000-000000000003',amount:10000000},{...old,amount:null})).required,true);
  // Comparing via Number would round away the historical .01 and miss this change.
  assert.equal((await amounts.assess(api,{action:'update',opportunity_id:'00000000-0000-4000-8000-000000000003',amount:9007199254740991},{...old,amount:'9007199254740991.01'})).required,true);
  await assert.rejects(amounts.assess(api,{action:'create',amount:'9007199254740991.01'}),/无法精确保存/);
});

for(const invalid of [null,{},policy(0),policy(1.5),policy('1000'),{opportunity_amount:{definition:{schema_version:2,warning_threshold_wan:1000}}}]) {
  test(`无有效公司策略时阻止保存且不假设默认阈值 ${JSON.stringify(invalid)}`,async()=>{
    const h=standalone('1000',{getCompanyPresentation:async()=>invalid});await tick();await h.page.submit();
    assert.equal(h.posts.length,0);assert.equal(h.modals.length,0);assert.equal(h.form.data.form.amount,'1000');assert.match(h.page.data.error,/规则加载失败.*重试/);assert.equal(h.page.data.busy,false);
  });
}

test('独立商机大额确认可保存，重复点保存和重复确认不重复 POST，确认值就是元金额',async()=>{
  const h=standalone('100000');const submitted=h.page.submit();await tick();h.page.submit();
  assert.equal(h.modals.length,1);assert.equal(h.posts.length,0);assert.match(h.modals[0].content,/100,000 万元\n即 1,000,000,000 元（10 亿元）/);
  h.modals[0].success({confirm:true});h.modals[0].success({confirm:true});await submitted;
  assert.equal(h.posts.length,1);assert.equal(h.posts[0].payload.amount,1000000000);assert.equal(h.posts[0].payload.amount_confirmed_value,1000000000);
  assert(!('amount_confirmed_value' in h.form.data.form));
});

test('取消保留表单；策略失败后重新保存重新取规则，不沿用旧 ack',async()=>{
  let count=0;const h=standalone('1000',{getCompanyPresentation:async()=>{if(++count===1)throw Error('offline');return policy(count===2?1000:2000);}});
  h.form.data.form.amount_confirmed_value=10000000;await tick();await h.page.submit();assert.match(h.page.data.error,/规则加载失败/);
  const cancel=h.page.submit();await tick();h.modals[0].success({confirm:false});await cancel;assert.equal(h.posts.length,0);assert.equal(h.form.data.form.amount,'1000');
  await tick();await h.page.submit();assert.equal(count,3);assert.equal(h.modals.length,1);assert.equal(h.posts.length,1);assert(!('amount_confirmed_value' in h.posts[0].payload));
});

for(const [name,change] of [
  ['金额',h=>h.form.data.form.amount='1001'],['客户',h=>h.page.data.customerId='other'],['商机',h=>h.page.opportunityId='other'],
  ['账号',h=>h.session.userId='other'],['租户',h=>h.session.workspaceId='other'],['登录会话',h=>h.session.loginAt='two'],
  ['记录版本',h=>h.page.data.existing={id:'opportunity',version_no:2,amount:10000000}],['隐藏页面',h=>h.page.onHide()],
])test(`金额确认返回前${name}变化，不提交旧确认`,async()=>{
  const h=standalone();const submitted=h.page.submit();await tick();change(h);h.modals[0].success({confirm:true});await submitted;assert.equal(h.posts.length,0);
});

test('策略迟到时金额已修改不再弹旧金额确认；历史金额未变可直接保存其它修改',async()=>{
  const pending=deferred();const h=standalone('1000',{getCompanyPresentation:()=>pending.promise});const submitted=h.page.submit();await tick();h.form.data.form.amount='1001';pending.resolve(policy());await submitted;assert.equal(h.modals.length,0);assert.equal(h.posts.length,0);
  const old={id:'00000000-0000-4000-8000-000000000003',amount:10000000,name:'旧商机',probability:10,status:'open',version_no:2,sales_channel:'direct'};
  const edit=standalone('1000',{getCompanyPresentation:async()=>assert.fail('unchanged amount must not depend on policy')},old);await tick();await edit.page.submit();assert.equal(edit.posts.length,1);assert.equal(edit.modals.length,0);assert(!('amount_confirmed_value' in edit.posts[0].payload));
});

for(const [input,yuan,wan] of [[1200,1200,'0.12'],[9999,9999,'0.9999'],['1200元',1200,'0.12'],['1.2万元',12000,'1.2'],['12345.67',12345.67,'1.234567']]) {
  test(`真实拜访确认页从 AI ${input} 预填、质检到归档保持同一元金额`,async()=>{
    const h=await visit(input);assert.equal(h.page.data.opportunityDraft.amount,wan);assert.match(h.form.data.amountPreview,/元/);
    await h.page.review();assert.equal(h.page.data.canSubmit,true);assert.equal(h.quality[0].opportunity_mutation.amount,yuan);assert.equal(h.policies.length,0);assert.equal(h.modals.length,0);
    await h.page.archive();assert.equal(h.modals.length,1);h.modals[0].success({confirm:true});await tick();
    assert.equal(h.posts.length,1);assert.equal(h.posts[0].fields._opportunity_mutation.amount,yuan);assert(!('amount_confirmed_value' in h.posts[0].fields._opportunity_mutation));
  });
}

test('拜访大额只在最终归档合并确认，取消不保存，不把确认写进已质检数据或草稿',async()=>{
  const h=await visit(10000000);await h.page.review();assert.equal(h.modals.length,0);assert.equal(h.policies.length,0);
  // A historical/malicious draft acknowledgment cannot bypass fresh confirmation.
  h.page.data.reviewPayload.opportunity_mutation.amount_confirmed_value=10000000;
  await h.page.archive();assert.equal(h.modals.length,1);assert.match(h.modals[0].content,/10,000,000 元/);h.modals[0].success({confirm:false});assert.equal(h.posts.length,0);
  delete h.page.data.reviewPayload.opportunity_mutation.amount_confirmed_value;
  await h.page.archive();assert.equal(h.modals.length,2);h.modals[1].success({confirm:true});h.modals[1].success({confirm:true});await tick();
  assert.equal(h.posts.length,1);assert.equal(h.posts[0].fields._opportunity_mutation.amount_confirmed_value,10000000);
  assert(!('amount_confirmed_value' in h.page.data.reviewPayload.opportunity_mutation));assert(!('amount_confirmed_value' in h.quality[0].opportunity_mutation));
});

for(const [name,change] of [['金额',h=>h.page.data.opportunityDraft.amount='1001'],['客户',h=>h.page.data.customerId='other'],['商机',h=>h.page.data.opportunityId='other'],['质检',h=>h.page.data.reviewRunId='other'],['版本',h=>h.page.data.selectedOpportunity={id:'other',version_no:2}],['账号',h=>h.session.userId='other']]) {
  test(`拜访归档金额确认后${name}变化不保存，旧确认不能被复用`,async()=>{
    const h=await visit(10000000);await h.page.review();await h.page.archive();change(h);h.modals[0].success({confirm:true});await tick();assert.equal(h.posts.length,0);
  });
}

test('拜访策略加载失败保留有效质检和草稿，再次归档只重取配置',async()=>{
  let attempts=0;const h=await visit(10000000,{getCompanyPresentation:async()=>{if(++attempts===1)throw Error('offline');return policy();}});
  await h.page.review();await h.page.archive();assert.match(h.page.data.errorText,/规则加载失败/);assert.equal(h.page.data.reviewRunId,'quality');assert.equal(h.page.data.canSubmit,true);assert.equal(h.modals.length,0);
  await h.page.archive();assert.equal(h.modals.length,1);assert.equal(h.quality.length,1);assert.equal(attempts,2);
});

test('visitFlow 兼容导出也使用同一个 AI 金额适配器，显式万元与 action none 不退化',()=>{
  const flow=require('../miniprogram/utils/visitFlow');assert.equal(flow.normalizeOpportunitySuggestion,ai.normalizeOpportunitySuggestion);
  assert.equal(flow.normalizeOpportunitySuggestion({amount_wan:'80万元'}).amount,'80');assert.equal(ai.normalizeOpportunitySuggestion({action:'none',amount:1200}).hasContent,false);
});

for(const statusCode of [500,409])test(`FIN-R 独立商机保存${statusCode}保留草稿，未知锁定而确定目录拒绝可修正`,async()=>{
 let fail=true;const submitted=[];
 const h=standalone('80',{createOpportunity:async(customer,payload)=>{submitted.push(structuredClone(payload));if(fail)throw Object.assign(Error('保存暂时失败'),{statusCode});return {id:payload.opportunity_id||'00000000-0000-4000-8000-000000000001',changed:true,event_id:'00000000-0000-4000-8000-000000000002',version_no:1};}});
 const material=require('../miniprogram/utils/opportunityMaterials');
 h.form.data.form.material_lines=[{...material.fromCatalog({id:'sku',version_no:2,name:'服务',recognition_rule:'delivery'}),quantity:'1',unit_price:'200',usage:'self_use',expected_delivery_on:'2026-12-01'}];
 h.form.data.form.trade_relation='direct';h.form.data.form.quarters=[{year:2026,quarter:4,recognized:'80',collection:'31'}];
 h.form.data.materialPreview={result:{recognized_inclusive:'200.00',persisted:false},display:{summary:'含税参考：200.00 元'}};
 const before=structuredClone(h.form.data.form),preview=structuredClone(h.form.data.materialPreview);
 await h.page.submit();assert.match(h.page.data.error,/保存暂时失败/);assert.deepEqual(structuredClone(h.form.data.form),before);assert.deepEqual(structuredClone(h.form.data.materialPreview),preview);
 fail=false;
 if(statusCode===500){assert.equal(h.page.data.saveStatus,'unknown');await h.page.submit();assert.equal(submitted.length,1);return;}
 await h.page.submit();assert.equal(submitted.length,2);assert.equal(submitted[1].quarterly_forecasts[0].recognized_amount,800000);assert.equal(submitted[1].quarterly_forecasts[0].collection_amount,310000);
 assert.equal(submitted[1].material_lines[0].unit_price,'200');assert.equal(submitted[0].material_lines[0].catalog_version_no,2);assert.equal(submitted[1].material_lines[0].catalog_version_no,2);assert.equal(submitted[1].recognized_inclusive,undefined);assert.equal(submitted[1].actuals,undefined);
});

test('FIN-R-MINI-001 编辑商机提交冻结物料版本，不替换成刷新后的目录版本',async()=>{
 const existing={id:'00000000-0000-4000-8000-000000000003',version_no:5,name:'原商机',amount:'800000',probability:10,status:'open',sales_channel:'direct',expected_close_date:'2026-12-31'};
 const h=standalone('80',{},existing),material=require('../miniprogram/utils/opportunityMaterials');
 await tick();h.form.data.form.trade_relation='direct';
 h.form.data.form.material_lines=[material.lineFor({id:'saved-line',material_id:'sku',version_no:8,catalog_snapshot:{version_no:3,name:'冻结规则'},quantity:'1',unit_price:'200',usage:'self_use'})];
 h.form.data.materialCatalog=[{id:'sku',version_no:9,name:'目录新规则'}];
 await h.page.submit();assert.equal(h.posts.length,1);assert.equal(h.posts[0].payload.version_no,5);assert.equal(h.posts[0].payload.material_lines[0].id,'saved-line');assert.equal(h.posts[0].payload.material_lines[0].catalog_version_no,3);
});

test('FIN-R-MINI-001 拜访质检和正式归档带同一目录版本，409保留物料草稿和已完成质检',async()=>{
 const submissions=[];const h=await visit(800000,{createVisit:async(customer,fields)=>{submissions.push(structuredClone(fields));throw Object.assign(Error('物料目录版本已变化'),{statusCode:409});}});
 const material=require('../miniprogram/utils/opportunityMaterials');
 h.form.data.form.material_lines=[{...material.fromCatalog({id:'sku',version_no:3,name:'目录物料'}),quantity:'1',unit_price:'200',usage:'self_use'}];h.form.data.form.trade_relation='direct';h.form.emit();
 await h.page.review();assert.equal(h.quality[0].opportunity_mutation.material_lines[0].catalog_version_no,3);
 const before=structuredClone(h.form.data.form);await h.page.archive();h.modals[0].success({confirm:true});await tick();
 assert.equal(submissions.length,1);assert.equal(submissions[0]._opportunity_mutation.material_lines[0].catalog_version_no,3);assert.equal(h.page.data.archived,false);assert.equal(h.page.data.reviewRunId,'quality');assert.match(h.page.data.errorText,/目录版本/);assert.deepEqual(structuredClone(h.form.data.form),before);assert.equal(h.page.data.busy,false);
});

test('MINI121 目录409真实传输映射保留草稿，仅显式重选采用新目录，不自动重发',async t=>{
 const detail='物料目录已更新，请重新选择物料并重新计算后再保存';
 const generic='记录已被他人更新，请刷新后再试';
 const material=require('../miniprogram/utils/opportunityMaterials');
 const catalog={id:'sku',version_no:2,name:'目录新规则',recognition_rule:'delivery',is_active:true};
 const storage=new Map([['salesApiAuth',{access_token:'test-only',actor:{workspace_id:'w',user_id:'u'}}]]);
 const requests=[];let responseDetail=detail;
 const wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
   request:options=>{requests.push(options);options.success(options.method==='GET'
     ?{statusCode:200,data:{items:[catalog],total:1}}:{statusCode:409,data:{detail:responseDetail}});}};
 const previousWx=global.wx;global.wx=wx;t.after(()=>{global.wx=previousWx;});
 const filename=path.resolve(__dirname,'../miniprogram/utils/apiClient.js'),module={exports:{}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{module,wx,Map,Set,Date,
   require:name=>require(path.resolve(path.dirname(filename),name))});
 const transport=module.exports;
 for(const existing of [null,{id:'00000000-0000-4000-8000-000000000003',version_no:5,name:'原商机',amount:'800000',probability:10,status:'open',sales_channel:'direct',expected_close_date:'2026-12-31'}]){
   const h=standalone('80',{createOpportunity:transport.createOpportunity,listMaterials:transport.listMaterials,
     previewMaterialDraft:transport.previewMaterialDraft},existing);
   await tick();
   h.form.data.form.material_lines=[{...material.fromCatalog({...catalog,version_no:1}),quantity:'2',unit_price:'113',usage:'self_use',expected_delivery_on:'2026-12-01',note:'保留原输入'}];
   h.form.data.form.trade_relation='direct';h.form.data.form.quarters=[{year:2026,quarter:4,recognized:'81',collection:'92'}];
   const before=structuredClone(h.form.data.form),start=requests.length;
   await h.page.submit();await tick();
   assert.equal(h.page.data.error,detail);assert.equal(h.page.data.busy,false);
   assert.equal(requests.length,start+1);assert.equal(requests[start].method,'POST');
   assert.match(requests[start].url,/\/customers\/customer\/opportunities$/);
   assert.equal(requests[start].data.action,existing?'update':'create');
   assert.equal(requests[start].data.material_lines[0].catalog_version_no,1);
   assert.deepEqual(structuredClone(h.form.data.form),before);
   await h.form.previewMaterials();await tick();
   assert.equal(requests.length,start+2);assert.match(requests.at(-1).url,/\/material-forecast-preview$/);
   assert.ok(h.form.data.materialPreview.error.startsWith(detail));assert.equal(h.form.data.materialPreview.display,null);
   assert.deepEqual(structuredClone(h.form.data.form),before);
   // The existing picker refreshes only its directory; it never rewrites old lines.
   await h.form.openMaterials();assert.equal(requests.length,start+3);assert.equal(requests.at(-1).method,'GET');
   assert.deepEqual(structuredClone(h.form.data.form),before);
   h.form.chooseMaterial({currentTarget:{dataset:{id:'sku'}}});clearTimeout(h.form.materialPreviewTimer);
   assert.equal(requests.length,start+3);assert.equal(h.form.data.form.material_lines.length,2);
   assert.deepEqual(structuredClone(h.form.data.form.material_lines[0]),before.material_lines[0]);
   assert.equal(h.form.data.form.material_lines[1].catalog_snapshot.version_no,2);
   assert.deepEqual(structuredClone(h.form.data.form.quarters),before.quarters);
 }
 // The same transport keeps ordinary record conflicts and visit archive details.
 responseDetail=generic;let start=requests.length;
 await assert.rejects(transport.createOpportunity('customer',{action:'update',version_no:5}),e=>e.message===generic&&e.statusCode===409);
 assert.equal(requests.length,start+1);
 responseDetail=detail;start=requests.length;
 await assert.rejects(transport.createVisit('customer',{_opportunity_mutation:{material_lines:[]}}),e=>e.message===detail&&e.statusCode===409);
 assert.equal(requests.length,start+1);
});

for(const value of ['12345.67','10000000.67'])test(`历史含分金额 ${value} 回填六位万元小数，只改名称不改金额、不额外核对`,async()=>{
 const h=harness('opportunity-create',{getCompanyPresentation:async()=>assert.fail('unchanged legacy cents must not read the warning policy')});
 const old={id:'00000000-0000-4000-8000-000000000003',amount:value,name:'原商机',probability:10,status:'open',version_no:2,sales_channel:'direct',expected_close_date:'2026-12-31',quarterly_forecasts:[{year:2026,quarter:4,recognized_amount:'12345.67',collection_amount:'0.01'}]};
 h.page.setData({customerId:'customer',customerName:'测试客户',existing:old});h.page.opportunityId='00000000-0000-4000-8000-000000000003';
 h.form=h.component(old);assert.equal(h.form.data.form.amount,opportunity.wan(value));
 h.form.input({currentTarget:{dataset:{key:'name'}},detail:{value:'只修改名称'}});
 await tick();await h.page.submit();assert.equal(h.posts.length,1);assert.equal(h.modals.length,0);
 assert.equal(h.posts[0].payload.amount,Number(value));assert.equal(h.posts[0].payload.name,'只修改名称');
 assert.equal(h.posts[0].payload.quarterly_forecasts[0].recognized_amount,12345.67);assert.equal(h.posts[0].payload.quarterly_forecasts[0].collection_amount,0.01);
 assert(!('amount_confirmed_value' in h.posts[0].payload));
});
