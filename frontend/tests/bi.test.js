require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const now = new Date();
const year = now.getFullYear();
const quarter = Math.floor(now.getMonth() / 3) + 1;
const closeDate = `${year}-${String((quarter - 1) * 3 + 1).padStart(2, '0')}-15`;

function pageWith(raw, overrides={}) {
  let page;
  const toasts = [];
  const filename = path.resolve(__dirname, '../'+(overrides.source || 'miniprogram')+'/pages/bi/index.js');
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: value => { page = value; },
    require: name => name.endsWith('apiClient')
      ? {
        getDashboard: async personal => ({contract_version:4,forecast_basis:"latest_sales_input",data_source:"database",scope:personal ? "self" : "workspace",opportunities:[],quarter_forecasts:[],quarter_actuals:[], ...raw}),
        getDashboardRankings: async query => ({contract_version:2,data_source:'database',year:query.year,quarters:query.quarters,scope:'peer',selection:{personal:true,member_id:'test-user',cohort_role:'sales',team_groups:['region:华北']},
          opportunity_acv:{rows:[{user_id:'test-user',name:'测试销售',value:200000,record_count:2,customer_count:2,rank:3,population:5}],groups:[],cohort:'team_sales'},
          region:{rows:[{code:'region:华北',name:'华北',value:40000,record_count:2,customer_count:1,rank:1}],groups:[]},
          followup:{rows:[{user_id:'test-user',name:'测试销售',value:1,record_count:1,customer_count:1,rank:3,population:5}],groups:[]},active_opportunities:{rows:[{user_id:'test-user',value:1}]}}),
        getDirectoryMembers: async () => ({items:[{id:'test-user',name:'测试销售',role:'sales',team_id:'north-east',team:'北区东区'}]}),
        ...overrides.api,
      }
      : require(path.resolve(__dirname, '../miniprogram/pages/bi', name)),
    Date, Set, Map,
    getApp: () => ({ globalData: { role: 'sales', session: overrides.session || { userId: 'test-user', userName: '测试销售' } }, ensureLogin: () => true }),
    wx: { showNavigationBarLoading() {}, hideNavigationBarLoading() {}, showToast: value => toasts.push(value.title) },
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = values => Object.assign(page.data, values);
  page.toasts = toasts;
  return page;
}

async function load(raw, overrides={}) {
  const page = pageWith(raw,overrides);
  page.onShow();
  await new Promise(resolve => setImmediate(resolve));
  return page;
}

test('看板真实响应格式可完成加载，阶段分布与季度预测相互独立', async () => {
  const page = await load({
    opportunities: [
      { id: 'o1', customer_id: 'c1', name: '项目一期', owner_id: 'test-user', owner_name: '测试销售', amount: 120000, probability: 30, expected_close_date: closeDate },
      { id: 'o2', customer_id: 'c2', name: '项目二期', owner_id: 'test-user', owner_name: '测试销售', amount: 80000, probability: 70, expected_close_date: closeDate },
    ],
    quarter_forecasts: [{ year, quarter, owner_name: '测试销售', recognized_amount: 60000, collection_amount: 0 }],
  });
  assert.deepEqual(page.toasts, []);
  assert.equal(page.data.selectedQuarter.key, `${year}-Q${quarter}`);
  assert.equal(page.data.totalAcv, '¥200,000');
  assert.equal(page.data.funnel.find(row => row.probability === 30).value, 120000);
  assert.equal(page.data.funnel.find(row => row.probability === 70).value, 80000);
  assert.equal(page.data.rankingCards[0].rows[0].count, 2);
  // 区域榜独立读取，接口无需提供已移除的渠道伙伴榜。
  assert.equal(page.data.rankingMessage, '');
  assert.equal(page.data.rankingLoading, false);
  assert.equal(page.data.rankingCards[2].rows[0].name, '华北');
  assert.equal(page.data.rankingCards[2].rows[0].value, 40000);
  assert.equal(page.data.kpis[2].value, '¥60,000');
  assert.equal(page.data.kpis[3].value, '¥0');
});

