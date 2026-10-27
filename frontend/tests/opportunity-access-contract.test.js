const test=require('node:test'),assert=require('node:assert/strict');
const access=require('../miniprogram/utils/opportunityAccess');
const oid='11111111-1111-1111-1111-111111111111',cid='22222222-2222-2222-2222-222222222222';
function row(){return {contract_version:1,opportunity_id:oid,name:'商机',stage_code:null,status:'open',version_no:1,
 owner:{user_id:null,display_name:null,team_id:null,team_name:null},collaborators:[],customer_reference:{id:cid,name:'客户'},
 capabilities:{can_follow_up:true,can_edit:false,can_transfer:false,can_manage_collaborators:false},capability_reasons:{transfer:'OPPORTUNITY_ACCESS_DENIED'}};}
test('跟进能力不提升编辑权限；未知合同或缺任一能力全部关闭',()=>{
 assert.equal(access.capabilities(row()).can_edit,false);assert.equal(access.capabilities(row()).can_follow_up,true);
 for(const change of [r=>r.contract_version=2,r=>delete r.capabilities.can_edit,r=>r.capabilities.can_edit='true']){
  const r=row();change(r);assert.ok(Object.values(access.capabilities(r)).every(v=>v===false));assert.throws(()=>access.read(r));
 }
});
test('仅投影最小客户及明确字段，不带兄弟商机或额外档案',()=>{
 const r=row();r.customer_reference.phone='private';r.opportunities=[{secret:true}];r.owner.extra='private';
 const actual=access.read(r,{opportunityId:oid,customerId:cid});
 assert.deepEqual(actual.customer_reference,{id:cid,name:'客户'});assert.equal(actual.opportunities,undefined);assert.equal(actual.owner.extra,undefined);
 assert.throws(()=>access.read(r,{opportunityId:cid}));assert.throws(()=>access.read(r,{customerId:oid}));
});
test('发现分页按total推进，不接受无权对象、错误页或空循环',()=>{
 const r=row(),p={contract_version:1,items:[r],total:2,limit:1,offset:0};
 assert.equal(access.page(p,{limit:1,customerId:cid}).nextOffset,1);
 assert.throws(()=>access.page(p,{limit:1,offset:1}));
 assert.equal(access.page({...p,items:[]},{limit:1}).hasMore,false);
 assert.equal(access.page({...p,items:[],offset:1},{limit:1,offset:1}).nextOffset,null);
 assert.equal(access.page({...p,limit:20,total:30}).hasMore,false);
 assert.throws(()=>access.page({...p,items:[{...r,capabilities:{...r.capabilities,can_follow_up:false}}]},{limit:1}));
 assert.deepEqual(access.page({...p,items:[],total:0},{limit:1}).items,[]);
});
test('同页重复、版本缺失和成员重复均拒绝；撤权后的合法只读回执可读',()=>{
 const r=row();assert.throws(()=>access.page({contract_version:1,items:[r,r],total:2,limit:20,offset:0}));
 assert.throws(()=>access.read({...r,version_no:0}));
 assert.throws(()=>access.read({...r,collaborators:[{user_id:cid,display_name:'同事'},{user_id:cid,display_name:'同事'}]}));
 assert.equal(access.read({...r,capabilities:{...r.capabilities,can_follow_up:false}}).capabilities.can_follow_up,false);
});
