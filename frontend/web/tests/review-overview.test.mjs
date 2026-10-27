import {test} from 'node:test';
import assert from 'node:assert/strict';
import {metricState,REVIEW_METRICS,REVIEW_ACTIVITIES,formatDecimal,money,percentage,matchingReview,matchingPage,reviewSelection,changeRows,changeValue} from '../department-ui/review/overview-model.js';
import {createReviewOverviewApi} from '../department-ui/review/overview-api.js';
import {dailyChangeView,notUpdatedView} from '../department-ui/review/overview-model.js';
const selection={scope:'self',anchor_date:'2026-10-14',period_type:'day'};
const envelope=()=>({selection:{...selection,team_id:null,member_id:null},scope:{kind:'self',label:'本人',member_ids:['a']},period:{label:'2026-10-14'},data_as_of:'2026-10-14T10:00:00Z'});
const overview=()=>({...envelope(),contract_version:'review.overview.v1',quarter:{quarter:4,time_progress:'0.15217391'},members:[],metrics:REVIEW_METRICS.map(({code})=>({metric_code:code,value:'0.00',known_subtotal:'0.00',coverage:'complete',target:{value:null,status:'no_target'},gap:null,rate:null,day_change:null,baseline:{status:'no_baseline'}})),activities:REVIEW_ACTIVITIES.map(({code})=>({code,count:0,known_count:0,coverage:'complete'}))});

test('scheduled daily change uses server delta and exact Shanghai capture time without reconstructing history',()=>{
 const metric={value:'999999.00',coverage:'complete',day_change:'-200000.00',baseline:{status:'available',basis:'previous_day_scheduled_snapshot',captured_at:'2026-10-14T15:53:27Z',value:'123.00'}};
 const view=dailyChangeView(metric);
 assert.equal(view.value,'−20.00 万元');assert.match(view.note,/2026\/10\/14 23:53:27（北京时间）/);assert.match(view.title,/2026-10-14T15:53:27Z/);
 assert.equal(dailyChangeView({...metric,day_change:'0.00'}).value,'0.00 万元');
 for(const changed of [
  {...metric,day_change:null},{...metric,coverage:'partial'},
  {...metric,baseline:{...metric.baseline,status:'no_baseline'}},
  {...metric,baseline:{...metric.baseline,status:'incomparable',reason_code:'baseline_amount_incomplete'}},
  {...metric,baseline:{...metric.baseline,captured_at:null}},
  {...metric,baseline:{...metric.baseline,captured_at:'2026-10-14T23:50:00'}},
  {...metric,baseline:{...metric.baseline,captured_at:'invalid'}},
  {...metric,baseline:{status:'available',captured_at:'2026-10-14T15:53:27Z'}},
 ])assert.doesNotMatch(dailyChangeView(changed).value,/万元/);
 const incomplete=dailyChangeView({...metric,baseline:{...metric.baseline,status:'incomparable',reason_code:'baseline_amount_incomplete'}});
 assert.equal(incomplete.value,'暂不可比');assert.match(incomplete.note,/昨日快照金额或资格信息不完整/);assert.match(incomplete.note,/23:53:27/);
 assert.match(dailyChangeView({...metric,baseline:{status:'no_baseline',reason_code:'historical_anchor_current_values'}}).note,/所选历史日期不计算日变化/);
});

test('not-updated filter needs explicit complete coverage; unknown count never becomes zero',()=>{
 const response=(availability,count=0)=>({filter_availability:{not_updated:availability},pool_counts:{not_updated:count}});
 assert.deepEqual(notUpdatedView(response({available:true,coverage:'complete'})),{available:true,count:0,reason:''});
 for(const availability of [undefined,{available:true},{coverage:'complete'},{available:false,coverage:'complete'},{available:true,coverage:'partial'},{available:true,coverage:'unavailable'}]){
  const view=notUpdatedView(response(availability));assert.equal(view.available,false);assert.equal(view.count,'—');assert.ok(view.reason);
 }
 assert.equal(notUpdatedView(response({available:true,coverage:'complete'},null)).count,'—');
 assert.match(notUpdatedView(response({available:false,coverage:'unavailable',history_coverage:{reason_codes:['capture_trigger_unavailable']}})).reason,/采集暂不可用/);
 // D04's partial field-detail coverage cannot override R06 presence coverage.
 assert.equal(notUpdatedView({...response({available:true,coverage:'complete'}),activities:[{code:'updated_opportunities',coverage:'partial'}]}).available,true);
});