test('只有预测的历史季度也能选择，多个预测不产生重复季度', async () => {
  const pastYear = year - 5;
  const page = await load({ quarter_forecasts: [
    { year: pastYear, quarter: 1, owner_name: '测试销售', recognized_amount: 50000, collection_amount: null },
    { year: pastYear, quarter: 1, owner_name: '测试销售', recognized_amount: 20000, collection_amount: null },
  ] });
  const matches = page.data.quarterOptions.filter(q => q.key === `${pastYear}-Q1`);
  assert.equal(matches.length, 1);
  page.changeQuarter({ detail: { value: page.data.quarterOptions.findIndex(q => q.key === `${pastYear}-Q1`) } });
  assert.deepEqual(page.toasts, []);
  assert.equal(page.data.kpis[2].value, '¥70,000');
  assert.equal(page.data.kpis[3].value, '未填写');
  assert.equal(page.data.totalAcv, '¥0');
  assert.equal(page.data.quarterEmpty, false);
});

test('缺少预测字段或完全空数据时正常显示空季度，不弹运行错误', async () => {
  for (const raw of [{}, { opportunities: [], quarter_forecasts: [] }]) {
    const page = await load(raw);
    assert.deepEqual(page.toasts, []);
    assert.equal(page.data.loading, false);
    assert.equal(page.data.quarterEmpty, true);
    assert.equal(page.data.kpis[2].value, '未填写');
    assert.equal(page.data.totalAcv, '¥0');
    assert.equal(page.data.visitDays.length, 7);
  }
});


test('看板实际0和未登记与预测分开展示', async () => {
  const page = await load({opportunities:[{id:'o1',customer_id:'c1',name:'AI中台CRM',amount:100,product_line:'智能语音',probability:50,expected_close_date:closeDate}],
    quarter_actuals:[{year,quarter,recognized_count:1,recognized_amount:0,collection_count:0,collection_amount:null}],
    quarter_forecasts:[{year,quarter,recognized_amount:600,collection_amount:800}]});
  assert.equal(page.data.kpis[0].value, '¥0');
  assert.equal(page.data.kpis[1].value, '未登记');
  assert.equal(page.data.kpis[2].value, '¥600');
  assert.equal(page.data.kpis[3].value, '¥800');
});

test('服务异常不显示空数据看板', async () => {
  const page = await load({data_source:'invalid'});
  assert.ok(page.data.loadError);
  assert.equal(page.data.kpis.length,0);
});

test('金额卡片与明细摘要保留分，不将小额实际或预测显示为零', async () => {
  const keys = ['actual_recognized_exclusive', 'actual_collection', 'forecast_recognized_inclusive', 'forecast_collection'];
  for (const [amount, expected] of [['0.49', '¥0.49'], [0.01, '¥0.01'], ['12345.67', '¥12,345.67'], [0, '¥0']]) {
    const page = await load({
      quarter_actuals: [{year, quarter, recognized_count:1, recognized_amount:amount, collection_count:1, collection_amount:amount}],
      quarter_forecasts: [{year, quarter, recognized_amount:amount, collection_amount:amount}],
    });
    for (const key of keys) {
      assert.equal(page.data.kpis.find(item => item.key === key).value, expected, key);
      assert.ok(page.data.metricDetails[key].summary.startsWith(`${expected} · `), key);
      if (amount === '0.49') assert.equal(page.data.metricDetails[key].rows[0].amount, '0.49元', key);
    }
  }
});

test('已登记小计和相邻商机金额保留分，零和缺失规则不变', async () => {
  const page = await load({
    quarter_actuals: [{year, quarter, recognized_count:1, recognized_amount:null,
      recognized_confirmed_subtotal:'0.49', recognized_complete:false, collection_count:0, collection_amount:null}],
    quarter_forecasts: [{year, quarter, recognized_amount:0, collection_amount:null}],
    opportunities: [{id:'cents-opportunity', customer_id:'cents-customer', amount:'0.49', probability:70, expected_close_date:closeDate}],
  });
  assert.equal(page.data.kpis[0].value, '¥0.49');
  assert.match(page.data.kpis[0].label, /已登记小计/);
  assert.equal(page.data.kpis[1].value, '未登记');
  assert.equal(page.data.kpis[2].value, '¥0');
  assert.equal(page.data.kpis[3].value, '未填写');
  assert.equal(page.data.totalAcv, '¥0.49');
  assert.equal(page.data.kpis[4].value, '¥0.49');
  assert.equal(page.data.kpis[5].value, '¥0.34');
  assert.equal(page.data.kpis[6].value, '¥0.49');
  assert.equal(page.data.timeline.find(row => row.count === 1).amount, '¥0.49');
});

