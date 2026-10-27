const test=require('node:test'),assert=require('node:assert/strict');
const monitor=require('../miniprogram/utils/dashboardMonitor');
const query={personal:true,member_id:null,team_groups:[],year:2026,quarter:4};
const envelope=(q=query)=>({selection:{...q,member_id:q.personal?q.member_id||'self':null},scope:{kind:q.personal?q.member_id&&q.member_id!=='self'?'member':'self':'team',label:'所选范围'},
  quarter:{year:q.year,quarter:q.quarter,time_progress:'0.25'},data_as_of:'2026-10-25T10:20:00Z'});
const overview=(q=query)=>({...envelope(q),contract_version:'dashboard.metrics.v1',values_basis:'current_authorized_quarter_snapshot',
  metrics:monitor.DEFINITIONS.map(d=>({metric_code:d.code,basis:d.basis,tax_basis:d.taxBasis,value:'100000.00',known_subtotal:'100000.00',coverage:'complete',
    target:{status:'effective',coverage:'complete',value:'200000.00',known_subtotal:'200000.00'},rate:'0.5',gap:'100000.00',rate_basis:'effective_target',day_change:null,
    missing_amount_count:0,unknown_eligibility_count:0,missing_reasons:[],registered_fact_count:1,registered_period_count:1,incomplete_period_count:0,
    missing_material_count:0,tax_unknown_period_count:0,conflict_period_count:0}))});
const detailQuery={metric_code:'actual_recognized_exclusive',limit:20,offset:0};
const actualPage=(q=query)=>({...envelope(q),contract_version:'dashboard.actual_page.v1',values_basis:'current_authorized_quarter_snapshot',basis:'adopted_actuals',...detailQuery,
  coverage:'partial',total:1,known_total:1,has_more:false,missing_reasons:[],items:[{id:'period:customer:Q4',customer_id:'customer',customer_name:'客户甲',opportunity_id:null,
    owner_name:null,year:q.year,quarter:q.quarter,kind:'recognized',amount:'0.00',raw_amount:'0.00',tax_basis:'tax_exclusive',tax_unresolved:false,source:'operations',
    status:'confirmed',period_precision:'quarter',complete:true,missing_material_count:0}]});
