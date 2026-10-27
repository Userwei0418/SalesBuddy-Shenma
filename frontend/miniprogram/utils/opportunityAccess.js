// V1.2.2 object access contract. No HTTP or global-permission fallback here.
const CAPABILITIES=['can_transfer','can_manage_collaborators','can_follow_up','can_edit'];
const TRANSFER_REASONS=['TRANSFER_NOT_READY','OPPORTUNITY_ACCESS_DENIED','TRANSFER_STATE_NOT_ALLOWED','TRANSFER_ATTRIBUTION_UNRESOLVED'];
const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const integer=(v,min)=>Number.isSafeInteger(v)&&v>=min;
const invalid=()=>{throw Error('商机权限信息不完整，请刷新后重试');};
function capabilities(value){
 const source=value&&value.contract_version===1&&value.capabilities;
 const valid=source&&CAPABILITIES.every(key=>typeof source[key]==='boolean');
 return Object.fromEntries(CAPABILITIES.map(key=>[key,!!valid&&source[key]]));
}
function read(value,{opportunityId,customerId,detail=true}={}){
 if(!value||value.contract_version!==1||!uuid(value.opportunity_id)||typeof value.name!=='string'||
   !integer(value.version_no,1)||typeof value.status!=='string'||!(value.stage_code===null||typeof value.stage_code==='string')||
   !value.capabilities||!CAPABILITIES.every(key=>typeof value.capabilities[key]==='boolean'))invalid();
 const customer=value.customer_reference,owner=value.owner,reasons=value.capability_reasons;
 if(!customer||!uuid(customer.id)||typeof customer.name!=='string'||
   (opportunityId&&value.opportunity_id!==opportunityId)||(customerId&&customer.id!==customerId))invalid();
 if(!owner||!['user_id','team_id'].every(k=>owner[k]===null||uuid(owner[k]))||
   !['display_name','team_name'].every(k=>owner[k]===null||typeof owner[k]==='string')||
   !reasons||!(reasons.transfer===null||TRANSFER_REASONS.includes(reasons.transfer)))invalid();
 const result={contract_version:1,opportunity_id:value.opportunity_id,name:value.name,
  stage_code:value.stage_code,status:value.status,version_no:value.version_no,
  owner:{user_id:owner.user_id,display_name:owner.display_name,team_id:owner.team_id,team_name:owner.team_name},
  customer_reference:{id:customer.id,name:customer.name},capabilities:capabilities(value),capability_reasons:{transfer:reasons.transfer}};
 if(detail){
  if(!Array.isArray(value.collaborators)||value.collaborators.some(m=>!m||!uuid(m.user_id)||typeof m.display_name!=='string')||
    new Set(value.collaborators.map(m=>m.user_id)).size!==value.collaborators.length)invalid();
  result.collaborators=value.collaborators.map(m=>({user_id:m.user_id,display_name:m.display_name}));
 }
 return result;
}
function page(value,{offset=0,limit=20,customerId}={}){
 if(!value||value.contract_version!==1||!Array.isArray(value.items)||!integer(value.total,0)||
   value.offset!==offset||value.limit!==limit||!integer(offset,0)||!integer(limit,1)||limit>100||value.items.length>limit)invalid();
 const items=value.items.map(item=>read(item,{customerId,detail:false}));
 if(items.some(item=>!item.capabilities.can_follow_up)||new Set(items.map(item=>item.opportunity_id)).size!==items.length||
   (items.length&&offset+items.length>value.total))invalid();
 const next=offset+items.length,hasMore=items.length===limit && next<value.total;
 return {items,total:value.total,offset,limit,hasMore,nextOffset:hasMore?next:null};
}
module.exports={capabilities,read,page};
