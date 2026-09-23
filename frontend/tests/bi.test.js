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

function pageWith(raw) {
  let page;
  const toasts = [];
  const filename = path.resolve(__dirname, '../miniprogram/pages/bi/index.js');
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: value => { page = value; },
    require: name => name.endsWith('apiClient')
      ? {
        getDashboard: async personal => ({data_source:"database",scope:personal ? "self" : "workspace",opportunities:[],quarter_forecasts:[],quarter_actuals:[], ...raw}),
        getDashboardRankings: async query => ({contract_version:2,data_source:'database',scope:'peer',selection:{personal:true,member_id:'test-user',cohort_role:'sales',team_groups:['region:华北']},
          opportunity_acv:{rows:[{user_id:'test-user',name:'测试销售',value:200000,record_count:2,customer_count:2,rank:3,population:5}],groups:[],cohort:'team_sales'},
          region:{rows:[{code:'region:华北',name:'华北',value:40000,record_count:2,customer_count:1,rank:1}],groups:[]},
          followup:{rows:[{user_id:'test-user',name:'测试销售',value:1,record_count:1,customer_count:1,rank:3,population:5}],groups:[]},active_opportunities:{rows:[{value:1}]}}),
        getDirectoryMembers: async () => ({items:[{id:'test-user',name:'测试销售',role:'sales',team_id:'north-east',team:'北区东区'}]}),
      }
      : require(path.resolve(path.dirname(filename), name)),
    Date, Set, Map,
    getApp: () => ({ globalData: { role: 'sales', session: { userId: 'test-user', userName: '测试销售' } }, ensureLogin: () => true }),
    wx: { showNavigationBarLoading() {}, hideNavigationBarLoading() {}, showToast: value => toasts.push(value.title) },
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = values => Object.assign(page.data, values);
  page.toasts = toasts;
  return page;
}

async function load(raw) {
  const page = pageWith(raw);
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
    quarter_forecasts: [{ year, quarter, owner_name: '测试销售', weighted_recognized_amount: 60000, weighted_collection_amount: 0 }],
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
    { year: pastYear, quarter: 1, owner_name: '测试销售', weighted_recognized_amount: 50000, weighted_collection_amount: null },
    { year: pastYear, quarter: 1, owner_name: '测试销售', weighted_recognized_amount: 20000, weighted_collection_amount: null },
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
    quarter_forecasts:[{year,quarter,recognized_amount:600,collection_amount:800,weighted_recognized_amount:300,weighted_collection_amount:400}]});
  assert.equal(page.data.kpis[0].value, '¥0');
  assert.equal(page.data.kpis[1].value, '未登记');
  assert.equal(page.data.kpis[2].value, '¥300');
  assert.equal(page.data.kpis[3].value, '¥400');
});

test('服务异常不显示空数据看板', async () => {
  const page = await load({data_source:'invalid'});
  assert.ok(page.data.loadError);
  assert.equal(page.data.kpis.length,0);
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

test('默认本年累计、季度多选汇总所有图表与排名，重置恢复Q1至当前季度',async()=>{
 const rows=[1,2,3,4].map(q=>({id:`q${q}`,customer_id:'c1',owner_id:'test-user',owner_name:'测试销售',team_name:'北区',partner_name:'渠道甲',product_line:'产品A',region_name:'华北',amount:q*10000,probability:70,status:'open',expected_close_date:`${year}-${String(q*3).padStart(2,'0')}-15`}));
 const page=await load({opportunities:rows,quarter_forecasts:[1,2,3,4].map(q=>({year,quarter:q,weighted_recognized_amount:q*1000,weighted_collection_amount:q*100})),quarter_actuals:[1,2,3,4].map(q=>({year,quarter:q,recognized_amount:q*2000,recognized_count:q,collection_amount:q*500,collection_count:1}))});
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
 assert.equal(page.data.selectedQuarterKeys.length,1);assert.equal(page.toasts.at(-1),'请至少选择一个季度');
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
 const page=await load({opportunities:[{id:'ten',owner_id:'test-user',amount:100000,probability:10,status:'open',expected_close_date:closeDate},{id:'thirty',owner_id:'test-user',amount:200000,probability:30,status:'open',expected_close_date:closeDate}],quarter_forecasts:[{year,quarter,weighted_recognized_amount:0,weighted_collection_amount:null}]});
 assert.equal(page.data.kpis[5].value,'¥70,000');
 assert.equal(page.data.kpis[2].value,'¥0');assert.equal(page.data.kpis[3].value,'未填写');
});