test('个人跟进节奏仅使用本人记录，空商机季度仍有近7天排名，季度切换不改变次数', async () => {
  const page = await load({ recent_visits: [
    { id: 'v1', recorder_id: 'test-user', recorder_name: '测试销售', customer_id: 'c1', interaction_at: new Date().toISOString() },
    { id: 'v2', recorder_id: 'u2', recorder_name: '销售乙', customer_id: 'c2', interaction_at: new Date().toISOString() },
    { id: 'v3', recorder_id: 'u2', recorder_name: '销售乙', customer_id: 'c3', interaction_at: new Date().toISOString() },
  ] });
  assert.equal(page.data.quarterEmpty, true);
  assert.equal(page.data.rankingCards[1].rows[0].name, '测试销售');
  assert.equal(page.data.rankingCards[1].rows[0].count, 1);
  assert.equal(page.data.weeklyVisitCount, 1);
  page.changeQuarter({ detail: { value: 0 } });
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(page.data.rankingCards[1].rows[0].value, 1);
  assert.equal(page.data.rankingMessage, '');
  const missing = await load({});
  assert.equal(missing.data.rankingMessage,''); // Native rank API is independent from the seven-day detail array.
});

test('个人视图只使用服务端事实，无模拟数据入口', async () => {
  for (const opportunities of [[], [{id:'real-1', owner_id:'test-user', owner_name:'测试销售', amount:123456, expected_close_date:closeDate, probability:50, partner_name:'真实伙伴', attributes:{region_code:'真实区域'}}]]) {
    const page = await load({opportunities, recent_visits:[]});
    const before = JSON.stringify({raw:page._raw, sales:page.data.rankingCards[0].rows, regions:page.data.rankingCards[2].rows, followup:page.data.rankingCards[1], kpis:page.data.kpis, total:page.data.totalAcv});
    assert.equal(page.toggleRankingPreview, undefined);assert.equal(page.data.rankingSamples, undefined);
    assert.equal(page.data.rankingPreview, undefined);
    page.rebuild();
    assert.equal(JSON.stringify({raw:page._raw, sales:page.data.rankingCards[0].rows, regions:page.data.rankingCards[2].rows, followup:page.data.rankingCards[1], kpis:page.data.kpis, total:page.data.totalAcv}), before);
    assert.equal(page.toggleRankingPreview, undefined);assert.equal(page.data.rankingSamples, undefined);
    assert.equal(page.data.rankingPreview, undefined);
  }
});

test('默认本年累计，季度多选清空恢复全年，重置恢复Q1至当前季度',async()=>{
 const rows=[1,2,3,4].map(q=>({id:`q${q}`,customer_id:'c1',owner_id:'test-user',owner_name:'测试销售',team_name:'北区',partner_name:'渠道甲',product_line:'产品A',region_name:'华北',amount:q*10000,probability:70,status:'open',expected_close_date:`${year}-${String(q*3).padStart(2,'0')}-15`}));
 const page=await load({opportunities:rows,quarter_forecasts:[1,2,3,4].map(q=>({year,quarter:q,recognized_amount:q*1000,collection_amount:q*100})),quarter_actuals:[1,2,3,4].map(q=>({year,quarter:q,recognized_amount:q*2000,recognized_count:q,collection_amount:q*500,collection_count:1}))});
 const expected=quarter*(quarter+1)/2;
 assert.deepEqual(Array.from(page.data.selectedQuarterKeys),Array.from({length:quarter},(_,i)=>`${year}-Q${i+1}`));
 assert.equal(page.data.totalAcv,`¥${(expected*10000).toLocaleString('zh-CN')}`);
 page.changeQuarter({detail:{value:page.data.quarterOptions.findIndex(q=>q.key===`${year}-Q1`)}});
 page.toggleQuarter({currentTarget:{dataset:{key:`${year}-Q3`}}});
 assert.equal(page.data.selectedQuarter.label,`${year} Q1+Q3`);
 assert.equal(page.data.totalAcv,'¥40,000');
 assert.equal(page.data.kpis[0].value,'¥8,000');assert.equal(page.data.kpis[1].value,'¥2,000');
 assert.equal(page.data.kpis[2].value,'¥4,000');assert.equal(page.data.kpis[3].value,'¥400');
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(page.data.rankingCards[0].rows[0].rank,3);
 assert.equal(page.data.funnel.find(row => row.probability === 70).value,40000);assert.equal(page.data.rankingCards[2].rows[0].value,40000);
 assert.equal(page.data.timeline.length,6);assert.deepEqual(Array.from(page.data.timeline,x=>x.month),['1月','2月','3月','7月','8月','9月']);
 page.toggleQuarter({currentTarget:{dataset:{key:`${year}-Q1`}}});
 page.toggleQuarter({currentTarget:{dataset:{key:`${year}-Q3`}}});
 assert.deepEqual(Array.from(page.data.selectedQuarterKeys),[1,2,3,4].map(q=>`${year}-Q${q}`));
 assert.equal(page.data.selectedQuarter.isFullYear,true);assert.equal(page.data.totalAcv,'¥100,000');assert.deepEqual(page.toasts,[]);
 page.resetQuarters();assert.equal(page.data.opportunityCount,quarter);
 const selection=JSON.stringify(page.data.selectedQuarterKeys);page.loadData();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(JSON.stringify(page.data.selectedQuarterKeys),selection);
});

