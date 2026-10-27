// A recording reference is not a customer/ opportunity detail or edit grant.
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
function read(value, {opportunityId, customerId} = {}) {
  const customer = value && value.customer_reference;
  if (!value || !uuid(value.opportunity_id) || typeof value.name !== 'string' ||
      !customer || !uuid(customer.id) || typeof customer.name !== 'string' || typeof value.can_follow_up !== 'boolean' ||
      (opportunityId && value.opportunity_id !== opportunityId) || (customerId && customer.id !== customerId)) {
    throw Error('商机跟进信息不完整或关联已变化，请重新选择');
  }
  if (!value.can_follow_up) throw Error('该商机当前不可跟进，请保留草稿并重新选择');
  return {opportunity_id:value.opportunity_id,name:value.name,customer_reference:{id:customer.id,name:customer.name},can_follow_up:true};
}
function page(value, {offset = 0, limit = 20, customerId} = {}) {
  if (!value || !Array.isArray(value.items) || !Number.isSafeInteger(value.total) || value.total < 0 ||
      value.limit !== limit || value.offset !== offset || value.items.length > limit) throw Error('商机目录响应不完整，请重试');
  const items = value.items.map(row => read(row, {customerId}));
  if (new Set(items.map(row => row.opportunity_id)).size !== items.length || offset + items.length > value.total && items.length) throw Error('商机目录响应不完整，请重试');
  const next = offset + items.length, hasMore = items.length === limit && next < value.total;
  return {items,total:value.total,offset,limit,hasMore,nextOffset:hasMore?next:null};
}
function customers(value, {offset = 0, limit = 20} = {}) {
  if (!value || !Array.isArray(value.items) || !Number.isSafeInteger(value.total) || value.total < 0 ||
      value.limit !== limit || value.offset !== offset || value.items.length > limit ||
      value.items.some(row => !row || !uuid(row.id) || typeof row.name !== 'string') ||
      new Set(value.items.map(row=>row.id)).size !== value.items.length || value.items.length && offset + value.items.length > value.total) throw Error('跟进客户目录响应不完整，请重试');
  const items=value.items.map(row=>({id:row.id,name:row.name})),next=offset+items.length,hasMore=items.length===limit&&next<value.total;
  return {items,total:value.total,offset,limit,hasMore,nextOffset:hasMore?next:null};
}
function customerAccess(value, {customerId} = {}) {
  const customer=value && value.customer_reference;
  if(!customer || !uuid(customer.id) || typeof customer.name!=='string' || typeof value.can_follow_up!=='boolean' || (customerId && customer.id!==customerId))throw Error('客户跟进信息不完整或关联已变化，请重新选择');
  if(!value.can_follow_up)throw Error('该客户当前不可跟进，请保留草稿并重新选择');
  return {customer_reference:{id:customer.id,name:customer.name},can_follow_up:true};
}
module.exports = {read,page,customers,customerAccess};
