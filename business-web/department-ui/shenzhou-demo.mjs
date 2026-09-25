// 前端演示字典，与小程序 Demo 一致；不是正式 API 字段或业务目录。
export const FOLLOW_UP_TYPES = ['客户拜访', '渠道拜访', '厂商拜访', '商机推进', '领导反馈'];
export const BUSINESS_RECORDS = [
  {id:'demo-001',code:'DEMO-001',name:'示例业务 · 数据平台建设项目',description:'平台建设与应用集成'},
  {id:'demo-002',code:'DEMO-002',name:'示例业务 · 网络设备采购项目',description:'园区网络设备采购'},
  {id:'demo-003',code:'DEMO-003',name:'示例业务 · 年度服务续约',description:'年度运维与服务续约'},
  {id:'demo-004',code:'DEMO-004',name:'示例业务 · 云资源扩容项目',description:'云资源扩容与迁移'},
  {id:'demo-005',code:'DEMO-005',name:'示例业务 · 多区域协同与业务系统一体化建设项目（长名称演示）',description:'长名称与换行展示'},
];
export const validIds = ids => Array.isArray(ids) ? [...new Set(ids)].filter(id => BUSINESS_RECORDS.some(r => r.id === id)) : [];
export function normalizeDemoDraft(raw) {
  const value = raw && raw.version === 1 ? raw : {};
  return {version:1,followUpType:FOLLOW_UP_TYPES.includes(value.followUpType) ? value.followUpType : '',relatedBusinessIds:validIds(value.relatedBusinessIds)};
}
export function demoDraftKey(page, data) {
  return 'shenzhou:web-demo:followup:v1:' + JSON.stringify([page.userKey || 'preview', data.customerId || '', (data.editing ? data.visitId : page.entryDraftId) || 'new']);
}
export function searchBusiness(query) {
  const needle = String(query || '').trim().toLowerCase();
  return BUSINESS_RECORDS.filter(r => `${r.code} ${r.name} ${r.description}`.toLowerCase().includes(needle));
}
export function validateDemoDraft(draft) {
  return {followUpType:FOLLOW_UP_TYPES.includes(draft.followUpType) ? '' : '请选择跟进类型',relatedBusinessIds:validIds(draft.relatedBusinessIds).length ? '' : '请至少关联一条业务数据'};
}
export function amountCny(value, currency) {
  if (currency && currency !== 'CNY') return '币种待确认';
  if (typeof value !== 'string' && typeof value !== 'number') return '未填写';
  const raw = String(value).trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(raw)) return '未填写';
  const [whole, fraction=''] = raw.split('.');
  return whole.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + fraction.padEnd(2,'0');
}
export function opportunityAppearance(raw = {}) {
  const text = v => typeof v === 'string' && v.trim() ? v.trim() : '未填写';
  // 未确认的神州字段保持未填写，不从伙伴、客户或导入元数据推断。
  return [
    ['商机名称',text(raw.name)],['商机金额（元/人民币）',amountCny(raw.amount,raw.currency)],
    ['代理商名称','未填写'],['最终用户名称','未填写'],['预计签约日期',text(raw.expected_close_date)],
    ['商机来源','未填写'],['项目背景','未填写'],['是否框架商机','未填写'],
  ];
}