test('北京时间各季度及跨年边界默认累计范围正确',()=>{
 const {defaultQuarterKeys}=require('../miniprogram/utils/dashboardQuarters');
 for(let q=1;q<=4;q++)assert.deepEqual(defaultQuarterKeys(`2026-${String(q*3).padStart(2,'0')}-01`),Array.from({length:q},(_,i)=>`2026-Q${i+1}`));
 assert.deepEqual(defaultQuarterKeys('2026-03-31T16:00:00Z'),['2026-Q1','2026-Q2']);
 assert.deepEqual(defaultQuarterKeys('2026-12-31T16:00:00Z'),['2027-Q1']);
});

test('加权ACV计入10%商机，季度预测区分0与未填写',async()=>{
 const page=await load({opportunities:[{id:'ten',owner_id:'test-user',amount:100000,probability:10,status:'open',expected_close_date:closeDate},{id:'thirty',owner_id:'test-user',amount:200000,probability:30,status:'open',expected_close_date:closeDate}],quarter_forecasts:[{year,quarter,recognized_amount:0,collection_amount:null}]});
 assert.equal(page.data.kpis[5].value,'¥70,000');
 assert.equal(page.data.kpis[2].value,'¥0');assert.equal(page.data.kpis[3].value,'未填写');
});

test('拒绝旧预测契约，不悄悄把概率金额当原始预测',async()=>{
 for(const override of [{contract_version:3},{forecast_basis:'weighted'},{contract_version:null}]){
   const p=await load(override);assert.ok(p.data.loadError);assert.equal(p.data.kpis.length,0);
 }
});
test('赢单和丢单保存的预测仍取原值，与实际互不覆盖',async()=>{
 const p=await load({quarter_forecasts:[{year,quarter,status:'won',probability:100,recognized_amount:80,collection_amount:100},{year,quarter,status:'lost',probability:0,recognized_amount:20,collection_amount:0}],quarter_actuals:[{year,quarter,recognized_amount:50,collection_amount:100,recognized_count:2,collection_count:1,complete:true}]});
 assert.equal(p.data.kpis[0].value,'¥50');assert.equal(p.data.kpis[1].value,'¥100');assert.equal(p.data.kpis[2].value,'¥100');assert.equal(p.data.kpis[3].value,'¥100');
 assert.doesNotMatch(p.data.kpis[0].sub,/笔/);
});

