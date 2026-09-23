import {state, api, dialog, field, options, details, date, toast, esc} from './core.js';

const roleNames = {user:'使用者', influencer:'影响者', decision_maker:'决策者'};
const lifecycleNames = {lead:'线索', prospect:'潜在客户', active:'活跃', dormant:'休眠', won:'已成单', lost:'已流失', archived:'已归档'};
const values = row => ({
  name:row.name, industry:row.industry_code, customer_type:row.customer_type_code,
  level_code:row.level_code, source:row.source_code, partner_name:row.primary_partner_name,
  operation_type:row.operation_type, cooperation_years:row.cooperation_years,
  main_business:row.main_business, customer_budget:row.customer_budget,
  demand_summary:row.demand_summary, next_action:row.next_action,
  contact_name:row.contact_name, contact_title:row.contact_title,
  contact_role:roleNames[row.contact_role] || row.contact_role,
  contact_phone:row.contact_phone, contact_email:row.contact_email,
});
const heading = title => `<div class="section-title">${esc(title)}</div>`;
const filled = entries => details(entries.map(([label,value]) => [label,value === '' ? null : value]));
const text = (label,value) => `<div class="customer-profile-text"><h4>${esc(label)}</h4><p>${esc(value || '未填写')}</p></div>`;

export function customerProfileDetails(row) {
  return `<div class="customer-profile">${heading('基础资料')}${filled([
    ['客户名称',row.name],['所属行业',row.industry_code],['客户类型',row.customer_type_code],
    ['客户优先级',row.level_code],['客户来源',row.source_code],['所属伙伴',row.primary_partner_name],
  ])}${heading('经营信息')}${filled([
    ['经营分类',row.operation_type],['合作年限',row.cooperation_years == null ? null : `${row.cooperation_years} 年`],
  ])}${text('主营业务',row.main_business)}${text('客户预算',row.customer_budget)}
  ${text('需求摘要',row.demand_summary)}${text('下一步行动',row.next_action)}
  ${heading('首要联系人')}${filled([
    ['姓名',row.contact_name],['职位',row.contact_title],['联系人角色',roleNames[row.contact_role] || row.contact_role],
    ['联系电话',row.contact_phone],['邮箱',row.contact_email],
  ])}${heading('建档信息 · 只读')}${filled([
    ['客户编号',row.customer_code],['CRM 外部 ID',row.external_customer_id],['公司建档编号',row.company_reference],
    ['管理部门',row.team_name],['客户状态',lifecycleNames[row.lifecycle_status] || row.lifecycle_status],
    ['数据来源',({manual:'手工建档',import:'导入',crm:'CRM 同步'})[row.data_source] || row.data_source],
    ['建档人',row.creator_name],['创建时间',date(row.created_at)],['最近更新',date(row.updated_at)],
  ])}</div>`;
}

export function customerProfileForm(row) {
  const v = values(row);
  const choose = (label,key,choices) => {
    const selected = v[key] || '';
    return field(label,key,'',{select:options([...new Set([...choices,...(selected ? [selected] : [])])],selected,'未填写')});
  };
  return '<div class="notice">维护客户主档与首要联系人。关联编号保持只读；认领和交接请使用客户归属操作。</div>' +
    `<div class="form-grid">${heading('基础资料')}${field('客户名称','name',v.name,{required:true,full:true})}
    ${field('所属行业','industry',v.industry)}${choose('客户类型','customer_type',['潜在客户','商机客户','已成单客户'])}
    ${choose('客户优先级','level_code',['Tier-1','Tier-2','Tier-3'])}
    ${choose('客户来源','source',['销售自拓','客户转介绍','市场活动','销售线索','合作伙伴','其他'])}
    ${field('所属伙伴','partner_name',v.partner_name)}${heading('经营信息')}
    ${field('经营分类','operation_type',v.operation_type)}
    ${field('合作年限（年）','cooperation_years',v.cooperation_years ?? '',{type:'number',min:0,max:9999999.9,step:0.1,help:'留空表示未填写；0 表示尚未满一年。'})}
    ${field('主营业务','main_business',v.main_business,{type:'textarea',full:true})}
    ${field('客户预算','customer_budget',v.customer_budget,{type:'textarea',full:true,help:'保留预算金额、币种及确认情况等原始口径。'})}
    ${field('需求摘要','demand_summary',v.demand_summary,{type:'textarea',full:true})}
    ${field('下一步行动','next_action',v.next_action,{type:'textarea',full:true})}
    ${heading('首要联系人')}${field('姓名','contact_name',v.contact_name,{required:!!v.contact_name})}
    ${field('职位','contact_title',v.contact_title)}${choose('联系人角色','contact_role',['使用者','影响者','决策者'])}
    ${field('联系电话','contact_phone',v.contact_phone)}${field('邮箱','contact_email',v.contact_email,{type:'email'})}
    ${heading('关联编号 · 只读')}${field('客户编号','',row.customer_code,{disabled:true})}
    ${field('CRM 外部 ID','',row.external_customer_id,{disabled:true})}${field('公司建档编号','',row.company_reference,{disabled:true})}</div>`;
}

export function customerProfileChanges(formData,row) {
  const previous = values(row), result = {version_no:row.version_no};
  for (const [key,value] of formData) {
    if (!(key in previous)) continue;
    const trimmed = String(value).trim();
    const normalized = key === 'cooperation_years' ? (trimmed === '' ? null : Number(trimmed)) : trimmed;
    const before = key === 'cooperation_years' ? (previous[key] == null ? null : Number(previous[key])) : (previous[key] ?? '');
    if (normalized !== before) result[key] = normalized;
  }
  return result;
}

let editGeneration = 0;
export async function editCustomerProfile(id) {
  const serial = ++editGeneration, actor = JSON.stringify(state.actor), page = state.page;
  const pendingDialog = document.querySelector('#dialog'), pendingContent = pendingDialog.innerHTML;
  const pendingForm = pendingDialog.querySelector?.('#dialog-form'), wasOpen = pendingDialog.open;
  const row = await api(`/customers/${encodeURIComponent(id)}/overview`);
  if (serial !== editGeneration || actor !== JSON.stringify(state.actor) || page !== state.page ||
      document.querySelector('#dialog') !== pendingDialog || (wasOpen && !pendingDialog.open) ||
      (pendingForm ? pendingDialog.querySelector('#dialog-form') !== pendingForm : pendingDialog.innerHTML !== pendingContent)) return;
  dialog(`编辑客户档案 · ${row.name}`,customerProfileForm(row),{
    wide:true, submit:'保存资料',
    onSubmit:async (formData,key) => {
      const body = customerProfileChanges(formData,row);
      if (Object.keys(body).length === 1) {toast('资料未发生变化');return;}
      await api(`/customers/${encodeURIComponent(id)}`,{method:'PATCH',body,key});
      toast('客户档案已保存，修改明细已记录');
    },
  });
}