test('display preserves exact cents and explicit zero; never loses tiny nonzero or signs',()=>{
 assert.equal(formatDecimal('9007199254740993.21'),'9,007,199,254,740,993.21');
 assert.equal(money('123456789.99'),'12,345.68');assert.equal(money('0.00'),'0.00');assert.equal(money(null),'—');
 assert.equal(money('0.01'),'<0.01');assert.equal(formatDecimal('-300000.00',{shift:4,signed:true}),'−30.00');
 assert.equal(percentage('0.15217391'),'15.2%');assert.equal(percentage(null),'—');
});
test('selected team and date cannot be replaced by a broader or stale response',()=>{
 assert.equal(matchingReview(overview(),selection).metrics[0].value,'0.00');
 for(const edit of [r=>r.selection.scope='team',r=>r.selection.anchor_date='2026-10-13',r=>r.scope.kind='team']){
  const value=overview();edit(value);assert.throws(()=>matchingReview(value,selection),/范围或周期/);
 }
 const incomplete=overview();incomplete.metrics[0].coverage='partial';assert.throws(()=>matchingReview(incomplete,selection),/部分数据/);
 incomplete.metrics[0].value=null;assert.equal(matchingReview(incomplete,selection).metrics[0].value,null);
 const partialActions=overview();partialActions.activities[0].coverage='partial';assert.throws(()=>matchingReview(partialActions,selection),/部分动作/);
 partialActions.activities[0].count=null;assert.equal(matchingReview(partialActions,selection).activities[0].count,null);
 const number=overview();number.metrics[0].value=0;assert.throws(()=>matchingReview(number,selection),/格式/);
 const request=reviewSelection({period:{kind:'two_weeks',anchor_date:'2026-10-14'},scope:{kind:'team',team_id:'team-a'}});
 assert.deepEqual(request,{scope:'team',period_type:'two_weeks',anchor_date:'2026-10-14',team_id:'team-a'});
});
test('known current and target subtotals can expose a labeled provisional rate',()=>{
 const value=overview(),metric=value.metrics[0];
 Object.assign(metric,{value:null,known_subtotal:'100.00',coverage:'partial',rate:'1.00000000',gap:'0.00',rate_basis:'known_subtotals_and_targets',target:{status:'no_target',value:null,known_subtotal:'100.00',coverage:'partial'}});
 assert.equal(matchingReview(value,selection).metrics[0].rate,'1.00000000');
});
test('detail requests reject wrong tab or page; partial known totals never become a complete zero',()=>{
 const query={activity:'formal_visits',limit:20,offset:0};
 const page={...envelope(),contract_version:'review.page.v1',...query,items:[],known_total:0,total:null,coverage:'unavailable',has_more:false};
 assert.equal(matchingPage(page,selection,query).total,null);
 assert.throws(()=>matchingPage({...page,activity:'pending_tasks'},selection,query),/筛选/);
 assert.throws(()=>matchingPage({...page,offset:20},selection,query),/分页/);
 assert.throws(()=>matchingPage({...page,total:0},selection,query),/可见范围/);
 assert.throws(()=>matchingPage({...page,coverage:'complete',total:0,available:false},selection,query),/可见范围/);
});
test('overview uses only authenticated GET and invalidates delayed identity responses',async()=>{
 const calls=[];let owner='a',finish;
 const api=createReviewOverviewApi({getIdentity:()=>owner,getClient:()=>({request:async options=>{calls.push(options);return overview();}})});
 await api.overview(selection);assert.equal(calls.length,1);assert.equal(calls[0].method,'GET');assert.ok(calls[0].fresh);assert.ok(calls[0].path.includes('scope=self'));
 const delayed=createReviewOverviewApi({getIdentity:()=>owner,getClient:()=>({request:()=>new Promise(resolve=>finish=resolve)})});
 const pending=delayed.overview(selection);owner='b';finish(overview());await assert.rejects(pending,/身份或权限/);
});
test('change details retain true before and after values including zero and only changed fields',()=>{
 const rows=changeRows([{id:'a',occurred_at:'now',changes:{before:{collection_amount:'1000000.00',recognized_amount:'0.00'},after:{collection_amount:'700000.00',recognized_amount:'0.00'}}}]);
 assert.deepEqual(rows,[{key:'a:collection_amount',at:'now',actor:'未记录或不可查看',field:'collection_amount',label:'预计回款',before:'1000000.00',after:'700000.00'}]);
});

