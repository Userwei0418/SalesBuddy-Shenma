const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const {normalizeRisk,canWrite}=require('../miniprogram/utils/riskDisplay');
const identity=require('../miniprogram/utils/requestIdentity');
const riskId='00000000-0000-4000-8000-000000000001';
const teamId='00000000-0000-4000-8000-000000000002';
const memberId='00000000-0000-4000-8000-000000000003';
const session=()=>({workspaceId:'w',userId:memberId,role:'sales',capabilities:{'risk.read':true,'risk.decide':true,'risk.resolve':true}});
const row=(extra={})=>({id:riskId,status:'accepted',version_no:4,can_decide:true,can_resolve:true,suggested_action:'核实预算',...extra});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {resolve,reject,promise};};
function harness(name,api={}) {
  let definition;const app={globalData:{session:session()},ensureLogin:()=>true},modals=[],toasts=[];
  const filename=path.resolve(__dirname,`../miniprogram/pages/${name}/index.js`);
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:d=>definition=d,getApp:()=>app,
    require:p=>p.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),p)),
    wx:{showToast:x=>toasts.push(x),showModal:x=>modals.push(x),setNavigationBarTitle(){},setStorageSync(){},vibrateShort(){},navigateBack(){}},
    Date,Set,Map,Promise,setTimeout:()=>1,clearTimeout(){}});
  const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(values,callback){Object.assign(this.data,values);if(callback)callback();}};
  page.onLoad(name==='risk-detail'?{id:riskId}:{});
  return {page,app,modals,toasts};
}
const result=(query,extra={})=>({items:Array.from({length:Math.max(0,Math.min(query.page_size,107-query.offset))},(_,i)=>row({id:`00000000-0000-4000-8000-${String(query.offset+i+1).padStart(12,'0')}`})),total:107,summary:{total:111,open_count:107,resolved_count:4,overdue_count:6},
  offset:query.offset,page_size:query.page_size,has_more:query.offset+query.page_size<107,next_offset:query.offset+query.page_size<107?query.offset+query.page_size:null,...extra});
const directory=()=>({items:[{id:memberId,name:'同名成员',account_code:'XS001',team_ids:[teamId]}],teams:[{id:teamId,name:'同名团队'}],defaults:{team_id:teamId}});

test('accepted保留原状态、未关闭提醒和明确写权限；清空正式建议不复活快照',()=>{
  const r=normalizeRisk(row(),session());assert.equal(r.status,'accepted');assert.equal(r.statusGroup,'open');assert.equal(r.signal.tone,'yellow');assert.match(r.statusLabel,/仍未关闭/);
  assert.equal(r.canDecide,true);assert.equal(r.canResolve,true);
  for(const value of [undefined,false,'true'])assert.equal(canWrite(session(),row({can_resolve:value}),'resolve'),false);
  for(const value of [undefined,0,'4',1.5])assert.equal(canWrite(session(),row({version_no:value}),'decide'),false);
  assert.equal(canWrite(session(),row({action_permissions:{'risk.resolve':false}}),'resolve'),false);
  const cleared=normalizeRisk(row({suggested_action:null,next_action:'旧建议',input_snapshot:{next_action:'快照旧建议'}}),session());
  assert.equal(cleared.nextAction,'当前未登记处理建议');assert.equal(cleared.canDecide,false);
  const stale=normalizeRisk(row({current_decision:{decision:'adopt',risk_version:3,is_current:true,note:'原确认'}}),session());assert.equal(stale.decisionStale,true);
});

test('风险超过50条从服务端翻页，完整统计不从本页或tab重算',async()=>{
  const queries=[];const {page}=harness('risks',{listRisks:async q=>{queries.push(q);return result(q,q.status==='resolved'?{total:4,has_more:false,next_offset:null,items:Array.from({length:4},()=>row({status:'resolved'}))}:{});}});
  await page.loadRisks();assert.equal(page.data.openCount,107);assert.equal(page.data.summary.total,111);assert.equal(page.data.risks.length,50);
  assert.equal(page.data.risks[0].status,'accepted');assert.equal(page.data.pageCount,3);
  await page.nextPage();assert.equal(queries[1].offset,50);assert.equal(page.data.page,2);
  await page.selectTab({currentTarget:{dataset:{key:'resolved'}}});assert.equal(queries[2].offset,0);assert.equal(page.data.total,4);assert.equal(page.data.openCount,107);
  assert.equal(queries[2].status,'resolved');assert.equal(page.data.risks[0].statusGroup,'resolved');
});

