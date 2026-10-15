const test=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const identity=require('../miniprogram/utils/requestIdentity');

test('工作报告POST/PUT按完整路径段进入幂等机制，读取与相似前缀不进入',()=>{
  for(const method of ['POST','PUT']) for(const path of ['/work-reports','/work-reports/report-id','/work-reports/report-id/share','/work-reports?mode=draft','/work-reports/report-id?mode=draft']) {
    assert.equal(identity.supports({method,path}),true,`${method} ${path}`);
  }
  for(const [method,path] of [['GET','/work-reports'],['GET','/work-reports/report-id'],['PATCH','/work-reports/report-id'],['DELETE','/work-reports/report-id'],['POST','/work-reports-legacy'],['PUT','/work-reports-other/report-id'],['POST','/other/work-reports']]) {
    assert.equal(identity.supports({method,path}),false,`${method} ${path}`);
  }
});

test('商机字段/人员仅约定写方法受保护，视图查询和字段目录保持只读',()=>{
  for(const [method,path] of [['PATCH','/opportunities/o/fields'],['PUT','/opportunities/o/people'],['PUT','/opportunities/o/fde-members'],['PUT','/opportunity-views/preference']]) {
    assert.equal(identity.supports({method,path}),true,`${method} ${path}`);
  }
  for(const [method,path] of [['GET','/opportunities/o/fields'],['POST','/opportunities/o/fields'],['PATCH','/opportunities/o/people'],['GET','/opportunities/field-options'],['GET','/directory/opportunity-members'],['POST','/opportunity-views/query'],['GET','/opportunity-views/preference'],['PATCH','/opportunities/o/fields/extra']]) {
    assert.equal(identity.supports({method,path}),false,`${method} ${path}`);
  }
});

for(const [method,path,data] of [
  ['PATCH','/opportunities/o/fields',{version_no:7,reason:'补充记录',values:{summary:'客户确认试用',is_key:false}}],
  ['PUT','/opportunities/o/people',{version_no:7,reason:'维护人员',primary_fde_id:null,participants:[]}],
  ['PUT','/opportunity-views/preference',{version_no:0,default_view_id:null}],
  ['POST','/work-reports',{version_no:0,period:'week',title:'本地测试报告'}],
  ['PUT','/work-reports/report-id',{version_no:3,title:'本地测试报告',content:'测试修改'}]
]) test(`${method} ${path} 超时/重载沿用幂等键，版本或账号变化生成新意图`,async()=>{
  const storage=new Map(),requests=[];let timeout=true;
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{requests.push(o);if(timeout)o.fail({errMsg:'timeout'});else o.success({statusCode:200,data:{changed:true,version_no:8}});}};
  const load=()=>{delete require.cache[require.resolve('../miniprogram/utils/apiClient')];return require('../miniprogram/utils/apiClient');};
  let api=load();api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
  await assert.rejects(api.request({method,path,data}),/timeout/);const key=requests[0].header['Idempotency-Key'];assert.match(key,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  api=load();await assert.rejects(api.request({method,path,data}),/timeout/);assert.equal(requests[1].header['Idempotency-Key'],key);
  await assert.rejects(api.request({method,path,data:{...data,version_no:data.version_no+1}}),/timeout/);assert.notEqual(requests[2].header['Idempotency-Key'],key);
  timeout=false;await api.request({method,path,data});assert.equal(requests[3].header['Idempotency-Key'],key);
  await api.request({method,path,data});assert.notEqual(requests[4].header['Idempotency-Key'],key);
  api.saveAuth({access_token:'test-b',actor:{workspace_id:'w',user_id:'b'}});
  await api.request({method,path,data});assert.notEqual(requests[5].header['Idempotency-Key'],key);
});