test('INSIGHTS八项稳定key与同季度明细保持原金额口径，不伪造逐笔或跳转权',async()=>{
 const raw={opportunities:[{id:'o1',name:'本期在推',amount:100,probability:70,status:'open',expected_close_date:closeDate}],
  quarter_forecasts:[{id:'f1',opportunity_id:'won',opportunity_name:'已成单计划',year,quarter,recognized_amount:'0',collection_amount:null,status:'won',input_snapshot:{secret:'不可展示'}}],
  quarter_actuals:[{year,quarter,recognized_amount:null,recognized_confirmed_subtotal:'20',recognized_complete:false,recognized_count:0,recognized_incomplete_period_count:1,collection_amount:'0',collection_complete:true,collection_count:0}]};
 const before=JSON.stringify(raw),p=await load(raw),details=p.data.metricDetails;
 assert.deepEqual(Array.from(p.data.kpis,row=>row.key),['actual_recognized_exclusive','actual_collection','forecast_recognized_inclusive','forecast_collection','opportunity_acv','weighted_opportunity_acv','high_probability_acv','expected_close_count']);
 assert.match(details.actual_recognized_exclusive.rows[0].amount,/小计.*20/);assert.match(details.actual_recognized_exclusive.note,/季度汇总/);
 assert.equal(details.actual_collection.rows[0].amount,'0元');assert.equal(details.forecast_recognized_inclusive.rows[0].amount,'0元');assert.equal(details.forecast_collection.rows[0].amount,'未填写');
 assert.equal(details.opportunity_acv.rows[0].id,'o1');assert.equal(details.weighted_opportunity_acv.summary,p.data.kpis[5].value+' · '+p.data.kpis[5].sub);
 assert.ok(Object.values(details).every(d=>d.rows.every(row=>row.can_open===false)));assert.doesNotMatch(JSON.stringify(details),/不可展示|input_snapshot/);
 assert.equal(JSON.stringify(raw),before);assert.equal(p.data.quarterEmpty,false);
});

test('FIN-ACTUAL 页面采用同一次看板明细，关单在其他期间的授权商机仍显示名称且总卡不变',async()=>{
 const raw={contract_version:4,forecast_basis:'latest_sales_input',data_source:'database',scope:'self',
  opportunities:[{id:'actual-opp',name:'跨关单季度的实际商机',status:'open',amount:'999',probability:70,expected_close_date:`${year-1}-01-15`}],
  quarter_forecasts:[],quarter_actuals:[{year,quarter,recognized_amount:'40.03',recognized_confirmed_subtotal:'40.03',recognized_count:2,recognized_complete:true,
    collection_amount:null,collection_confirmed_subtotal:null,collection_count:0,collection_complete:false}],
  actual_details:['23.01','17.02'].map((amount,index)=>({id:`actual-${index}`,opportunity_id:'actual-opp',year,quarter,kind:'recognized',amount,
    complete:true,missing_material_count:0,tax_unresolved:false,tax_basis:'exclusive',status:'confirmed',source:'operations'})),
  detail_coverage:{actuals:{complete:true,count:2,basis:'same_authorized_effective_actual_rows'}}};
 let reads=0;
 const p=pageWith({}, {session:{role:'sales',userId:'test-user',permissions:{'dashboard.read':true}},
  api:{getDashboard:async()=>{reads+=1;return raw;},getOpportunity:()=>assert.fail('贡献明细不得逐商机读取')}});
 await p.onShow();
 const detail=p.data.metricDetails.actual_recognized_exclusive;
 assert.equal(reads,1);assert.equal(p.data.kpis[0].value,'¥40.03');assert.equal(p.data.totalAcv,'¥0');
 assert.equal(detail.summary,`${p.data.kpis[0].value} · ${p.data.kpis[0].sub}`);assert.equal(detail.coverageComplete,true);
 assert.equal(detail.rows.length,1);assert.equal(detail.rows[0].name,'跨关单季度的实际商机');assert.equal(detail.rows[0].amount,'40.03元');
 p.openMetricDetail({currentTarget:{dataset:{key:'actual_recognized_exclusive'}}});
 assert.equal(p.data.metricDetailVisible,true);assert.equal(reads,1);
 p.onHide();assert.equal(Object.keys(p.data.metricDetails).length,0);
});