test('风险目录与请求只用UUID，团队默认自己的团队，成员选择不携带团队残留',async()=>{
  const queries=[];const {page}=harness('risks',{getDirectoryMembers:async q=>{assert.equal(q.permission,'risk.read');return directory();},listRisks:async q=>{queries.push(q);return result(q);}});
  await page.onShow();await page.changeScope({detail:{value:'team'}});assert.equal(queries.at(-1).team_id,teamId);
  const before=queries.length;await page.changeScope({detail:{value:'person'}});assert.equal(queries.length,before);assert.match(page.data.loadError,/请选择成员/);
  await page.confirmMember({detail:{ids:[memberId]}});assert.equal(queries.at(-1).member_id,memberId);assert.equal(queries.at(-1).team_id,undefined);
  page.data.opportunityOnly=true;await page.changeScope({detail:{value:'self'}});assert.equal(queries.at(-1).member_id,undefined);assert.equal(queries.at(-1).team_id,undefined);assert.equal(queries.at(-1).opportunity_only,true);
  const old=harness('risks',{listRisks:async()=>{throw Error('should not query');}}).page;old.onLoad({team:'南区'});await old.loadRisks();assert.match(old.data.loadError,/链接已过期/);
});

test('风险缺少服务端统计为错误；切账号立即清除旧范围、统计和目录',async()=>{
  const pending=deferred();const h=harness('risks',{listRisks:async()=>({items:[row()]})});
  await h.page.loadRisks();assert.match(h.page.data.loadError,/不完整/);assert.equal(h.page.data.summary,null);
  const changed=harness('risks',{listRisks:()=>pending.promise});changed.page._riskIdentity='old';Object.assign(changed.page.data,{summary:{total:99},memberOptions:[{id:memberId}],selectedMemberId:memberId,scopeMode:'person'});
  const loading=changed.page.loadRisks();assert.equal(changed.page.data.summary,null);assert.equal(changed.page.data.memberOptions.length,0);assert.equal(changed.page.data.selectedMemberId,'');
  pending.resolve(result({offset:0,page_size:50}));await loading;assert.equal(changed.page.data.summary.total,111);
});

test('采纳只记录建议，双击及解除并发被阻止，说明在途不可改',async()=>{
  const pending=deferred();let posts=0;const h=harness('risk-detail',{getRisk:async()=>row(),decideRisk:async(id,payload)=>{posts++;assert.equal(id,riskId);assert.equal(payload.version_no,4);return pending.promise;}});
  await h.page.onShow();h.page.chooseDecision({detail:{value:'adopt'}});h.page.inputDecisionNote({detail:{value:' 核实后采用 '}});
  const saving=h.page.confirmDecision();await h.page.confirmDecision();h.page.confirmResolve();h.page.inputDecisionNote({detail:{value:'另一个说明'}});
  assert.equal(posts,1);assert.equal(h.modals.length,0);assert.equal(h.page.data.decisionNote,' 核实后采用 ');
  pending.resolve({risk:row({version_no:5,current_decision:{decision:'adopt',note:'核实后采用',risk_version:5,is_current:true}})});await saving;
  assert.equal(h.page.data.risk.status,'accepted');assert.equal(h.page.data.risk.current_decision.note,'核实后采用');assert.equal(h.page.data.decisionSubmitting,false);
});