test('工作报告显式同键不同内容的409透传且不自动重试',async()=>{
  const storage=new Map(),requests=[];let conflict=false;
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{requests.push(o);if(!conflict)o.fail({errMsg:'timeout'});else o.success({statusCode:409,data:{message:'幂等键已用于不同内容'}});}};
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');
  api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
  const idempotencyKey='12345678-1234-4234-8234-123456789abc';
  const options={method:'PUT',path:'/work-reports/report-id',idempotencyKey,data:{version_no:3,content:'原测试内容'}};
  await assert.rejects(api.request(options),/timeout/);
  conflict=true;
  await assert.rejects(api.request({...options,data:{version_no:3,content:'另一份测试内容'}}),error=>error.statusCode===409);
  assert.equal(requests.length,2);
  assert.ok(requests.every(r=>r.header['Idempotency-Key']===idempotencyKey));
  assert.equal(requests[1].data.version_no,3);
});

test('工作报告无法持久保存提交键时不发送变更，GET不创建提交键',async()=>{
  const storage=new Map(),requests=[];
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>{if(k===identity.STORAGE_KEY)throw Error('quota');storage.set(k,v);},removeStorageSync:k=>storage.delete(k),request:o=>{requests.push(o);o.success({statusCode:200,data:{items:[]}});}};
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');
  api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
  await assert.rejects(api.request({method:'POST',path:'/work-reports',data:{version_no:0}}),/无法保存提交标识/);
  assert.equal(requests.length,0);
  await api.request({method:'GET',path:'/work-reports'});
  assert.equal(requests.length,1);
  assert.equal(requests[0].header['Idempotency-Key'],undefined);
  assert.equal(storage.has(identity.STORAGE_KEY),false);
});

test('POST视图查询及GET字段读取不创建写幂等记录',async()=>{
  const storage=new Map(),requests=[];
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{requests.push(o);o.success({statusCode:200,data:{items:[]}});}};
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');
  api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
  await api.request({method:'POST',path:'/opportunity-views/query',data:{config:{schema_version:2},offset:0,limit:50}});
  await api.request({method:'GET',path:'/opportunities/o/fields'});
  assert.ok(requests.every(row=>row.header['Idempotency-Key']===undefined));
  assert.equal(storage.has(identity.STORAGE_KEY),false);
});

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


test('多人派发超时重试保留幂等标识和完整批次，重新加载也不丢失',async()=>{
 const storage=new Map(),requests=[];let fail=true;
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{requests.push(o);if(fail)o.fail({errMsg:'timeout'});else o.success({statusCode:201,data:{items:[{id:'a'},{id:'b'}]}});}};
 const load=()=>{delete require.cache[require.resolve('../miniprogram/utils/apiClient')];return require('../miniprogram/utils/apiClient');};
 let api=load();api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
 const inputs=['A001','B001'].map(assigneeAccount=>({assigneeAccount,description:'核对试点资料',dueAt:'2030-01-01T00:00:00Z',associationKind:'daily',priority:'高'}));
 await assert.rejects(api.createTasks(inputs),/timeout/);const key=requests[0].header['Idempotency-Key'];assert.ok(key);
 assert.ok(requests[0].url.endsWith('/tasks/batch'));assert.equal(requests[0].data.tasks.length,2);
 api=load();fail=false;assert.equal((await api.createTasks(inputs)).items.length,2);
 assert.equal(requests[1].header['Idempotency-Key'],key);assert.deepEqual(requests[1].data,requests[0].data);
});