test('INSIGHTS实际零、小计及质量状态即使count为0也不冒充空季度',async()=>{
 for(const actual of [{recognized_amount:'0',recognized_complete:true},{recognized_amount:null,recognized_confirmed_subtotal:'20',recognized_complete:false},{recognized_amount:null,recognized_tax_unknown_period_count:1}]){
  const p=await load({quarter_actuals:[{year,quarter,recognized_count:0,collection_count:0,...actual}]});
  assert.equal(p.data.quarterEmpty,false);assert.equal(p.data.metricDetails.actual_recognized_exclusive.rows.length,1);
  p.changeQuarter({detail:{value:p.data.quarterOptions.findIndex(row=>row.year===year&&row.quarter!==quarter)}});
  assert.equal(p.data.quarterEmpty,true);assert.equal(p.data.metricDetails.actual_recognized_exclusive.rows.length,0);
 }
});

test('INSIGHTS打开明细后切季度、身份、离页和失败读取不会残留旧明细',async()=>{
 const session={role:'sales',userId:'test-user',workspaceId:'w',permissionVersion:1,permissions:{'dashboard.read':true}};
 const p=pageWith({}, {session});await p.onShow();
 const event={currentTarget:{dataset:{key:'opportunity_acv'}}};
 p.openMetricDetail(event);assert.equal(p.data.metricDetailVisible,true);
 p.changeQuarter({detail:{value:0}});assert.equal(p.data.metricDetailVisible,false);
 p.openMetricDetail(event);assert.equal(p.data.metricDetailVisible,true);
 p.closeMetricDetail();session.userId='other';p.openMetricDetail(event);assert.equal(p.data.metricDetailVisible,false);
 p.onHide();assert.equal(Object.keys(p.data.metricDetails).length,0);
 let resolve;const late=pageWith({}, {session,api:{getDashboard:()=>new Promise(r=>resolve=r)}});
 const loading=late.loadFacts();late.onHide();resolve({contract_version:4,forecast_basis:'latest_sales_input',data_source:'database',scope:'self',opportunities:[],quarter_forecasts:[],quarter_actuals:[]});await loading;
 assert.equal(Object.keys(late.data.metricDetails).length,0);
 const failed=pageWith({}, {api:{getDashboard:async()=>{throw Error('读取失败');}}});failed.data.metricDetails={old:{}};await failed.onShow();assert.equal(Object.keys(failed.data.metricDetails).length,0);assert.match(failed.data.loadError,/读取失败/);
});

test('INSIGHTS近7天柱图与筛选共用北京时间，跨年与设备时区无关',()=>{
 const {recentVisits,sevenDayVisits}=require('../miniprogram/utils/followupRanking');
 const now='2026-12-31T16:10:00Z',visits=[{id:'today',interaction_at:'2026-12-31T16:00:00Z',status:'confirmed'},{id:'yesterday',interaction_at:'2026-12-31T15:59:59Z',status:'archived'},{id:'old',interaction_at:'2026-12-25T15:59:59Z',status:'confirmed'},{id:'draft',interaction_at:now,status:'draft'}];
 const rows=sevenDayVisits(recentVisits(visits,now),now);
 assert.equal(rows[0].key,'2026-12-26');assert.equal(rows[6].key,'2027-01-01');assert.equal(rows[6].weekday,'周五');assert.equal(rows[6].count,1);assert.equal(rows[5].count,1);assert.equal(rows.reduce((s,r)=>s+r.count,0),2);
});

test('INSIGHTS活跃商机沿正式跟进口径，排名无权/期间错配/缺行时不补0',async()=>{
 const p=await load({});assert.equal(p.data.activeOpportunityCount,1);
 p._nativeRankings.active_opportunities.rows=[];p.updateActiveCount();assert.equal(p.data.activeOpportunityCount,null);
 p._nativeRankings.active_opportunities.rows=[{user_id:'test-user',value:0}];p.updateActiveCount();assert.equal(p.data.activeOpportunityCount,0);
 p._nativeRankingKey='stale';p.updateActiveCount();assert.equal(p.data.activeOpportunityCount,null);
 const denied=pageWith({}, {session:{role:'sales',userId:'test-user',permissions:{'dashboard.read':true,'dashboard.ranking':false}}});await denied.onShow();assert.equal(denied.data.activeOpportunityCount,null);
 const mismatch=pageWith({}, {api:{getDashboardRankings:async q=>({contract_version:2,data_source:'database',scope:'peer',selection:{personal:true,member_id:'test-user'},year:q.year-1,quarters:q.quarters})}});
 await mismatch.onShow();assert.equal(mismatch.data.activeOpportunityCount,null);assert.match(mismatch.data.rankingMessage,/期间/);
});