test('建议超时保留原body；同内容重试沿旧意图，改稿或显式刷新才换意图',async()=>{
  const calls=[];const h=harness('risk-detail',{getRisk:async()=>row(),decideRisk:async(id,payload,options)=>{calls.push({payload,options});throw Error('timeout');}});
  await h.page.onShow();h.page.chooseDecision({detail:{value:'defer'}});h.page.inputDecisionNote({detail:{value:'等待预算'}});
  await h.page.confirmDecision();await h.page.confirmDecision();assert.deepEqual(calls[0].payload,calls[1].payload);assert.equal(calls[0].options.freshIntent,true);assert.equal(calls[1].options.freshIntent,false);
  h.page.inputDecisionNote({detail:{value:'补充原因'}});await h.page.confirmDecision();assert.equal(calls[2].options.freshIntent,true);
  await h.page.refreshRisk();await h.page.confirmDecision();assert.equal(calls[3].options.freshIntent,true);
});

test('409锁住两种写入，改稿、普通回读或失败刷新都不能解锁',async()=>{
  let posts=0,failRead=false;const h=harness('risk-detail',{getRisk:async()=>{if(failRead)throw Error('断网');return row();},decideRisk:async()=>{posts++;throw Object.assign(Error('stale'),{statusCode:409});}});
  await h.page.onShow();h.page.chooseDecision({detail:{value:'adopt'}});h.page.inputDecisionNote({detail:{value:'采用'}});h.page.data.resolutionNote='事实已核实解除';
  await h.page.confirmDecision();assert.equal(h.page.data.refreshRequired,true);
  h.page.inputDecisionNote({detail:{value:'新意见'}});await h.page.confirmDecision();h.page.confirmResolve();assert.equal(posts,1);assert.equal(h.modals.length,0);
  await h.page.loadRisk();assert.equal(h.page.data.refreshRequired,true);failRead=true;await h.page.refreshRisk();assert.equal(h.page.data.refreshRequired,true);
  failRead=false;await h.page.refreshRisk();assert.equal(h.page.data.refreshRequired,false);h.page.confirmResolve();assert.equal(h.modals.length,1);
});

test('解除409同样保持锁定且必须显式刷新，提交带准确版本',async()=>{
  const h=harness('risk-detail',{getRisk:async()=>row(),resolveRisk:async(id,note,version)=>{assert.equal(version,4);throw Object.assign(Error('stale'),{statusCode:409});}});
  await h.page.onShow();h.page.data.resolutionNote='事实已核实解除';h.page.confirmResolve();h.modals[0].success({confirm:true});await tick();
  assert.equal(h.page.data.refreshRequired,true);assert.equal(h.page.data.submitting,false);h.page.confirmResolve();assert.equal(h.modals.length,1);
});

test('详情未知对象权限、旧版本和切账号不能提交；拒绝错误风险回包',async()=>{
  for(const extra of [{can_decide:false},{can_decide:undefined},{version_no:undefined},{status:'resolved'}]) {
    let posts=0;const h=harness('risk-detail',{getRisk:async()=>row(extra),decideRisk:async()=>posts++});await h.page.onShow();h.page.data.decisionChoice='adopt';h.page.data.decisionNote='确认';await h.page.confirmDecision();assert.equal(posts,0);
  }
  let posts=0;const h=harness('risk-detail',{getRisk:async()=>row(),decideRisk:async()=>posts++});await h.page.onShow();h.app.globalData.session.userId='new';h.page.data.decisionChoice='adopt';h.page.data.decisionNote='确认';await h.page.confirmDecision();assert.equal(posts,0);
  const wrong=harness('risk-detail',{getRisk:async()=>row({id:teamId})});await wrong.page.onShow();assert.equal(wrong.page.data.risk,null);assert.match(wrong.page.data.loadError,/不一致/);
});

test('建议离页迟到响应不覆盖新身份，原身份返回在途保持互斥并在结算后回读',async()=>{
  const pending=deferred();let reads=0,latest=false;const h=harness('risk-detail',{getRisk:async()=>{reads++;return row({version_no:latest?5:4});},decideRisk:()=>pending.promise});
  await h.page.onShow();h.page.data.decisionChoice='adopt';h.page.data.decisionNote='采用';const saving=h.page.confirmDecision();h.page.onHide();await h.page.onShow();
  assert.equal(h.page.data.decisionSubmitting,true);h.page.confirmResolve();assert.equal(h.modals.length,0);
  latest=true;pending.resolve({risk:row({version_no:5})});await saving;await tick();assert.equal(reads,3);assert.equal(h.page.data.risk.version_no,5);assert.equal(h.page.data.decisionSubmitting,false);
});

