require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {normalizeCustomerDetail} = require('../miniprogram/utils/customerDetail');
const {opportunityContext} = require('../miniprogram/utils/opportunityAdvice');

function setup(api, name='visit-detail') {
  const filename=path.resolve(__dirname,`../miniprogram/pages/${name}/index.js`);
  const app={ensureLogin:()=>true,globalData:{session:{workspaceId:'w',userId:'u',role:'sales',permissionVersion:1}}};
  const urls=[],toasts=[];let page;
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{
    Page:value=>page=value,getApp:()=>app,
    require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),
    wx:{navigateTo:({url})=>urls.push(url),showToast:({title})=>toasts.push(title)},
  });
  page.data=JSON.parse(JSON.stringify(page.data));page.setData=(data,cb)=>{Object.assign(page.data,data);if(cb)cb();};
  const adviceMethod=page.loadVisitAdvice;
  page.loadVisitAdvice=()=>{};
  return {page,app,urls,toasts,adviceMethod};
}

test('跟进建议生成中离开再返回可重新加载，旧响应不能覆盖新请求',async()=>{
  const pending=[];
  const h=setup({queryBusinessAdvice:()=>new Promise(resolve=>pending.push(resolve))});
  h.page.loadVisitAdvice=h.adviceMethod;h.page.data.visitId='v';h.page.data.visit={id:'v'};
  const first=h.page.loadVisitAdvice();assert.equal(pending.length,1);
  h.page.onHide();
  const second=h.page.loadVisitAdvice();assert.equal(pending.length,2);
  pending[0]({id:'old',status:'succeeded',summary:'old',suggestions:[]});await first;
  assert.equal(h.page.data.visitAdvice.status,'loading');
  pending[1]({id:'new',status:'succeeded',summary:'new',suggestions:[]});await second;
  assert.equal(h.page.data.visitAdvice.summary,'new');
});

test('同账号权限版本变化后不展示旧跟进建议响应',async()=>{
  let resolve;
  const h=setup({queryBusinessAdvice:()=>new Promise(done=>resolve=done)});
  h.page.loadVisitAdvice=h.adviceMethod;h.page.data.visitId='v';h.page.data.visit={id:'v'};
  const request=h.page.loadVisitAdvice();h.app.globalData.session.permissionVersion=2;
  resolve({id:'permission-old',status:'succeeded',summary:'old permissions',suggestions:[]});await request;
  assert.notEqual(h.page.data.visitAdvice.summary,'old permissions');
});

test('历史跟进保留原作者与当前管理人，多商机仍是一条记录且分别关联',()=>{
  const visit={id:'v',customer_id:'c',recorder_name:'接收账号',original_recorder_name:'原作者',manager_name:'现管理人',partner_id:'p',partner_name:'原伙伴',
    linked_opportunities:[{id:'o1',name:'商机一'},{id:'o2',name:'商机二'},{id:'o1',name:'商机一'}]};
  const detail=normalizeCustomerDetail({id:'c',name:'客户',visits:[visit],opportunities:[{id:'o1'},{id:'o2'}]});
  assert.equal(detail.visits.length,1);assert.equal(detail.visits[0].owner,'原作者');assert.equal(detail.visits[0].managerName,'现管理人');
  assert.equal(detail.visits[0].originalRecorderName,'原作者');assert.equal(detail.visits[0].partnerName,'原伙伴');
  assert.equal(detail.visits[0].linkedOpportunities.length,2);assert.equal(detail.visits[0].opportunityName,'商机一、商机二');
  assert.ok(detail.opportunities.every(opportunity=>opportunity.relatedVisits.length===1));
  const context=opportunityContext({id:'c',name:'客户',opportunities:[{id:'o2'}],visits:[visit,{id:'other',linked_opportunities:[{id:'o3'}]}]},'o2');
  assert.deepEqual(context.visits.map(row=>row.id),['v']);
});