test('INSIGHTS兼容服务端authorized范围标识，仍检查团队投影身份',async()=>{
 for(const selection of [{personal:false,team_groups:[]},{personal:true},null]){
  const p=pageWith({scope:'authorized',selection});Object.assign(p.data,{role:'manager',viewMode:'team',canViewTeam:true});
  await p.loadFacts();assert.equal(Boolean(p.data.loadError),!selection||selection.personal!==false);
 }
});

test('INSIGHTS万元以上实际、预测和ACV明细仍以元保留分，与同一卡片可精确核对', async () => {
  for (const value of ['10000.01','12345.67','123456.78','9999.99','0.49','0']) {
    const raw={quarter_actuals:[{year,quarter,recognized_amount:value,collection_amount:value,recognized_complete:true,collection_complete:true}],
      quarter_forecasts:[{year,quarter,recognized_amount:value,collection_amount:value}],
      opportunities:[{id:'precise-opportunity',name:'精确金额商机',amount:value,probability:70,status:'open',expected_close_date:closeDate}]};
    const before=JSON.stringify(raw),p=await load(raw),expected=Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2})+'元';
    for (const key of ['actual_recognized_exclusive','actual_collection','forecast_recognized_inclusive','forecast_collection','opportunity_acv','weighted_opportunity_acv','high_probability_acv','expected_close_count']) {
      const detail=p.data.metricDetails[key];
      assert.equal(detail.rows[0].amount,expected,`${key}: ${value}`);
      assert.equal(detail.rows[0].can_open,false);
      assert.equal(detail.summary,p.data.kpis.find(row=>row.key===key).value+' · '+p.data.kpis.find(row=>row.key===key).sub);
      assert.doesNotMatch(detail.rows[0].amount,/万/);
    }
    assert.equal(JSON.stringify(raw),before);
  }
});

test('INSIGHTS大额小计保持税未知提示、缺失与零，不改变全局紧凑格式', async () => {
  const p=await load({quarter_actuals:[{year,quarter,recognized_amount:null,recognized_confirmed_subtotal:'12345.67',recognized_complete:false,recognized_tax_unknown_period_count:1,collection_amount:null}],
    quarter_forecasts:[{year,quarter,recognized_amount:0,collection_amount:null}]});
  const details=p.data.metricDetails;
  assert.equal(details.actual_recognized_exclusive.rows[0].amount,'已登记小计 12,345.67元');
  assert.match(details.actual_recognized_exclusive.rows[0].quality,/税口径未确认/);
  assert.match(details.actual_recognized_exclusive.rows[0].quality,/已登记小计/);
  assert.equal(details.actual_collection.rows[0].amount,'未登记');
  assert.equal(details.forecast_recognized_inclusive.rows[0].amount,'0元');
  assert.equal(details.forecast_collection.rows[0].amount,'未填写');
  assert.equal(require('../miniprogram/utils/financialDisplay').money('12345.67'),'1.23万');
});