function transport(mode='timeout') {
  const storage=new Map(),requests=[];const control={mode};
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{
    requests.push(o);if(control.mode==='hold')return;
    if(control.mode==='timeout')o.fail({errMsg:'timeout'});else o.success({statusCode:control.mode==='conflict'?409:200,data:control.mode==='conflict'?{message:'stale'}:{risk:row({version_no:5}),decision_event_id:'event'}});
  }};
  const load=()=>{delete require.cache[require.resolve('../miniprogram/utils/apiClient')];return require('../miniprogram/utils/apiClient');};
  const api=load();api.saveAuth({access_token:'synthetic-only',actor:{workspace_id:'w',user_id:'u'}});
  return {api,load,storage,requests,control};
}
const decision={decision:'adopt',note:'采用',version_no:4};
test('风险API持久化request_id，超时跨加载同body同键，显式新意图换键且并发不覆盖收据',async()=>{
  const h=transport();await assert.rejects(h.api.decideRisk(riskId,decision),/timeout/);const first=h.requests[0].data.request_id;
  assert.match(first,/^[0-9a-f-]{36}$/);assert.equal(h.requests[0].header['Idempotency-Key'],first);
  const api=h.load();await assert.rejects(api.decideRisk(riskId,decision),/timeout/);assert.equal(h.requests[1].data.request_id,first);
  h.control.mode='hold';const a=api.decideRisk(riskId,decision,{freshIntent:true}), b=api.decideRisk(riskId,decision,{freshIntent:true});assert.equal(h.requests.length,3);
  const next=h.requests[2].data.request_id;assert.notEqual(next,first);assert.ok(Object.values(h.storage.get(identity.STORAGE_KEY)).includes(next));
  h.requests[2].success({statusCode:200,data:{risk:row({version_no:5})}});await Promise.all([a,b]);assert.equal(Object.values(h.storage.get(identity.STORAGE_KEY)).length,0);
});

test('风险API范围编码、版本校验、409透传及身份隔离；不存业务说明',async()=>{
  const h=transport();await assert.rejects(h.api.resolveRisk(riskId,'解除依据'),/版本缺失/);assert.equal(h.requests.length,0);
  await assert.rejects(h.api.decideRisk(riskId,decision),/timeout/);const first=h.requests[0].data.request_id;
  assert.ok(!JSON.stringify(h.storage.get(identity.STORAGE_KEY)).includes('采用'));
  h.api.saveAuth({access_token:'synthetic-b',actor:{workspace_id:'w',user_id:'b'}});await assert.rejects(h.api.decideRisk(riskId,decision),/timeout/);assert.notEqual(h.requests[1].data.request_id,first);
  h.control.mode='conflict';await assert.rejects(h.api.decideRisk(riskId,decision),e=>e.statusCode===409);assert.equal(h.requests.length,3);
  h.control.mode='success';await h.api.listRisks({status:'open',scope:'person',member_id:memberId,opportunity_only:true,page_size:50,offset:50});
  const query=new URL(h.requests[3].url).searchParams;assert.equal(query.get('member_id'),memberId);assert.equal(query.get('offset'),'50');assert.equal(query.get('opportunity_only'),'true');
  await h.api.resolveRisk(riskId,'  已确认风险解除 ',4);assert.equal(h.requests[4].data.version_no,4);assert.equal(h.requests[4].data.resolution_note,'已确认风险解除');assert.equal(h.requests[4].header['Idempotency-Key'],undefined);
});

