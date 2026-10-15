// Tests the generated Web bundle, not the upstream Mini source or a live API.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import {historyView} from '../department-ui/profile-history.js';

const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = fs.readFileSync(path.resolve(root, process.env.SALES_WEB_DIST_DIR || 'dist', 'bundle.js'), 'utf8');
const reviewId = '00000000-0000-4000-8000-000000000001';
const taskId = '00000000-0000-4000-8000-000000000002';
function clientFixture({storage = new Map(), request = options => options.success({statusCode:200,data:{}})} = {}) {
  const requests = [], navigation = [];
  const session = {workspaceId:'synthetic-workspace',userId:'synthetic-member',role:'fde',permissions:{'profile.fde_read':true,'task.read':true}};
  const context = vm.createContext({window:{},console,setTimeout,clearTimeout,getApp:()=>({globalData:{session}}),wx:{
    getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),removeStorageSync:key=>storage.delete(key),
    request:options=>{requests.push(options);request(options);},navigateTo:options=>navigation.push(options.url),
  }});
  vm.runInContext(bundle,context);
  const cache = new Map();
  const load = (name, from='') => {
    const id = path.posix.normalize(name.startsWith('.') ? path.posix.join(path.posix.dirname(from),name) : name).replace(/\.js$/,'');
    if(cache.has(id))return cache.get(id).exports;
    const source = context.window.SALES_BUNDLE.modules[id];
    assert.equal(typeof source,'string',`generated dependency ${id} must exist`);
    const module = {exports:{}};cache.set(id,module);
    vm.runInContext(`(function(require,module,exports){${source}\n})`,context)(name=>load(name,id),module,module.exports);
    return module.exports;
  };
  const client = load('utils/apiClient');
  client.saveAuth({access_token:'SYNTHETIC-ONLY',actor:{workspace_id:session.workspaceId,user_id:session.userId}});
  return {client,load,requests,storage,session,navigation};
}

test('generated growth client retries one intent, resets after acknowledgement and freshly reads both evidence routes',async()=>{
  const storage = new Map(), requests = [];let uncertain = true;
  const options = {storage,request:request=>{
    requests.push(request);
    if(uncertain)request.fail({errMsg:'synthetic timeout'});
    else request.success({statusCode:200,data:{review_id:reviewId,status:'queued'}});
  }};
  let {client} = clientFixture(options);
  await assert.rejects(()=>client.ensureSalesGrowthReview(),/timeout/);
  const key = requests[0].header['Idempotency-Key'];assert.match(key,/^[0-9a-f-]{36}$/);
  ({client}=clientFixture(options));uncertain=false;
  await client.ensureSalesGrowthReview();assert.equal(requests[1].header['Idempotency-Key'],key);
  await client.ensureSalesGrowthReview();assert.notEqual(requests[2].header['Idempotency-Key'],key);
  for(const read of [()=>client.getSalesGrowthReview(reviewId),()=>client.getFdeProfileReview(reviewId)]){await read();await read();}
  assert.deepEqual(requests.map(row=>row.url),[
    ...Array(3).fill('/api/v1/profile/sales-growth/review'),
    ...Array(2).fill('/api/v1/profile/sales-growth/reviews/'+reviewId),
    ...Array(2).fill('/api/v1/fde/profile/reviews/'+reviewId),
  ]);
  for(const request of requests.slice(3)){assert.equal(request.method,'GET');assert.equal(request.header['Idempotency-Key'],undefined);}
});

test('imported projection preserves frozen null and rule metadata through Web history consumption',()=>{
  const {load}=clientFixture(),growth=load('utils/growthProfile');
  const score_basis={snapshot_status:'frozen',basis_id:'server-basis',framework_version:1};
  const row={id:reviewId,review_date:'2026-10-09',overall_score:95,score_summary:{value:null},score_basis,evaluation_state:'partially_assessed'};
  const data=growth.salesDisplay({contract_version:'sales.growth.v2',latest:row,history:[row],can_review:true});
  assert.equal(data.overallScore,'--');assert.equal(data.profileScoreInfo.value,null);
  assert.equal(historyView(data.history).rows[0].score,null);
  assert.equal(historyView(data.history).rows[0].rule,'server-basis');
  const fde=growth.historyPoints([{run_id:reviewId,review_date:'2026-10-08',reviewed_at:'2026-10-08T17:30:00Z',score_basis,dimensions:[{code:'record_quality',score:70}]}],'record_quality',true);
  const history=historyView(fde.map(pt=>({...pt,overall_score:pt.score})),'overall',true);
  assert.equal(history.rows[0].reviewId,reviewId);assert.equal(history.rows[0].score,70);
});

test('generated FDE evidence preserves its Web discriminator and routes only currently readable sources',async()=>{
  const fixture=clientFixture({request:request=>request.success({statusCode:200,data:{
    contract_version:'fde.profile.v1',assessment_kind:'fde_factual_portrait',
    review:{run_id:reviewId},evidence_summary:{total:2,readable:1,unavailable:1},
    evidence:[{source_type:'task',source_id:taskId,readable:true,detail:'synthetic public evidence'},
      {source_type:'task',source_id:'hidden-id',readable:false,detail:'MUST_NOT_LEAK'}],
  }})});
  const growth=fixture.load('utils/growthProfile');
  const view={data:{latestReviewId:reviewId,history:[]},visible:true,growthReadOwner:'owner',growthEvidenceContext:()=> 'owner',setData(patch){Object.assign(this.data,patch);},
    ...growth.evidenceMethods(fixture.client,()=>fixture.session,true)};
  await view.openGrowthEvidence({currentTarget:{dataset:{id:reviewId}}});
  const result=view.data.growthEvidence.result;
  assert.equal(result.assessment_kind,'fde_factual_portrait');assert.equal(result.contract_version,'fde.profile.v1');
  assert.equal(result.evidence[1].source_id,null);assert.equal(result.evidence[1].detail,null);
  assert.doesNotMatch(JSON.stringify(result),/MUST_NOT_LEAK|hidden-id/);
  const event={currentTarget:{dataset:{kind:'task',id:taskId}}};
  view.openGrowthSource(event);assert.deepEqual(fixture.navigation,['/pages/task-detail/index?id='+taskId]);
  fixture.session.permissions['task.read']=false;view.openGrowthSource(event);assert.equal(fixture.navigation.length,1);
  view.closeGrowthEvidence();view.openGrowthSource(event);assert.equal(fixture.navigation.length,1);
});