test('关单季度只显示原季度，明确日期优先；归属状态及回款信心度按原值显示',()=>{
  const detail=normalizeCustomerDetail({id:'c',name:'客户',opportunities:[{
    id:'q',expected_close_year:2026,expected_close_quarter:3,expected_close_date:null,original_owner_name:'原销售',ownership_resolution:'provisional',
    quarterly_forecasts:[{year:2026,quarter:3,recognized_amount:0,collection_amount:null,collection_confidence:'high'},
      {year:2026,quarter:4,recognized_amount:10000,collection_amount:0,collection_confidence:'low'},
      {year:2027,quarter:1,collection_confidence:null}],
  },{id:'d',expected_close_date:'2026-09-21',expected_close_year:2025,expected_close_quarter:1},
  {id:'unknown',expected_close_year:2026,expected_close_quarter:null,ownership_resolution:'unknown'}]});
  const [quarter,date,unknown]=detail.opportunities;
  assert.equal(quarter.expectedDate,'2026 Q3');assert.equal(date.expectedDate,'2026-09-21');assert.equal(unknown.expectedDate,'待确认');
  assert.equal(quarter.originalOwnerName,'原销售');assert.equal(quarter.ownershipResolutionText,'暂定归属');assert.equal(unknown.ownershipResolutionText,'');
  assert.equal(quarter.quarterDetails[0].recognized,'0元');assert.equal(quarter.quarterDetails[0].collection,'未填写');
  assert.deepEqual(quarter.quarterDetails.map(row=>row.collectionConfidenceText),['高（≥70%）','低（<70%）','']);
});

test('关联伙伴、转售伙伴和签单方式各读独立字段，未知不互相推断',()=>{
  const rows=normalizeCustomerDetail({id:'c',opportunities:[
    {id:'both',associated_partners:[{id:'a',name:'技术伙伴'},{id:'b',name:'联合伙伴'}],partner_id:'r',partner_name:'转售公司',sales_channel:'partner'},
    {id:'associated',associated_partners:[{id:'a',name:'技术伙伴'}],sales_channel:'direct'},
    {id:'resale',partner_id:'r',partner_name:'转售公司'},
    {id:'legacy',sales_channel:'direct',partner_name:'直销'},
    {id:'unknown'},
  ]}).opportunities;
  assert.deepEqual(rows.map(row=>[row.associatedPartnersText,row.resalePartnerText,row.signingMethodText]),[
    ['技术伙伴、联合伙伴','转售公司','伙伴转售'],['技术伙伴','未填写','客户直签'],
    ['未填写','转售公司','未填写'],['未填写','未填写','客户直签'],['未填写','未填写','未填写'],
  ]);
  assert.equal(rows[0].partner_id,'r');assert.equal(rows[0].associated_partners[0].id,'a');
});

test('历史季度原始记录原值原单位展示，零与未填写分开且不混入预测',()=>{
  const records=[
    {year:2026,quarter:2,kind:'recognized',raw_amount:'12.345600',source_unit:'wan_cny',tax_basis:'unknown',source_field:'Q2真实确收'},
    {year:2026,quarter:2,kind:'collection',raw_amount:0,source_unit:'wan_cny',tax_basis:'not_applicable',source_field:'Q2真实回款'},
    {year:2026,quarter:1,kind:'recognized',raw_amount:'12345.67',source_unit:'cny',tax_basis:'inclusive'},
    {year:2026,quarter:1,kind:'recognized',raw_amount:null,source_unit:'wan_cny',tax_basis:'exclusive'},
  ];
  const raw={id:'c',opportunities:[{id:'o',historical_period_actuals:records,
    quarterly_forecasts:[{year:2026,quarter:3,recognized_amount:10000,collection_amount:null}]}]};
  const before=JSON.stringify(raw);const row=normalizeCustomerDetail(raw).opportunities[0];
  assert.deepEqual(row.historicalPeriodRecords.map(record=>[record.period,record.kindText,record.amountText,record.taxBasisText]),[
    ['2026 Q2','确收','12.345600万元','含税口径未知'],['2026 Q2','回款','0万元','税口径不适用'],
    ['2026 Q1','确收','12345.67元','含税'],['2026 Q1','确收','未填写','不含税'],
  ]);
  assert.equal(row.historicalPeriodRecords[0].sourceFieldText,'Q2真实确收');
  assert.equal(row.quarterDetails.length,1);assert.equal(row.quarterDetails[0].label,'2026 Q3');
  assert.equal(row.quarterDetails[0].recognized,'1万');assert.equal(JSON.stringify(raw),before);
  assert.deepEqual(normalizeCustomerDetail({id:'c',opportunities:[{id:'old'}]}).opportunities[0].historicalPeriodRecords,[]);
});

