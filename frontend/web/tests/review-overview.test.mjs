import {test} from 'node:test';
import assert from 'node:assert/strict';
import {REVIEW_METRICS,REVIEW_ACTIVITIES,formatDecimal,money,percentage,matchingReview,matchingPage,reviewSelection,changeRows,changeValue} from '../department-ui/review/overview-model.js';
import {createReviewOverviewApi} from '../department-ui/review/overview-api.js';
const selection={scope:'self',anchor_date:'2026-10-14',period_type:'day'};
const envelope=()=>({selection:{...selection,team_id:null,member_id:null},scope:{kind:'self',label:'本人',member_ids:['a']},period:{label:'2026-10-14'},data_as_of:'2026-10-14T10:00:00Z'});
const overview=()=>({...envelope(),contract_version:'review.overview.v1',quarter:{quarter:4,time_progress:'0.15217391'},members:[],metrics:REVIEW_METRICS.map(({code})=>({metric_code:code,value:'0.00',known_subtotal:'0.00',coverage:'complete',target:{value:null,status:'no_target'},gap:null,rate:null,day_change:null,baseline:{status:'no_baseline'}})),activities:REVIEW_ACTIVITIES.map(({code})=>({code,count:0,known_count:0,coverage:'complete'}))});

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