test('风险建议无法保存提交标识就不发送POST，读取不产生标识',async()=>{
  const h=transport('success'),write=global.wx.setStorageSync;
  global.wx.setStorageSync=(key,value)=>{if(key===identity.STORAGE_KEY)throw Error('quota');write(key,value);};
  await assert.rejects(h.api.decideRisk(riskId,decision),/无法保存提交标识/);assert.equal(h.requests.length,0);
  await h.api.getRisk(riskId,{fresh:true});assert.equal(h.requests.length,1);assert.equal(h.requests[0].header['Idempotency-Key'],undefined);
});

test('当前末页风险减少后回到有效页，不展示越界页或把完整总数清零',async()=>{
  const offsets=[];const {page}=harness('risks',{listRisks:async q=>{offsets.push(q.offset);return result(q,{items:q.offset?[]:[row()],total:1,summary:{total:1,open_count:1,resolved_count:0,overdue_count:0},has_more:false,next_offset:null});}});
  page.data.offset=100;await page.loadRisks();assert.deepEqual(offsets,[100,0]);assert.equal(page.data.page,1);assert.equal(page.data.total,1);assert.equal(page.data.loading,false);
});

test('迟到409在离页返回后仍锁定当前风险；切账号后丢弃旧写响应',async()=>{
  const pending=deferred();const h=harness('risk-detail',{getRisk:async()=>row(),decideRisk:()=>pending.promise});
  await h.page.onShow();h.page.data.decisionChoice='adopt';h.page.data.decisionNote='确认';const saving=h.page.confirmDecision();h.page.onHide();await h.page.onShow();
  pending.reject(Object.assign(Error('stale'),{statusCode:409}));await saving;assert.equal(h.page.data.refreshRequired,true);assert.equal(h.page.data.decisionSubmitting,false);
  const second=deferred();const other=harness('risk-detail',{getRisk:async()=>row(),decideRisk:()=>second.promise});
  await other.page.onShow();other.page.data.decisionChoice='adopt';other.page.data.decisionNote='原账号';const old=other.page.confirmDecision();other.app.globalData.session.userId='next';await other.page.onShow();
  second.resolve({risk:row({title:'旧账号秘密',version_no:99})});await old;assert.notEqual(other.page.data.risk.title,'旧账号秘密');assert.equal(other.page.data.decisionNote,'');assert.equal(other.page.data.decisionSubmitting,false);
});

test('共享风险样例按设备本地时区展示，UTC与北京时间均保留业务和权限口径',()=>{
  const fixture=require('./helpers/risk-shared-v1.json');
  // Isolate TZ per process so other tests keep their own device timezone.
  for(const [tz,time]of [['Asia/Shanghai','2026年10月10日 09:00'],['UTC','2026年10月10日 01:00']]){
    const actual=JSON.parse(execFileSync(process.execPath,['-e',`
      const {normalizeRisk}=require('./miniprogram/utils/riskDisplay');
      const fixture=require('./tests/helpers/risk-shared-v1.json');
      process.stdout.write(JSON.stringify({
        detail:normalizeRisk(fixture.input.detail,fixture.input.session),
        afterDecision:normalizeRisk(fixture.input.decision.risk,fixture.input.session)
      }));
    `],{cwd:path.resolve(__dirname,'..'),env:{...process.env,TZ:tz},encoding:'utf8',timeout:10000}));
    assert.deepEqual(actual.detail,fixture.data.detail,tz);
    assert.deepEqual(actual.afterDecision,{
      ...fixture.data.afterDecision,decisionTime:time,
      historyRows:fixture.data.afterDecision.historyRows.map(event=>({...event,time})),
    },tz);
  }
  assert.equal(fixture.data.detail.canDecide,true);assert.equal(fixture.data.detail.canResolve,false);assert.equal(fixture.data.afterDecision.status,'accepted');
  const {normalizeCustomerDetail}=require('../miniprogram/utils/customerDetail');
  // The old-API fallback must keep an accepted risk visible without a summary projection.
  assert.equal(normalizeCustomerDetail({id:'customer',name:'合成客户',risks:[row({title:'已接受仍需关注'})]}).risk,'已接受仍需关注');
});
