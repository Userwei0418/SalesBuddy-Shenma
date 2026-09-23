// Mirrors the canonical visit-flow OpenAPI fields; identity stays outside model fields.
const keys = ['follow_up_record','next_action','customer_name','customer_type','opportunity_name',
  'partner_name','interaction_at','created_date','contact_name','contact_title','interaction_mode',
  'visit_location','visit_goal','customer_main_business','customer_needs','customer_budget','contact_role'];
function fields(data) {
  const values = data.values || {}, result = {};
  keys.forEach(key => { result[key] = String(values[key] || '').trim(); });
  result.customer_name = data.customerName || result.customer_name;
  result.customer_type = data.customerType || '客户';
  result.is_first_visit = data.isFirstVisit === true;
  const selected=(data.opportunityOptions || []).find(row=>row.id===data.opportunityId);
  result.opportunity_name = data.opportunityId === '__new__'
    ? (data.opportunityDraft || {}).name || '' : data.opportunityId ? (selected || {}).name || result.opportunity_name : '';
  return result;
}
function signature(data) {
  return JSON.stringify({fields:fields(data), customerId:data.customerId, opportunityId:data.opportunityId || '',
    draft:data.opportunityEditing ? data.opportunityDraft : null,
    collaborators:[...(data.collaboratorIds || [])].sort(), source:data.sourceImportId || null});
}
module.exports={fields,signature};
