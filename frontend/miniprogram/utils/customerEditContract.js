const LABELS = {
  industry: '所属行业', customer_type: '客户类型', level_code: '客户优先级', source: '客户来源',
  partner_name: '关联合作伙伴', contact_name: '联系人姓名', contact_title: '联系人职位', contact_role: '联系人角色',
};
const LIMITS = {industry:100, customer_type:100, source:100, partner_name:200, contact_name:100, contact_title:100};
const CONTACTS = ['contact_name', 'contact_title', 'contact_role'];
const text = value => String(value || '').trim();

function payload(form, initial) {
  const result = {};
  for (const key of Object.keys(LABELS)) result[key] = text(form[key]);
  for (const key of ['industry', 'customer_type', 'level_code', 'source', 'partner_name']) {
    if (key==='customer_type' && Array.isArray(form.business_identities) && !form.business_identities.includes('customer')) continue;
    if (key==='partner_name' && Object.prototype.hasOwnProperty.call(form,'partner_id')) continue;
    if (!result[key]) throw Error(`请填写${LABELS[key]}`);
  }
  // Customer PATCH treats null as unchanged. It cannot clear an existing name/title.
  for (const key of CONTACTS) {
    if (initial && result[key] === text(initial[key])) delete result[key];
    else if (!result[key]) {
      if (initial && text(initial[key])) throw Error(`${LABELS[key]}暂不支持清空，请填写后保存`);
      delete result[key];
    }
  }
  const existingName = initial && text(initial.contact_name);
  const newName = text(form.contact_name);
  if (!existingName && !newName) {
    if (result.contact_title || (initial && result.contact_role)) throw Error('新增首要联系人时请填写姓名');
    delete result.contact_role;
  } else if (!existingName && newName && text(form.contact_role)) {
    result.contact_role = text(form.contact_role);
  }
  for (const [key, limit] of Object.entries(LIMITS)) {
    if (result[key] && [...result[key]].length > limit) throw Error(`${LABELS[key]}最多填写 ${limit} 字`);
  }
  if (!['Tier-1','Tier-2','Tier-3'].includes(result.level_code)) throw Error('请重新选择客户优先级');
  if (result.contact_role && !['使用者','影响者','决策者'].includes(result.contact_role)) throw Error('请重新选择联系人角色');
  for (const key of ['business_identities','partner_profile_id','partner_id']) {
    if (Object.prototype.hasOwnProperty.call(form,key)) result[key]=form[key];
  }
  if (Array.isArray(form.business_identities) && !form.business_identities.includes('customer')) result.customer_type=null;
  // Do not revalidate or rewrite a historical classification during an unrelated edit.
  if (initial && text(form.customer_type)===text(initial.customer_type)) delete result.customer_type;
  return result;
}

function errorMessage(error) {
  const labels = [];
  for (const item of Array.isArray(error && error.validationErrors) ? error.validationErrors : []) {
    if (!item || !Array.isArray(item.loc) || item.loc.length !== 2 || item.loc[0] !== 'body') continue;
    const key = item.loc[1];
    if (Object.prototype.hasOwnProperty.call(LABELS, key) && !labels.includes(LABELS[key])) labels.push(LABELS[key]);
  }
  return labels.length ? `请检查${labels.join('、')}的填写内容` : error && error.message || '客户信息更新失败';
}

module.exports = {payload, errorMessage};