for(const route of ['opportunity','visit'])test(`${route} 金额确认不改变业务指纹，商机未决锁定而拜访保持原重试契约`,async()=>{
 const storage=new Map(),requests=[];let timeout=true;
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
  request:o=>{requests.push(o);if(timeout)o.fail({errMsg:'timeout'});else o.success({statusCode:201,data:{id:route==='opportunity'?'00000000-0000-4000-8000-000000000001':'same-database-record',changed:true,version_no:1,event_id:'00000000-0000-4000-8000-000000000002'}});}};
 const load=()=>{delete require.cache[require.resolve('../miniprogram/utils/apiClient')];return require('../miniprogram/utils/apiClient');};
 let api=load();api.saveAuth({access_token:'test-only',actor:{workspace_id:'w',user_id:'a'}});
 const data=ack=>route==='opportunity'?{action:'create',amount:10000000,...ack}:{customer_id:'c',fields:{follow_up_record:'确认试点',_quality_review_run_id:'quality',_opportunity_mutation:{action:'create',amount:10000000,...ack}}};
 const path=route==='opportunity'?'/customers/c/opportunities':'/visits';
 const firstData=data({amount_confirmed_value:10000000});
 await assert.rejects(api.request({path,method:'POST',data:firstData}),/timeout/);
 const key=requests[0].header['Idempotency-Key'];
 assert.equal(route==='opportunity'?firstData.amount_confirmed_value:firstData.fields._opportunity_mutation.amount_confirmed_value,10000000,'fingerprinting must not mutate the actual acknowledgment');
 if(route==='opportunity'){
   const scope=['http://test','w','a'];
   assert.equal(identity.fingerprint(scope,{path,method:'POST',data:firstData}),identity.fingerprint(scope,{path,method:'POST',data:data({})}));
   await assert.rejects(api.request({path,method:'POST',data:data({amount:20000000})}),/结果待确认/);assert.equal(requests.length,1);
   timeout=false;await api.retryOpportunitySave('c');assert.equal(requests[1].header['Idempotency-Key'],key);
   await api.request({path,method:'POST',data:data({})});assert.notEqual(requests[2].header['Idempotency-Key'],key);
   return;
 }
 api=load();await assert.rejects(api.request({path,method:'POST',data:data({})}),/timeout/);
 assert.equal(requests[1].header['Idempotency-Key'],key,'threshold raised: retry without acknowledgment retains original key');
 await assert.rejects(api.request({path,method:'POST',data:data({amount_confirmed_value:10000000})}),/timeout/);
 assert.equal(requests[2].header['Idempotency-Key'],key,'threshold lowered: confirming again retains original key');
 const changed=data({amount_confirmed_value:20000000});if(route==='opportunity')changed.amount=20000000;else changed.fields._opportunity_mutation.amount=20000000;
 await assert.rejects(api.request({path,method:'POST',data:changed}),/timeout/);assert.notEqual(requests[3].header['Idempotency-Key'],key);
 timeout=false;assert.equal((await api.request({path,method:'POST',data:data({})})).id,'same-database-record');assert.equal(requests[4].header['Idempotency-Key'],key);
 await api.request({path,method:'POST',data:data({})});assert.notEqual(requests[5].header['Idempotency-Key'],key,'a successfully acknowledged new intent gets a fresh key');
});

test('金额确认摘要排除严格限定两个POST位置，其它业务字段/身份/版本仍区分',()=>{
 const storage=new Map();global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v)};
 const scope=['origin','workspace','actor'];const key=(path,method,data,s=scope)=>identity.begin(s,{path,method,data}).key;
 const payload={action:'update',amount:10000000,version_no:1,opportunity_id:'o'};
 const original=key('/customers/c/opportunities','POST',payload);
 assert.equal(key('/customers/c/opportunities','POST',{...payload,amount_confirmed_value:10000000}),original);
 for(const change of [{amount:20000000},{version_no:2},{opportunity_id:'other'},{name:'new name'}])assert.notEqual(key('/customers/c/opportunities','POST',{...payload,...change}),original);
 assert.notEqual(key('/customers/other/opportunities','POST',payload),original);
 assert.notEqual(key('/customers/c/opportunities','POST',payload,['origin','workspace','other']),original);
 for(const [path,method] of [['/tasks','POST'],['/customers/c','PATCH'],['/visits','PATCH'],['/customers/c/opportunities','PATCH']]) {
  assert.notEqual(key(path,method,payload),key(path,method,{...payload,amount_confirmed_value:10000000}));
 }
 const visit={customer_id:'c',fields:{_quality_review_run_id:'quality',_opportunity_mutation:payload}};
 const visitKey=key('/visits','POST',visit);
 assert.notEqual(key('/visits','POST',{...visit,amount_confirmed_value:10000000}),visitKey,'only nested opportunity acknowledgment may be omitted');
 assert.notEqual(key('/visits','POST',{...visit,fields:{...visit.fields,_quality_review_run_id:'other'}}),visitKey);
 assert.notEqual(key('/visits','POST',{...visit,customer_id:'other'}),visitKey);
});
