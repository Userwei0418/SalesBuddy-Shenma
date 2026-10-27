const test=require('node:test'),assert=require('node:assert/strict');
const contract=require('../miniprogram/utils/opportunityCollaboration');
const id=n=>`${String(n).padStart(8,'0')}-1111-4111-8111-000000000000`,oid=id(1),uid=id(2),key=id(3);
test('协作请求只允许版本与不重复UUID名单，空名单合法',()=>{
  assert.deepEqual(contract.body({version_no:7,member_ids:[]}),{version_no:7,member_ids:[]});
  for(const value of [{version_no:0,member_ids:[]},{version_no:'7',member_ids:[]},{version_no:7,member_ids:[uid,uid]},
    {version_no:7,member_ids:[],owner_id:uid},{version_no:7,member_ids:['invalid']},{version_no:7,member_ids:Array.from({length:31},(_,i)=>id(100+i))}])assert.throws(()=>contract.body(value));
});
test('回执须与本次名单/版本一致，不能接受丢失成员或错误版本',()=>{
  const body={version_no:7,member_ids:[uid]},result={changed:true,event_id:key,version_no:8,collaborators:[{user_id:uid,display_name:'成员'}]};
  assert.equal(contract.receipt(result,body).version_no,8);
  for(const patch of [{event_id:null},{version_no:7},{version_no:9},{collaborators:[]},{changed:'true'}])assert.throws(()=>contract.receipt({...result,...patch},body));
  assert.equal(contract.receipt({...result,changed:false,event_id:null,version_no:7},body).changed,false);
});
test('真实apiClient固定collaborate目的，PUT携带UUID键和严格body；读写均不访问客户接口',async()=>{
  const store=new Map(),requests=[];global.wx={getStorageSync:k=>store.get(k),setStorageSync:(k,v)=>store.set(k,v),removeStorageSync:k=>store.delete(k),
    request:options=>{requests.push(options);options.success({statusCode:200,data:{}});}};
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');
  api.saveAuth({access_token:'synthetic-test',actor:{workspace_id:id(8),user_id:id(9)}});
  await api.getOpportunityCollaborationCandidates(oid,{q:'售前',purpose:'transfer',limit:20,offset:20});
  assert.match(requests[0].url,/purpose=collaborate/);assert.match(requests[0].url,/offset=20/);assert.doesNotMatch(requests[0].url,/transfer|\/customers/);
  await api.saveOpportunityCollaborators(oid,{version_no:7,member_ids:[uid]},key);
  assert.equal(requests[1].method,'PUT');assert.match(requests[1].url,new RegExp('/opportunities/'+oid+'/collaborators$'));
  assert.equal(requests[1].header['Idempotency-Key'],key);assert.deepEqual(requests[1].data,{version_no:7,member_ids:[uid]});
  await assert.rejects(api.saveOpportunityCollaborators(oid,{version_no:7,member_ids:[uid]},''));
  await assert.rejects(api.saveOpportunityCollaborators(oid,{version_no:7,member_ids:[uid],primary_fde_id:uid},key));assert.equal(requests.length,2);
});