test('详情有历史季度原值时，空的正式实绩仍为未登记',async()=>{
  const calls=[];const h=setup({getCustomerAssetQuarters:async params=>{
    calls.push(params);return {as_of:params.as_of,items:[],years:[]};
  }},'customer-assets');
  const opportunity=normalizeCustomerDetail({id:'c',opportunities:[{id:'o',historical_period_actuals:[
    {year:2026,quarter:2,kind:'recognized',raw_amount:9999,source_unit:'wan_cny',tax_basis:'unknown'},
  ]}]}).opportunities[0];
  Object.assign(h.page.data,{customerId:'c',opportunityId:'o',opportunity});
  await h.page.loadQuarterActuals();
  assert.equal(calls.length,1);assert.equal(calls[0].opportunity_id,'o');
  assert.equal(h.page.data.quarterRecognized,'未登记');assert.equal(h.page.data.quarterCollection,'未登记');
  assert.equal(h.page.data.quarterEntryCount,0);assert.equal(h.page.data.opportunity.historicalPeriodRecords.length,1);
});

test('两处已有商机详情均提供分离的伙伴字段和独立历史季度记录区',()=>{
  for(const page of ['customer-detail','customer-assets']) {
    const template=fs.readFileSync(path.resolve(__dirname,`../miniprogram/pages/${page}/index.wxml`),'utf8');
    for(const [label,field] of [['关联伙伴','associatedPartnersText'],['转售伙伴','resalePartnerText'],['签单方式','signingMethodText']]) {
      assert.ok(template.includes(`<text>${label}</text><label>{{opportunity.${field}}}</label>`));
    }
    assert.ok(template.includes('历史季度原始记录'));assert.ok(template.includes('wx:for="{{opportunity.historicalPeriodRecords}}"'));
    assert.ok(template.includes('{{item.amountText}} · {{item.taxBasisText}}'));assert.ok(template.includes('{{item.sourceFieldText}}'));
  }
});

test('列表展示和分组使用原年季度，没有具体日期不推算按日风险',()=>{
  const {decorateOpportunity}=require('../miniprogram/utils/opportunityListCard');
  const {groupOpportunitiesByQuarter}=require('../miniprogram/utils/opportunityQuarter');
  const row=decorateOpportunity({id:'quarter',expected_close_year:2026,expected_close_quarter:3,expected_close_date:null,status:'open',probability:50});
  assert.equal(row.closeLabel,'2026 Q3');assert.equal(row.closeText,'2026 Q3');assert.equal(row.expected_close_date,null);
  assert.equal(row.signal.detail,'季度计划');assert.match(row.signal.reason,/不判断按日逾期/);
  const groups=groupOpportunitiesByQuarter([row,{id:'unknown',expected_close_date:null}]);
  assert.equal(groups[0].key,'2026-Q3');assert.equal(groups[1].key,'undated');
});

test('销售商机主列表卡片与季度分组一致，季度精度不显示待确认日期',()=>{
  const h=setup({},'opportunities');h.page.pageRequest={};
  h.page.acceptPage({items:[{id:'q',customer_id:'c',status:'open',probability:50,expected_close_date:null,expected_close_year:2026,expected_close_quarter:3},
    {id:'d',customer_id:'c',status:'open',probability:50,expected_close_date:'2026-10-01'},
    {id:'unknown',customer_id:'c',status:'open',probability:50,expected_close_date:null}],summary:{total:3,open_amount:0},has_more:false,facets:{}},false);
  assert.equal(h.page.data.opportunities[0].closeLabel,'2026 Q3');assert.equal(h.page.data.opportunityGroups[0].key,'2026-Q3');
  assert.equal(h.page.data.opportunities[1].closeLabel,'2026-10-01');assert.equal(h.page.data.opportunities[2].closeLabel,'待确认');
});

test('仅visit_id即可读取伙伴独立跟进，不补造客户关系',async()=>{
  const h=setup({getVisit:async id=>({id,customer_id:null,partner_id:'partner',partner_name:'正式伙伴',original_recorder_name:'原作者',manager_name:'蒋磊',follow_up_record:'原文'})});
  h.page.onLoad({visit_id:'visit'});await h.page.loadVisit();
  assert.equal(h.page.data.error,'');assert.equal(h.page.data.customerId,'');assert.equal(h.page.data.visit.customerId,'');
  assert.equal(h.page.data.visit.partnerName,'正式伙伴');assert.equal(h.page.data.visit.title,'伙伴跟进记录');
});