for(const source of ['miniprogram','web/source/miniprogram']) {
  test(source+' 看板缺 ACV 与未知阶段只展示已知小计，明确零和空范围保留',async()=>{
    const rows=[{id:'known',amount:'10.50',probability:70},{id:'missing',amount:null,probability:70},{id:'unknown-stage',amount:'2.50',probability:null}]
      .map(row=>({...row,customer_id:'c',status:'open',expected_close_date:closeDate}));
    const page=await load({opportunities:rows},{source});
    assert.equal(page.data.totalAcv,'已知小计 ¥13');assert.match(page.data.kpis[4].label,/已知小计/);
    assert.equal(page.data.kpis[5].value,'¥7.35');assert.match(page.data.kpis[5].sub,/1 个商机阶段待确认/);
    assert.equal(page.data.funnel.find(row=>row.probability===null).value,2.5);
    assert.match(page.data.funnel.find(row=>row.probability===70).note,/1 个商机 ACV 未填写/);
    assert.equal(page.data.timeline.find(row=>row.count===3).share,null);
    for(const amount of [null,0]) {
      const sample=await load({opportunities:[{...rows[0],amount}]},{source});
      assert.equal(sample.data.kpis[4].value,amount===null?'未填写':'¥0');
      assert.equal(sample.data.timeline.find(row=>row.count===1).value,amount===null?null:0);
    }
  });
  test(source+' 全部已填计划与高信心监控不同，未填行显示数量且不改变纳入金额',async()=>{
    const page=await load({quarter_forecasts:[{year,quarter,recognized_amount:'10.50',collection_amount:'20.50',status:'lost'},
      {year,quarter,recognized_amount:null,collection_amount:null,status:'open'}]},{source});
    assert.match(page.data.kpis[3].label,/已填回款计划.*已填小计/);assert.equal(page.data.kpis[3].value,'¥20.5');
    assert.match(page.data.kpis[3].sub,/1 条计划金额未填/);assert.match(page.data.metricDetails.forecast_collection.definition,/不筛回款信心/);
  });
  test(source+' 采用实际商机贡献明细接通并保留未知归属的完整性提示',async()=>{
    const page=await load({quarter_actuals:[{year,quarter,recognized_amount:null,recognized_confirmed_subtotal:'10.50',recognized_count:1,recognized_complete:false,recognized_attribution_unknown_period_count:1}],
      actual_details:[{id:'fact',opportunity_id:'history',year,quarter,kind:'recognized',amount:'10.50',complete:true,missing_material_count:0,tax_unresolved:false,tax_basis:'exclusive',source:'operations',status:'confirmed'}],
      detail_coverage:{actuals:{complete:true,count:1,basis:'same_authorized_effective_actual_rows'}}},{source});
    const detail=page.data.metricDetails.actual_recognized_exclusive;
    assert.equal(detail.granularity,'opportunity_period');assert.equal(detail.rows[0].amount,'10.5元');
    assert.match(detail.note,/1 项历史季度记录归属待核对/);assert.match(page.data.kpis[0].sub,/归属待核对/);
    const unknown=await load({quarter_actuals:[{year,quarter,recognized_amount:null,recognized_confirmed_subtotal:null,recognized_count:0,recognized_complete:false,recognized_attribution_unknown_period_count:1}]},{source});
    assert.equal(unknown.data.kpis[0].value,'归属待核对');
  });
}

test('经营看板共用金额和明细工具的Web镜像一致',()=>{
  for(const file of ['dashboardMetricDetails','financialDisplay','rankingDisplay','fdePresentation']) {
    assert.equal(fs.readFileSync(path.resolve(__dirname,`../web/source/miniprogram/utils/${file}.js`),'utf8'),
      fs.readFileSync(path.resolve(__dirname,`../miniprogram/utils/${file}.js`),'utf8'),file);
  }
});


test('季度监控默认收起，开关保留主看板与两套季度，关闭恢复明细遮罩状态', () => {
  const page = pageWith({});
  assert.equal(page.data.monitorOverviewOpen, false);
  page.data.selectedQuarterKeys = ['2025-Q2', '2025-Q3'];
  Object.assign(page.data, {monitorYear:2026, monitorQuarter:4,
    monitorForecasts:[{code:'forecast_collection', amount:'90.00', amountLabel:'已知小计'}],
    showQuarterFilter:true, metricDetail:{title:'主看板明细'}, metricDetailVisible:true});
  page.loadFacts = page.loadMonitor = page.loadRankingData = () => assert.fail('折叠不应发起请求');
  let overlayClosed = false;
  page.monitorDetailVisibility = open => { overlayClosed = open === false; };
  page.toggleMonitorOverview();
  assert.equal(page.data.monitorOverviewOpen, true);
  page.data.monitorDetail = {code:'forecast_collection'};
  page.toggleMonitorOverview();
  assert.equal(page.data.monitorOverviewOpen, false);
  assert.equal(overlayClosed, true);
  assert.equal(page.data.monitorDetail, null);
  assert.equal(page.data.showQuarterFilter, true);
  assert.equal(page.data.metricDetailVisible, true);
  assert.equal(page.data.metricDetail.title, '主看板明细');
  assert.equal(page.data.monitorForecasts[0].amountLabel, '已知小计');
  assert.deepEqual(page.data.selectedQuarterKeys, ['2025-Q2', '2025-Q3']);
  assert.equal(page.data.monitorYear, 2026);
  assert.equal(page.data.monitorQuarter, 4);
});