test('business events omit unchanged fields and retain channel round trips, zero, actor and evidence time',()=>{
 const rows=changeRows([
  {id:'a',actor_id:'u',occurred_at:'first',changes:[{label:'销售渠道',before:'direct',after:'partner'},{label:'未变',before:{x:1,y:2},after:{y:2,x:1}}]},
  {id:'b',actor_id:'hidden',occurred_at:'second',changes:[{field:'sales_channel',before:'partner',after:'direct'},{field:'amount',before:'1.00',after:'0.00'}]}
 ],[{id:'u',name:'修改人甲'}]);
 assert.equal(rows.length,3);assert.deepEqual(rows.map(row=>row.at),['first','second','second']);
 assert.equal(rows[0].actor,'修改人甲');assert.equal(rows[1].actor,'未记录或不可查看');
 assert.equal(changeValue(rows[0],'before'),'直销');assert.equal(changeValue(rows[0],'after'),'伙伴');
 assert.equal(changeValue(rows[1],'after'),'直销');assert.equal(changeValue(rows[2],'after'),'0.00');
 assert.equal(changeRows([{id:'unchanged',changes:[{before:0,after:0}]}]).length,0);
});

test('zero effective target is labelled explicitly instead of missing',()=>{
 assert.deepEqual(metricState({coverage:'complete',target:{status:'effective',value:'0.00'},status:'no_target'}),{label:'零目标 · 不计算达成率',color:'default'});
});


test('review reads cancel their own transport and release listeners without caching or retrying',async()=>{
 for(const kind of ['overview','activities','opportunities']){
  const controller=new AbortController();let finish,requests=0,aborts=0,added=0,removed=0;
  const signal={get aborted(){return controller.signal.aborted;},addEventListener(...args){added++;controller.signal.addEventListener(...args);},removeEventListener(...args){removed++;controller.signal.removeEventListener(...args);}};
  const api=createReviewOverviewApi({getIdentity:()=> 'actor',getClient:()=>({request:options=>{requests++;assert.equal(options.fresh,true);assert.equal(options.method,'GET');assert.equal(options.timeout,30000);assert.equal(options.timeoutMs,undefined);options.onRequestTask({abort(){aborts++;}});return new Promise(resolve=>finish=resolve);}})});
  const read=()=>kind==='overview'?api.overview(selection,{signal}):api[kind](selection,{limit:20,offset:0,...(kind==='activities'?{activity:'formal_visits'}:{filter:'all'})},{signal});
  const pending=read();controller.abort();finish(overview());await assert.rejects(pending,{code:'REQUEST_CANCELLED'});
  assert.equal(aborts,1);assert.equal(added,1);assert.equal(removed,1);await assert.rejects(read(),{code:'REQUEST_CANCELLED'});assert.equal(requests,1);
 }
 const controller=new AbortController();let aborts=0,reads=0;
 const api=createReviewOverviewApi({getIdentity:()=> 'actor',getClient:()=>({request:async options=>{reads++;options.onRequestTask({abort(){aborts++;}});return overview();}})});
 await api.overview(selection,{signal:controller.signal});controller.abort();assert.equal(aborts,0);
 await api.overview(selection,{signal:new AbortController().signal});assert.equal(reads,2,'successful values are not cached');
});