test('无主客户或伙伴的跨客户多商机跟进可按visit_id打开，不借用页面客户名称',async()=>{
  const links=[{id:'o1',name:'客户一商机'},{id:'o2',name:'客户二商机'}];
  const h=setup({getVisit:async id=>({id,customer_id:null,partner_id:null,linked_opportunities:links,follow_up_record:'跨客户原始正文'})});
  h.page.data.customerName='上个页面客户';h.page.onLoad({visit_id:'multi'});await h.page.loadVisit();
  assert.equal(h.page.data.error,'');assert.equal(h.page.data.customerId,'');assert.equal(h.page.data.customerName,'');
  assert.equal(h.page.data.visit.customerName,'');assert.equal(h.page.data.visit.title,'关联商机跟进');
  assert.equal(h.page.data.visit.followUpRecord,'跨客户原始正文');assert.deepEqual(h.page.data.visit.linkedOpportunities,links);
});

test('传入客户仍校验主体，缺客户和伙伴的异常记录不伪造主体',async()=>{
  for(const [options,raw,expected] of [
    [{visit_id:'v',customer_id:'wrong'},{id:'v',customer_id:'actual'},/客户不匹配/],
    [{visit_id:'v'},{id:'v',customer_id:null,partner_id:null},/主体不完整/],
    [{visit_id:'v',customer_id:'wrong'},{id:'v',customer_id:null,partner_id:null,linked_opportunities:[{id:'o'}]},/客户不匹配/],
    [{visit_id:'v'},{id:'v',customer_id:null,partner_id:null,linked_opportunities:[{id:''},{id:' '},{name:'只有名称'}]},/主体不完整/],
  ]) {
    const h=setup({getVisit:async()=>raw});h.page.onLoad(options);await h.page.loadVisit();assert.match(h.page.data.error,expected);assert.equal(h.page.data.visit,null);
  }
});

test('商机相关跟进保留原主体，跨客户和伙伴跟进跳转不借用商机客户',()=>{
  const h=setup({},'customer-assets');
  const customer=normalizeCustomerDetail({id:'opportunity-customer',name:'商机客户',opportunities:[{id:'opp'}],visits:[
    {id:'cross',customer_id:'primary-customer',customer_name:'原客户',linked_opportunities:[{id:'opp',name:'关联商机'}]},
    {id:'partner',customer_id:null,partner_id:'partner-id',partner_name:'合作伙伴',linked_opportunities:[{id:'opp',name:'关联商机'}]},
    {id:'legacy',opportunity_id:'opp'},
  ]});
  Object.assign(h.page.data,{customerId:'opportunity-customer',relatedVisits:customer.visits});
  for(const id of ['cross','partner','legacy','unlisted'])h.page.openRelatedVisit({currentTarget:{dataset:{id}}});
  assert.deepEqual(h.urls,[
    '/pages/visit-detail/index?customer_id=primary-customer&visit_id=cross',
    '/pages/visit-detail/index?visit_id=partner',
    '/pages/visit-detail/index?visit_id=legacy',
  ]);
  assert.equal(customer.visits.find(visit=>visit.id==='partner').customerName,'');
  assert.equal(customer.visits.find(visit=>visit.id==='legacy').customerId,'');
  const unknownName=normalizeCustomerDetail({id:'opportunity-customer',name:'商机客户',visits:[{id:'v',customer_id:'other'}]}).visits[0];
  assert.equal(unknownName.customerId,'other');assert.equal(unknownName.customerName,'');
});

test('时间线仅按跟进ID读取；FDE本人归档按原主体读取并保留完整资料权限检查',()=>{
  const h=setup({},'customer-assets');
  Object.assign(h.page.data,{customerId:'context',progressEvents:[{key:'timeline',object_type:'visit',object_id:'v'}],fdeOwnVisits:[
    {id:'partner',customer_id:null,partner_id:'p',can_read_detail:true},
    {id:'cross',customer_id:'original',can_read_detail:true},
    {id:'restricted',customer_id:'original',can_read_detail:false},
  ]});
  h.page.openProgressEvent({currentTarget:{dataset:{key:'timeline'}}});
  for(const id of ['partner','cross','restricted','unlisted'])h.page.openFdeOwnVisit({currentTarget:{dataset:{id}}});
  assert.deepEqual(h.urls,[
    '/pages/visit-detail/index?visit_id=v',
    '/pages/visit-detail/index?visit_id=partner',
    '/pages/visit-detail/index?customer_id=original&visit_id=cross',
  ]);
  assert.deepEqual(h.toasts,['仅保留本人归档摘要，当前无完整资料权限']);
});

