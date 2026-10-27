require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const growth=require('../miniprogram/utils/growthProfile');
// Exact six synthetic examples from Backend contract commit 0d7bb1c, not runtime evidence.
const examples=require('./helpers/growth-contract-v1.json').examples;
const copy=x=>JSON.parse(JSON.stringify(x)),event=id=>({currentTarget:{dataset:{id}}});
function harness(fde=false,api={}){
  let definition;const timers=[],navigation=[],modals=[],session={userId:'self',role:'sales',workspaceId:'w',permissionVersion:1,permissions:{'profile.sales_read':true,'profile.sales_review':true,'profile.fde_read':true,'profile.fde_review':true,'visit.read':true,'task.read':true}};
  const app={globalData:{session}},file=path.resolve(__dirname,'../miniprogram',fde?'components/fde-profile/index.js':'pages/profile/index.js');
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:x=>definition=x,Component:x=>definition=x,require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),n)),getApp:()=>app,Date,
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},wx:{nextTick:fn=>fn(),navigateTo:row=>navigation.push(row.url),showModal:row=>modals.push(row)}});
  const page={...definition,...definition.methods,visible:true,properties:{profileScope:'self'},data:copy(definition.data),setData(value,done){Object.assign(this.data,value);if(done)done();},drawGrowthCharts(){},drawRadar(){},loadMarketingAnalytics(){}};
  return {page,session,timers,navigation,modals};
}
const record=()=>copy(examples.self_without_material);
test('GROWTH 入页和刷新仅GET，无材料已完成作业保留未评估而非0或能力弱点',async()=>{
  let reads=0,posts=0;const {page}=harness(false,{getSalesGrowth:async()=>{reads++;return record();},ensureSalesGrowthReview:async()=>posts++});
  await page.loadSalesGrowth();await page.loadSalesGrowth();
  assert.equal(reads,2);assert.equal(posts,0);assert.equal(page.data.growthReady,true);assert.equal(page.data.overallScore,'--');assert.equal(page.data.growthReview.evaluationState,'insufficient_data');
  assert.ok(page.data.dimensions.every(d=>d.score==='--'));assert.match(page.data.growthCoverageText,/采用 0 \/ 0/);assert.doesNotMatch(page.data.growthStatusText,/今日.*完成/);
});
test('GROWTH 保存公司总分与原框架总分分开，部分零权重缺项可有总分，null不得回退',()=>{
  const r=record();r.latest.overall_score=99;r.latest.evaluation_state='partially_assessed';r.latest.score_summary.value=65;r.latest.score_summary.text='65';
  let d=growth.salesDisplay(r);assert.equal(d.overallScore,'65');assert.equal(d.growthReview.evaluationState,'partially_assessed');assert.match(d.growthStatusText,/部分/);
  r.latest.score_summary.value=null;r.latest.score_summary.text='--';d=growth.salesDisplay(r);assert.equal(d.overallScore,'--');assert.equal(d.profileScoreInfo.coverage_percent,0);
  r.latest.score_summary={value:0,text:'0'};assert.equal(growth.salesDisplay(r).overallScore,'0');
  const legacy=copy(examples.review_evidence_unavailable.review);assert.equal(growth.scoreInfo(legacy).value,70);assert.match(growth.basisLabel(legacy.score_basis),/历史规则/);
});
test('GROWTH 画像规则弹窗按保存口径解释null和零权重缺项，不沿用成熟度缺项折算',()=>{
  const h=harness(),show=kind=>{h.page.showScoreRule({currentTarget:{dataset:{kind}}});return h.modals.at(-1).content;};
  const r=record();r.latest.overall_score=99;
  h.page.applyGrowth(r,false);let content=show('profile');
  assert.match(content,/正权重维度缺料时显示“--”/);assert.match(content,/已保存总分：--/);
  assert.match(content,/已评估 0\/6 项，覆盖权重 0%/);assert.doesNotMatch(content,/剔除|剩余权重|历史评估|99/);
  r.latest.evaluation_state='partially_assessed';r.latest.score_summary={value:65,text:'65'};
  h.page.applyGrowth(r,false);content=show('profile');
  assert.match(content,/只有零权重维度缺料时/);assert.match(content,/本次状态：部分维度已评估，仍有缺项/);assert.match(content,/已保存总分：65/);
  r.latest.score_summary={value:0,text:'0'};h.page.applyGrowth(r,false);assert.match(show('profile'),/已保存总分：0/);
  h.page.applyGrowth({latest:copy(examples.review_evidence_unavailable.review)},false);content=show('profile');
  assert.match(content,/历史评估.*口径未知/);assert.match(content,/原框架总分/);assert.match(content,/历史结果：70/);assert.doesNotMatch(content,/剔除|剩余权重/);
  h.page.applyGrowth(examples.mixed_group,false);assert.match(show('profile'),/不提供团队画像总分/);
  h.page.applyGrowth({},false);assert.match(show('profile'),/尚无已保存评估/);
  for(const kind of ['maturity','efficiency'])assert.match(show(kind),/缺失项剔除后按剩余权重折算/);
});
test('GROWTH 冻结维度定义和历史元数据完整保留，不能套用当前框架或当前分数',()=>{
  const r=record(),id=r.latest.id;r.framework.dimensions[0].name='最新规则名称';r.latest.score_summary={value:80,text:'80'};r.latest.evaluation_state='assessed';
  r.history[0].score_summary={value:35,text:'35'};r.history[0].evaluation_state='assessed';
  const d=growth.salesDisplay(r),points=growth.historyPoints(d.history);assert.notEqual(d.dimensions[0].name,'最新规则名称');assert.equal(points[0].score,35);
  assert.equal(points[0].id,id);assert.equal(points[0].generation_no,1);assert.equal(points[0].reviewed_at,r.history[0].reviewed_at);assert.equal(points[0].score_basis.basis_id,r.history[0].score_basis.basis_id);
  assert.equal(d.growthReview.reviewId,id);assert.equal(d.growthReview.reviewedAt,r.latest.reviewed_at);
});
test('GROWTH 80/81条和旧覆盖状态、团队混合规则如实显示，不生成假历史或总分',()=>{
  const c=examples.coverage_81_uses_latest_80;assert.match(growth.coverageText(c),/80 \/ 81.*已截取最近材料/);
  assert.match(growth.coverageText({...c,total_eligible_visits:80,truncated:false}),/80 \/ 80.*未截断/);
  assert.match(growth.coverageText({status:'legacy_unknown',used_visits:10}),/不能确认是否为全量/);
  const d=growth.salesDisplay({...examples.mixed_group,history:[{overall_score:90}],latest:{overall_score:90,score_summary:{value:90}}});
  assert.equal(d.overallScore,'--');assert.equal(d.history.length,0);assert.match(d.growthStatusText,/未评估 2 人/);assert.equal(d.growthReview.comparisonStatus,'mixed_basis');assert.equal(d.growthReview.groupCoverage.eligible_members,5);
});
test('GROWTH 显式点击只提交一次，202之后只GET；上次结果日期与待更新状态独立',async()=>{
  let posts=0,resolvePost,reads=0;const r=record();r.latest.review_date='2026-10-08';r.latest.reviewed_at='2026-10-08T10:00:00+08:00';
  const h=harness(false,{getSalesGrowth:async()=>{reads++;return posts?{...r,current_review:{id:'pending',status:'running',generation_no:2}}:r;},ensureSalesGrowthReview:()=>{posts++;return new Promise(resolve=>resolvePost=resolve);}});
  await h.page.loadSalesGrowth();const pending=h.page.requestGrowthReview();await h.page.requestGrowthReview();assert.equal(posts,1);assert.equal(h.page.data.growthReview.busy,true);
  resolvePost(examples.manual_receipt);await pending;assert.equal(reads,2);assert.match(h.page.data.reviewDate,/2026-10-08/);assert.match(h.page.data.growthStatusText,/上次保存/);
  await h.timers.shift()();assert.equal(posts,1);assert.equal(reads,3);
  h.page.pause();await h.timers.shift()();assert.equal(reads,3);
});
test('GROWTH 读取权不等于生成权，查看他人即使can_review=true也不代生成',async()=>{
  let posts=0;const h=harness(false,{getSalesGrowth:async()=>record(),getScopedSalesGrowth:async()=>record(),ensureSalesGrowthReview:async()=>posts++});
  h.session.permissions['profile.sales_review']=false;await h.page.loadSalesGrowth();assert.equal(h.page.data.growthReview.canReview,false);await h.page.requestGrowthReview();assert.equal(posts,0);
  h.session.permissions['profile.sales_review']=true;Object.assign(h.page.data,{profileScopeMode:'person',profileSelectedMemberId:'other'});await h.page.loadScopedGrowth();await h.page.requestGrowthReview();assert.equal(posts,0);assert.equal(h.page.data.growthReview.canReview,false);
});
test('GROWTH 生成409停止按钮等待刷新，403清除结果，无自动POST重试',async()=>{
  for(const statusCode of [409,403]){
    let posts=0;const h=harness(false,{getSalesGrowth:async()=>record(),ensureSalesGrowthReview:async()=>{posts++;throw Object.assign(Error('拒绝'),{statusCode});}});
    await h.page.loadSalesGrowth();await h.page.requestGrowthReview();assert.equal(posts,1);assert.equal(h.page.data.growthReview.canReview,false);
    if(statusCode===403){assert.equal(h.page.data.history.length,0);assert.equal(h.page.data.growthReady,false);}else assert.match(h.page.data.growthReview.error,/刷新/);
  }
});
test('GROWTH 切人、撤权及离页后的迟到GET不覆盖当前评估',async()=>{
  for(const change of [h=>{h.page.data.profileScopeMode='person';h.page.data.profileSelectedMemberId='other';},h=>{h.session.permissionVersion=2;},h=>h.page.pause()]){
    let resolve;const h=harness(false,{getSalesGrowth:()=>new Promise(r=>resolve=r)});const pending=h.page.loadSalesGrowth();change(h);resolve(record());await pending;assert.equal(h.page.data.growthReady,false);assert.equal(h.page.data.history.length,0);
  }
});
test('GROWTH 未刷新旧页面时，换登录身份不能借旧按钮发起生成或读取旧依据',async()=>{
  for(const fde of [false,true]){
    let posts=0,details=0;const h=harness(fde,{getSalesGrowth:async()=>record(),getFdeProfile:async()=>fdePayload(),ensureSalesGrowthReview:async()=>posts++,reviewFdeProfile:async()=>posts++,getSalesGrowthReview:async()=>details++,getFdeProfileReview:async()=>details++});
    if(fde)await h.page.load();else await h.page.loadSalesGrowth();
    h.session.userId='new-user';h.page.data.reviewStatus='missing';
    if(fde)await h.page.generateAdvice();else await h.page.requestGrowthReview();
    await h.page.openGrowthEvidence(event(h.page.data.latestReviewId));assert.equal(posts,0);assert.equal(details,0);
  }
});
test('GROWTH 安全证据只显式GET，匿名不可见引用不能跳转，旧依据身份失效后不能使用',async()=>{
  let calls=0;const detail=copy(examples.review_evidence_unavailable);detail.evidence.push({visit_id:'visible-visit',visit_version:2,dimension_codes:['needs_discovery'],detail:'可读原依据',readable:true});
  // Even a malformed unreadable row must not render hidden identifiers or prose.
  Object.assign(detail.evidence[0],{visit_id:'hidden-id',detail:'隐藏正文'});detail.review.input_snapshot={secret:'PRIVATE'};
  const h=harness(false,{getSalesGrowth:async()=>record(),getSalesGrowthReview:async()=>{calls++;return detail;}});await h.page.loadSalesGrowth();assert.equal(calls,0);
  await h.page.openGrowthEvidence(event(detail.review.id));assert.equal(calls,1);assert.doesNotMatch(JSON.stringify(h.page.data.growthEvidence.result),/hidden-id|隐藏正文|PRIVATE/);
  h.page.openGrowthSource({currentTarget:{dataset:{key:'0'}}});assert.equal(h.navigation.length,0);
  h.page.openGrowthSource({currentTarget:{dataset:{kind:'visit',id:'visible-visit'}}});assert.equal(h.navigation.length,1);
  h.session.permissionVersion=2;h.page.openGrowthSource({currentTarget:{dataset:{key:'1'}}});assert.equal(h.navigation.length,1);
});
test('GROWTH 依据关闭、切人和403/404清缓存，迟到旧依据不复活',async()=>{
  for(const action of ['close','switch','denied','missing']){
    let resolve,reject;const h=harness(false,{getSalesGrowth:async()=>record(),getSalesGrowthReview:()=>new Promise((r,j)=>{resolve=r;reject=j;})});await h.page.loadSalesGrowth();
    const pending=h.page.openGrowthEvidence(event(h.page.data.latestReviewId));
    if(action==='close')h.page.closeGrowthEvidence();else if(action==='switch'){h.page.data.profileScopeMode='person';h.page.data.profileSelectedMemberId='other';h.page.clearGrowthAccess();}
    if(['denied','missing'].includes(action))reject(Object.assign(Error('不可读'),{statusCode:action==='denied'?403:404}));else resolve(examples.review_evidence_unavailable);
    await pending;assert.equal(h.page.data.growthEvidence.result,null);if(['denied','missing'].includes(action))assert.equal(h.page.data.history.length,0);
  }
});
test('GROWTH 历史曲线按实际日期比例定位，null与basis变化断线，0保留真实点',()=>{
  const calls=[],ctx=new Proxy({},{get:(_,name)=>(...args)=>calls.push([name,...args])});
  const basis={snapshot_status:'frozen',basis_id:'a'},points=[{date:'2026-10-01',score:0,score_basis:basis},{date:'2026-10-02',score:50,score_basis:basis},{date:'2026-10-03',score:null,score_basis:basis},{date:'2026-10-05',score:55,score_basis:basis},{date:'2026-10-11',score:90,score_basis:{...basis,basis_id:'b'}}];
  require('../miniprogram/utils/fdeHistory').draw(ctx,350,220,points);
  const dots=calls.filter(c=>c[0]==='arc');assert.equal(dots.length,4);assert.equal(dots[0][1],34);assert.equal(dots[1][1],64);assert.equal(dots[3][1],334);
  const lastBegin=calls.findIndex(c=>c[0]==='setLineWidth'&&c[1]===2),pathCalls=calls.slice(lastBegin).filter(c=>['lineTo','moveTo'].includes(c[0]));assert.equal(pathCalls.filter(c=>c[0]==='lineTo').length,1);
});
function fdePayload(){const codes=['record_quality','plan_coverage','project_coverage','customer_coverage','task_completion','task_timeliness'];return {data_source:'database',can_review:true,review_status:'empty',review_run_id:'run-one',framework:{dimensions:codes.map(code=>({code,name:code}))},latest:{overall_score:99,dimensions:codes.map((code,i)=>({code,score:i===0?80:null,numerator:i===0?160:0,denominator:i===0?2:0})),advice:[],score_basis:{basis_id:'fde-v1',snapshot_status:'frozen',calculation_version:'fde_portrait_ratios_v1'}},history:[{...examples.fde_history_metadata,dimensions:[{code:'record_quality',score:76}]}],evidence_coverage:{archived_visits:25,readable_archived_visits:20,assigned_projects:4,assigned_customers:3,owned_tasks:9},coaching_coverage:{status:'known',selected_visits:12,selected_tasks:9,visit_limit:12,task_limit:20,truncated_text_sources:2}};}
test('GROWTH FDE沿事实指标与独立建议素材，记录质量分/其余百分比，历史元数据保留',async()=>{
  let posts=0;const h=harness(true,{getFdeProfile:async()=>fdePayload(),reviewFdeProfile:async()=>posts++});await h.page.load();assert.equal(posts,0);
  assert.equal(h.page.data.portraitScore,'—');assert.equal(h.page.data.dimensions[0].unit,'分');assert.equal(h.page.data.dimensions[1].unit,'%');assert.equal(h.page.data.dimensions[0].numerator,160);
  assert.match(h.page.data.coverageText,/25.*20/);assert.match(h.page.data.coachingCoverageText,/上限 12.*上限 20/);assert.equal(h.page.data.historyPoints[0].run_id,examples.fde_history_metadata.run_id);assert.equal(h.page.data.historyPoints[0].score_basis.snapshot_status,'legacy_unknown');
  h.page.properties.selectedMemberId='other';h.page.data.reviewStatus='missing';await h.page.generateAdvice();assert.equal(posts,0);
});
test('GROWTH FDE证据使用同一安全接口、只读详情和来源按钮，403清历史与建议',async()=>{
  let fail=false;const h=harness(true,{getFdeProfile:async()=>fdePayload(),getFdeProfileReview:async()=>{if(fail)throw Object.assign(Error('撤权'),{statusCode:403});return {assessment_kind:'fde_factual_portrait',review:{run_id:'run-one'},evidence:[{source_type:'task',source_id:'task-visible',source_version:3,detail:'已授权任务',readable:true},{source_type:'visit',source_id:'secret-id',detail:'secret',readable:false}],evidence_summary:{total:2,readable:1,unavailable:1}};}});
  await h.page.load();await h.page.openGrowthEvidence(event('run-one'));assert.doesNotMatch(JSON.stringify(h.page.data.growthEvidence.result),/secret/);
  h.page.openGrowthSource({currentTarget:{dataset:{kind:'task',id:'task-visible'}}});assert.match(h.navigation[0],/task-detail/);
  fail=true;await h.page.openGrowthEvidence(event('run-one'));assert.equal(h.page.data.ready,false);assert.equal(h.page.data.history.length,0);assert.equal(h.page.data.growthEvidence.result,null);
});
test('GROWTH 实际API封装丢响应重用key、并发点击共用请求，成功后下一点击新key，详情仅GET',async()=>{
  const storage=new Map(),requests=[];let fail=true,held;
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{requests.push(o);if(o.method==='GET')return o.success({statusCode:200,data:{}});if(fail)o.fail({errMsg:'timeout'});else held=o;}};
  const load=()=>{delete require.cache[require.resolve('../miniprogram/utils/apiClient')];return require('../miniprogram/utils/apiClient');};let api=load();api.saveAuth({access_token:'synthetic',actor:{workspace_id:'w',user_id:'self'}});
  await assert.rejects(api.ensureSalesGrowthReview(),/timeout/);const key=requests[0].header['Idempotency-Key'];assert.match(key,/^[0-9a-f-]{36}$/);assert.deepEqual(requests[0].data,{});
  api=load();fail=false;const first=api.ensureSalesGrowthReview(),second=api.ensureSalesGrowthReview();assert.equal(requests.length,2);assert.equal(requests[1].header['Idempotency-Key'],key);held.success({statusCode:202,data:examples.manual_receipt});await Promise.all([first,second]);
  const next=api.ensureSalesGrowthReview();assert.notEqual(requests[2].header['Idempotency-Key'],key);held.success({statusCode:202,data:examples.manual_receipt});await next;
  await api.getSalesGrowthReview('review-id');await api.getFdeProfileReview('run-id');assert.ok(requests.slice(3).every(r=>r.method==='GET'&&!r.header['Idempotency-Key']));assert.match(requests[3].url,/sales-growth\/reviews\/review-id/);assert.match(requests[4].url,/fde\/profile\/reviews\/run-id/);
});
