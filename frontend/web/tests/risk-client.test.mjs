// Tests the generated Web bundle, not the upstream Mini source or a live API.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const bundle = fs.readFileSync(path.resolve(root, process.env.SALES_WEB_DIST_DIR || 'dist', 'bundle.js'), 'utf8');
const reviewId = '00000000-0000-4000-8000-000000000001';
const taskId = '00000000-0000-4000-8000-000000000002';
function clientFixture({storage = new Map(), request = options => options.success({statusCode:200,data:{}})} = {}) {
  const requests = [], navigation = [];
  const session = {workspaceId:'synthetic-workspace',userId:'synthetic-member',role:'sales',permissions:{'risk.read':true,'risk.decide':true}};
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

test('generated risk client binds decision request IDs to the body and retains uncertain retry across reload',async()=>{
  const storage=new Map(),requests=[];let timeout=true;
  const options={storage,request:request=>{requests.push(request);timeout?request.fail({errMsg:'synthetic timeout'}):request.success({statusCode:200,data:{risk:{id:reviewId,version_no:5},decision_event_id:taskId,idempotent_replay:false}});}};
  let {client}=clientFixture(options);
  const body={decision:'adopt',note:'确认预算审批时间',version_no:4};
  await assert.rejects(()=>client.decideRisk(reviewId,body),/timeout/);
  const first=requests[0];assert.match(first.data.request_id,/^[0-9a-f-]{36}$/);
  ({client}=clientFixture(options));timeout=false;
  await client.decideRisk(reviewId,body);assert.equal(requests[1].data.request_id,first.data.request_id);
  await client.decideRisk(reviewId,{...body,note:'采用新安排',version_no:5});assert.notEqual(requests[2].data.request_id,first.data.request_id);
  assert.ok(requests.every(r=>r.url.endsWith('/risks/'+reviewId+'/decision')),'不会请求task接口');
});

test('generated risk client encodes UUID selectors and forces explicit versions on closure',async()=>{
  const {client,requests}=clientFixture();
  await client.listRisks({scope:'person',member_id:reviewId,status:'open',page_size:50,offset:50,opportunity_only:true});
  const url=new URL(requests[0].url,'https://synthetic.invalid');
  assert.equal(url.searchParams.get('member_id'),reviewId);assert.equal(url.searchParams.get('offset'),'50');assert.equal(url.searchParams.get('opportunity_only'),'true');
  await assert.rejects(()=>client.resolveRisk(reviewId,'已收到书面审批'),/版本/);
  assert.equal(requests.length,1);
  await client.resolveRisk(reviewId,'  已收到书面审批  ',5);
  assert.equal(requests[1].data.version_no,5);assert.equal(requests[1].data.resolution_note,'已收到书面审批');
  await client.getRisk(reviewId,{fresh:true});await client.getRisk(reviewId,{fresh:true});assert.equal(requests.length,4);
});