test('地图侧栏跟进跳转使用原主体且不接受未列出的记录ID',()=>{
  const h=setup({},'customers');
  h.page.data.selectedCustomer=normalizeCustomerDetail({id:'current',name:'当前客户',visits:[
    {id:'cross',customer_id:'original'}, {id:'partner',customer_id:null,partner_id:'p'},
  ]});
  for(const id of ['cross','partner','unlisted'])h.page.openVisitDetail({currentTarget:{dataset:{id}}});
  assert.deepEqual(h.urls,[
    '/pages/visit-detail/index?customer_id=original&visit_id=cross',
    '/pages/visit-detail/index?visit_id=partner',
  ]);
});

test('客户详情内展开按记录原主体校验，不把关联商机客户当作主主体',async()=>{
  const records={cross:{id:'cross',customer_id:'original'},partner:{id:'partner',customer_id:null,partner_id:'p'},wrong:{id:'wrong',customer_id:'wrong'}};
  const seen=[];const h=setup({getVisit:async id=>{seen.push(id);return records[id];}},'customer-detail');
  h.page.data.customerId='context';h.page.data.customer=normalizeCustomerDetail({id:'context',visits:[
    {id:'cross',customer_id:'original'}, {id:'partner',customer_id:null,partner_id:'p'}, {id:'wrong',customer_id:'original'},
  ]});
  h.page._reader={token:()=>1,current:()=>true};h.page._fullVisits={};h.page.renderCustomer=()=>{};
  for(const id of ['cross','partner']) {
    await h.page.toggleVisit({currentTarget:{dataset:{id}}});
    assert.equal(h.page.data.visitDetailState.error,'');assert.equal(h.page._fullVisits[id].id,id);
  }
  await h.page.toggleVisit({currentTarget:{dataset:{id:'wrong'}}});
  assert.equal(h.page.data.visitDetailState.error,'拜访记录不匹配');assert.equal(h.page._fullVisits.wrong,undefined);
  await h.page.toggleVisit({currentTarget:{dataset:{id:'unlisted'}}});assert.deepEqual(seen,['cross','partner','wrong']);
});

test('旧详情仍显示旧记录人和单商机，不显示空的新留痕字段',async()=>{
  const h=setup({getVisit:async()=>({id:'v',customer_id:'c',customer_name:'旧客户',recorder_name:'旧记录人',opportunity_id:'o',opportunity_name:'旧商机'})});
  h.page.onLoad({visit_id:'v',customer_id:'c'});await h.page.loadVisit();
  assert.equal(h.page.data.visit.owner,'旧记录人');assert.equal(h.page.data.visit.originalRecorderName,'');assert.equal(h.page.data.visit.managerName,'');
  assert.equal(h.page.data.visit.linkedOpportunities.length,1);assert.equal(h.page.data.visit.linkedOpportunities[0].id,'o');
});

test('多商机跳转读取每条商机真实客户，不沿用跟进主体或接受未关联ID',async()=>{
  const seen=[];const h=setup({getOpportunityDetailHeader:async id=>{seen.push(id);return {id:'other-customer',opportunities:[{id}]};}});
  h.page.data.customerId='visit-customer';h.page.data.visit={linkedOpportunities:[{id:'one'},{id:'two'}]};
  await h.page.openOpportunity({currentTarget:{dataset:{id:'two'}}});
  assert.equal(h.urls.length,1);assert.match(h.urls[0],/customer_id=other-customer&opportunity_id=two/);
  await h.page.openOpportunity({currentTarget:{dataset:{id:'not-linked'}}});assert.deepEqual(seen,['two']);
});

test('切换身份或离开详情后，不接受旧读取与旧商机跳转结果',async()=>{
  let resolveVisit;const h=setup({getVisit:()=>new Promise(resolve=>{resolveVisit=resolve;})});
  h.page.onLoad({visit_id:'v'});const pending=h.page.loadVisit();h.app.globalData.session.userId='new';
  resolveVisit({id:'v',customer_id:'c',customer_name:'旧身份客户'});await pending;assert.equal(h.page.data.visit,null);
  for(const change of [item=>item.page.onHide(),item=>item.app.globalData.session.permissionVersion++]) {
    let resolve;const link=setup({getOpportunityDetailHeader:()=>new Promise(done=>{resolve=done;})});
    link.page.data.visit={linkedOpportunities:[{id:'o'}]};const task=link.page.openOpportunity({currentTarget:{dataset:{id:'o'}}});
    change(link);resolve({id:'c',opportunities:[{id:'o'}]});await task;assert.equal(link.urls.length,0);assert.equal(link.toasts.length,0);
  }
});
