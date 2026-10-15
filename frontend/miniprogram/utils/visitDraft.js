const {draftScope} = require('./draftScope');
// Browser storage is origin-bound; native storage additionally needs the API URL.
function scope(session, api) {
  const base = api && typeof api.getBaseUrl === 'function' ? String(api.getBaseUrl()).replace(/\/+$/, '') : '';
  return `${draftScope(session)}${base ? `:service:${encodeURIComponent(base)}` : ''}`;
}
// Keep route-bound actions separate. Never guess which service owns a legacy draft.
function context(options = {}) {
  if (typeof options.draftContext === 'string' && /^(customer|opportunity):[\w%~.-]+(?:\/opportunity:[\w%~.-]+)?$/.test(options.draftContext)) return options.draftContext;
  const customer = options.customerId || options.customer_id;
  const opportunity = options.opportunityId || options.opportunity_id;
  return customer ? `customer:${encodeURIComponent(customer)}${opportunity ? `/opportunity:${encodeURIComponent(opportunity)}` : ''}`
    : opportunity ? `opportunity:${encodeURIComponent(opportunity)}` : '';
}
function key(prefix, scope, action = '') { return `${prefix}:${scope}${action ? `:${action}` : ''}`; }
// Lifecycle flushes can update the save timestamp without changing any draft content.
const signature = value => {
  if (!value) return 'null';
  const {savedAt, ...content} = value;
  return JSON.stringify(content);
};
function open(storage, storageKey, initial) {
  const value = arguments.length > 2 ? initial : storage.getStorageSync(storageKey);
  let expected = signature(value), ended = false;
  const current = () => !ended && signature(storage.getStorageSync(storageKey)) === expected;
  const check = () => { if (!current()) throw Object.assign(Error('草稿已在其他页面更新或结束，当前内容未覆盖，请重新打开核对'), {code:'DRAFT_CHANGED'}); };
  return {value, current, hasValue:()=>expected !== signature(null),
    write(next) { check(); storage.setStorageSync(storageKey, next); expected = signature(next); },
    remove() { check(); storage.removeStorageSync(storageKey); ended = true; },
  };
}
function removeAction(storage, keys, draftId) {
  keys.forEach(storageKey => {
    const value = storage.getStorageSync(storageKey);
    if (value && (draftId ? value.draftId === draftId : !value.draftId)) storage.removeStorageSync(storageKey);
  });
}
function legacyText(storage, session) {
  const actor=draftScope(session);
  const read=storage.readLegacyVisitDraftSync || storage.getStorageSync;
  const entry=read.call(storage,key('visitEntryV2',actor)) || {};
  const confirm=read.call(storage,key('visitConfirmV2',actor)) || {};
  const labels={follow_up_record:'沟通记录',next_action:'下一步计划',customer_main_business:'客户主营业务',customer_needs:'客户需求',customer_budget:'客户预算',contact_role:'联系人角色',
    contact_name:'对接人',contact_title:'联系人职位',partner_name:'伙伴名称',interaction_at:'跟进日期',created_date:'创建日期',risk_description:'风险描述',competitor_info:'竞争情况'};
  const parts=[];
  if (typeof entry.transcript==='string' && entry.transcript.trim()) parts.push(entry.transcript);
  // Preserve edited confirmation text, but never reuse unidentified-service API references.
  if (!entry.draftId || !confirm.draftId || entry.draftId===confirm.draftId) {
    for (const [field,label] of Object.entries(labels)) {
      const value=(confirm.values || {})[field];
      if (typeof value==='string' && value.trim()) parts.push(`${label}：${value}`);
    }
  }
  return parts.join('\n\n');
}
module.exports = {scope, context, key, open, removeAction, legacyText};
