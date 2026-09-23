const test=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const identity=require('../miniprogram/utils/requestIdentity');

test('request fingerprints match SHA-256 for empty, Unicode and long visit content',()=>{
  for(const input of ['', 'abc', '客户拜访：😀\n下一步行动', '拜访目标'.repeat(20000)]) {
    assert.equal(identity.sha256(input),crypto.createHash('sha256').update(input).digest('hex'));
  }
});

test('uncertain submit survives reload, scopes by actor, and new successful intent uses a new key',async()=>{
  const storage=new Map(), requests=[];
  let mode='timeout';
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    request:o=>{requests.push(o);if(mode==='timeout')o.fail({errMsg:'timeout'});else o.success({statusCode:201,data:{id:'database-row'}});}};
  const load=()=>{delete require.cache[require.resolve('../miniprogram/utils/apiClient')];return require('../miniprogram/utils/apiClient');};
  let api=load();
  api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
  const options={path:'/tasks',method:'POST',data:{description:'任务',priority_code:'high'}};
  await assert.rejects(api.request(options),/timeout/);
  const first=requests[0].header['Idempotency-Key'];
  assert.match(first,/^[a-f0-9-]{36}$/);
  assert.equal(JSON.stringify(storage.get(identity.STORAGE_KEY)).includes('任务'),false);
  api=load();mode='success';await api.request({...options,data:{priority_code:'high',description:'任务'}});
  assert.equal(requests[1].header['Idempotency-Key'],first);
  await api.request(options);assert.notEqual(requests[2].header['Idempotency-Key'],first);
  mode='timeout';await assert.rejects(api.request(options));const old=requests[3].header['Idempotency-Key'];
  api.saveAuth({access_token:'test-only-b',actor:{workspace_id:'w',user_id:'b'}});
  await assert.rejects(api.request(options));assert.notEqual(requests[4].header['Idempotency-Key'],old);
});

test('concurrent taps share a request, refresh retains key, and ordinary reads never receive a key',async()=>{
  const storage=new Map(), requests=[];let deliver;
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    request:o=>{requests.push(o);deliver=o;}};
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');
  const actor={workspace_id:'w',user_id:'a'};
  api.saveAuth({access_token:'expired-test',refresh_token:'test-refresh',actor});
  const a=api.request({path:'/visits',method:'POST',data:{fields:{visit_goal:'了解需求'}}});
  const b=api.request({path:'/visits',method:'POST',data:{fields:{visit_goal:'了解需求'}}});
  assert.equal(requests.length,1);const key=deliver.header['Idempotency-Key'];
  deliver.success({statusCode:401,data:{}});await new Promise(r=>setImmediate(r));
  assert.match(deliver.url,/auth\/refresh$/);
  deliver.success({statusCode:200,data:{access_token:'fresh-test',refresh_token:'test-refresh',actor}});
  await new Promise(r=>setImmediate(r));assert.equal(deliver.header['Idempotency-Key'],key);
  deliver.success({statusCode:201,data:{id:'v'}});assert.deepEqual(await Promise.all([a,b]),[{id:'v'},{id:'v'}]);
  const read=api.request({path:'/tasks'});assert.equal(deliver.header['Idempotency-Key'],undefined);
  deliver.success({statusCode:200,data:{items:[]}});await read;
});

test('parallel reads share one transport but not mutable results or subsequent refreshes',async()=>{
 const storage=new Map(),requests=[];
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>requests.push(o)};
 delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');
 api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
 const a=api.request({path:'/tasks'}),b=api.request({path:'/tasks'});
 assert.equal(requests.length,1);requests[0].success({statusCode:200,data:{items:[{name:'database'}]}});
 const [left,right]=await Promise.all([a,b]);left.items[0].name='changed locally';assert.equal(right.items[0].name,'database');
 const fresh=api.request({path:'/tasks'});assert.equal(requests.length,2);
 requests[1].success({statusCode:200,data:{items:[]}});await fresh;
});

test('a mutation or identity change prevents sharing an earlier read',async()=>{
 const storage=new Map(),requests=[];
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>requests.push(o)};
 delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');
 api.saveAuth({access_token:'test-a',actor:{workspace_id:'w',user_id:'a'}});
 const before=api.request({path:'/tasks'});const write=api.request({path:'/tasks',method:'POST',data:{description:'new'}});
 const after=api.request({path:'/tasks'});assert.equal(requests.length,3);
 requests[1].success({statusCode:201,data:{id:'t'}});await write;
 requests[0].success({statusCode:200,data:{items:[]}});await before;
 api.saveAuth({access_token:'test-b',actor:{workspace_id:'w',user_id:'b'}});
 const late=assert.rejects(after,/登录状态已变更/);
 const other=api.request({path:'/tasks'});assert.equal(requests.length,4);
 requests[2].success({statusCode:200,data:{items:['old']}});await late;
 requests[3].success({statusCode:200,data:{items:['current']}});assert.deepEqual((await other).items,['current']);
});


test('a read begun during a write cannot be shared by a post-commit refresh',async()=>{
 const storage=new Map(),requests=[];
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>requests.push(o)};
 delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');
 api.saveAuth({access_token:'test-a',actor:{workspace_id:'w',user_id:'a'}});
 const write=api.request({path:'/tasks',method:'POST',data:{description:'new'}});
 const during=api.request({path:'/tasks'});
 requests[0].success({statusCode:201,data:{id:'t'}});await write;
 const after=api.request({path:'/tasks'});assert.equal(requests.length,3);
 requests[1].success({statusCode:200,data:{items:[]}});
 requests[2].success({statusCode:200,data:{items:[{id:'t'}]}});
 assert.equal((await during).items.length,0);assert.equal((await after).items[0].id,'t');
 const failed=api.request({path:'/tasks'});requests[3].fail({errMsg:'timeout'});await assert.rejects(failed);
 const retry=api.request({path:'/tasks'});assert.equal(requests.length,5);
 requests[4].success({statusCode:200,data:{items:[]}});await retry;
});