function page(api={}) {
  return {...monitor.methods(api),data:{...monitor.initialData(new Date('2026-10-25T00:00:00Z')),viewMode:'personal',selectedMemberId:'',selectedTeamGroups:[],canViewTeam:false},
    identity:'company-a:self',allowed:true,identityKey(){return this.identity;},monitorSelfId(){return 'self';},monitorAllowed(){return this.allowed;},
    factsQuery(){return {personal:this.data.viewMode==='personal',member_id:this.data.selectedMemberId,team_groups:this.data.selectedTeamGroups};},
    setData(values){Object.assign(this.data,values);}};
}
test('Mini监控按北京时间当前季度初始化，精确缩放金额且不与多季度图表共用期间',()=>{
  const initial=monitor.initialData(new Date('2026-09-30T16:30:00Z'));assert.equal(initial.monitorQuarter,4);
  assert.equal(monitor.money(null),'—');assert.equal(monitor.money('0.00'),'0.00');assert.equal(monitor.money('0.01'),'<0.01');
  assert.equal(monitor.money('999999999999999999.99'),'100,000,000,000,000.00');
  assert.equal(monitor.formatDecimal('-12.345'),'−12.35');assert.equal(monitor.percentage('0.250000'),'25.0%');
  const p=page();p.data.selectedQuarterKeys=['2026-Q1','2026-Q2'];assert.deepEqual(p.monitorQuery(),query);
  p.data.viewMode='team';p.data.selectedTeamGroups=['team:b','team:a','team:a'];
  assert.deepEqual(p.monitorQuery(),{...query,personal:false,team_groups:['team:a','team:b']});
});
test('五项契约拒绝错误成员、范围、期间和旧预测口径，目标无权限不接受达成率',()=>{
  assert.equal(monitor.matchingMetrics(overview(),query,'self').metrics.length,5);
  for(const edit of [r=>r.selection.member_id='other',r=>r.scope.kind='authorized',r=>r.quarter.quarter=3,r=>r.selection.team_groups=['team:a'],
    r=>r.values_basis='current_authorized_forecast_snapshot',r=>r.metrics[3].tax_basis='tax_inclusive',r=>r.metrics[0].value=100]){
    const value=overview();edit(value);assert.throws(()=>monitor.matchingMetrics(value,query,'self'));
  }
  const q={...query,personal:false,team_groups:['team:a']},team=overview(q);assert.equal(monitor.matchingMetrics(team,q,'self').scope.kind,'team');
  team.selection.member_id='self';assert.throws(()=>monitor.matchingMetrics(team,q,'self'));
  const denied=overview();denied.metrics[0].target={status:'permission_denied',value:null};assert.throws(()=>monitor.matchingMetrics(denied,query,'self'),/可见范围/);
  denied.metrics[0].rate=null;denied.metrics[0].gap=null;assert.equal(monitor.matchingMetrics(denied,query,'self').metrics[0].rate,null);
});
test('卡片保留未设置、不可查看、部分目标、真实0和实际缺项数量，实际不渲染预测目标布局',()=>{
  const forecast=overview().metrics[0];forecast.target={status:'no_target',coverage:'unavailable',value:null};forecast.rate=null;forecast.gap=null;
  assert.equal(monitor.cardView(forecast).target,'未设置');assert.equal(monitor.cardView(forecast).targetNote,'');
  forecast.target={status:'permission_denied',coverage:'partial',value:null,known_subtotal:'900000'};assert.equal(monitor.cardView(forecast).target,'不可查看');
  forecast.target={status:'no_target',coverage:'partial',value:null,known_subtotal:'50000'};assert.match(monitor.cardView(forecast).target,/已知 5.00/);
  forecast.target={status:'effective',value:'0.00'};assert.match(monitor.cardView(forecast).targetNote,/目标明确为 0/);
  Object.assign(forecast,{coverage:'unavailable',value:null,known_subtotal:null,unknown_eligibility_count:2,missing_amount_count:3,missing_reasons:['unknown_eligibility','currency_unavailable']});
  assert.equal(monitor.cardView(forecast).amount,'—');assert.match(monitor.cardView(forecast).note,/2 条记录/);assert.match(monitor.cardView(forecast).note,/3 条符合条件/);assert.doesNotMatch(monitor.cardView(forecast).note,/5 条/);
  const actual=overview().metrics[3];actual.value='0.00';assert.equal(monitor.cardView(actual).amount,'0.00');
  assert.equal(monitor.cardView(actual).target,undefined);assert.equal(monitor.cardView(actual).rate,undefined);
  Object.assign(actual,{value:null,known_subtotal:null,coverage:'unavailable',missing_reasons:['actual_not_registered']});assert.equal(monitor.cardView(actual).state,'未登记');
  Object.assign(actual,{coverage:'partial',known_subtotal:'120000',incomplete_period_count:2,tax_unknown_period_count:2,missing_material_count:1,missing_reasons:['actual_incomplete','actual_tax_basis_unresolved']});
  const partial=monitor.cardView(actual);assert.equal(partial.amount,'12.00');assert.equal(partial.amountLabel,'已登记小计');assert.match(partial.note,/2 项季度记录.*2 项季度记录/);assert.doesNotMatch(partial.note,/5 项/);
});
test('实际明细允许准确总条数的部分金额，客户级记录和税待确认不冒充商机或含税金额',()=>{
  const data=actualPage();assert.equal(monitor.matchingDetail(data,query,detailQuery,'self').total,1);
  const view=monitor.detailRow(data.items[0],detailQuery.metric_code);assert.match(view.subtitle,/客户级记录/);assert.equal(view.amount,'0.00');assert.equal(view.owner,'—');assert.equal(view.date,'季度汇总记录');assert.equal(view.source,'实绩登记');
  const unknown=monitor.detailRow({...data.items[0],amount:null,raw_amount:'123456',complete:false,tax_unresolved:true,source:'private_code'},detailQuery.metric_code);
  assert.equal(unknown.amount,'—');assert.match(unknown.note,/不含税口径待确认/);assert.equal(unknown.source,'来源未记录');
  assert.throws(()=>monitor.matchingDetail({...data,items:[{...data.items[0],year:2025}]},query,detailQuery,'self'));
  assert.throws(()=>monitor.matchingDetail({...data,offset:20},query,detailQuery,'self'));
  assert.equal(monitor.detailRow({metric_contributions:{forecast_collection:{value:null,reason:'private_field SQL'}}},'forecast_collection').note,'暂不可计算');
});
test('切换季度立即清旧值，较晚回包及切换公司后的回包不能恢复旧卡',async()=>{
  const pending=[];const p=page({getDashboardMetrics:q=>new Promise(resolve=>pending.push({q,resolve}))});
  const first=p.loadMonitor();await Promise.resolve();
  const second=p.changeMonitorQuarter({detail:{value:3}});assert.equal(p.data.monitorReady,false);assert.deepEqual(p.data.monitorForecasts,[]);await Promise.resolve();
  pending[1].resolve(overview(pending[1].q));await second;assert.equal(p.data.monitorReady,true);assert.equal(p.data.monitorQuarter,3);
  pending[0].resolve(overview(pending[0].q));await first;assert.equal(p.data.monitorQuarter,3);assert.equal(p.data.monitorForecasts.length,3);
  const third=p.loadMonitor();await Promise.resolve();p.identity='company-b:self';p.clearMonitor();pending[2].resolve(overview(pending[2].q));await third;
  assert.equal(p.data.monitorReady,false);assert.deepEqual(p.data.monitorActuals,[]);assert.equal(p.data.monitorDetail,null);
});
test('明细关闭或切换成员后晚回包丢弃，实际只调用actuals且无客户/商机跳转授权扩展',async()=>{
  const pending=[];let forecastCalls=0;
  const p=page({getDashboardMetrics:async q=>overview(q),getDashboardMetricActuals:q=>new Promise(resolve=>pending.push({q,resolve})),getDashboardMetricOpportunities(){forecastCalls++;}});
  await p.loadMonitor();const task=p.openMonitorDetail({currentTarget:{dataset:{code:detailQuery.metric_code}}});await Promise.resolve();
  p.closeMonitorDetail();pending[0].resolve(actualPage());await task;assert.equal(p.data.monitorDetail,null);assert.equal(forecastCalls,0);
  const next=p.openMonitorDetail({currentTarget:{dataset:{code:detailQuery.metric_code}}});await Promise.resolve();
  p.data.selectedMemberId='other';const refresh=p.loadMonitor();assert.equal(p.data.monitorDetail,null);pending[1].resolve(actualPage());await next;await refresh;
  assert.equal(p.data.monitorDetail,null);assert.equal(p.data.monitorReady,true);assert.equal(p.data.monitorForecasts.length,3);
});
test('权限或目录不可用不请求，接口失败不显示底层错误且可以重试',async()=>{
  let calls=0,fail=true;const p=page({getDashboardMetrics:async q=>{calls++;if(fail)throw Error('SQL private_field');return overview(q);}});
  p.allowed=false;await p.loadMonitor();assert.equal(calls,0);
  p.allowed=true;p.data.canViewTeam=true;p.data.optionsError='unavailable';await p.loadMonitor();assert.equal(calls,0);assert.match(p.data.monitorError,/范围尚未加载/);
  p.data.optionsError='';await p.loadMonitor();assert.equal(p.data.monitorReady,false);assert.match(p.data.monitorError,/暂时加载失败/);assert.doesNotMatch(p.data.monitorError,/SQL|private_field/);
  fail=false;await p.retryMonitor();assert.equal(p.data.monitorReady,true);assert.equal(p.data.monitorError,'');assert.equal(p.data.monitorAsOf,'2026-10-25 18:20');
});


test('历史归属待核对单独解释数量，不误写未登记或当前负责人',()=>{
  const metric={...overview().metrics[3],value:null,known_subtotal:null,coverage:'partial',attribution_unknown_period_count:2,missing_reasons:['actual_attribution_unresolved']};
  const view=monitor.cardView(metric);assert.equal(view.state,'归属待核对');assert.match(view.note,/2 项历史季度记录归属待核对/);
  const row=monitor.detailRow({...actualPage().items[0],owner_name:'当前认领人',attribution_status:'unresolved'},'actual_recognized_exclusive');
  assert.equal(row.owner,'历史归属待核对');assert.match(row.note,/不按当前认领人/);
});
