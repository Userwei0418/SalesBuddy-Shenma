import {api, identity, normalizeVisit} from './data.js';

// These are facets of the authorized result, not an authorization directory.
export function visitFacets(visits, customerId = '') {
  const customers = new Map(), opportunities = new Map();
  for (const visit of visits) {
    for(const id of (visit.customerIds || [visit.customerId]).filter(Boolean)) customers.set(id,visit.customerNames?.[id] || visit.customerName);
    if (customerId && !(visit.customerIds || [visit.customerId]).includes(customerId)) continue;
    for (const id of visit.opportunityIds) opportunities.set(id,
      visit.opportunityNames?.[id] || (visit.opportunityIds.length === 1 ? visit.opportunityName : '') || '关联商机（名称未记录）');
  }
  const options = values => [...values].map(([value, label]) => ({value, label})).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'));
  return {customers: options(customers), opportunities: options(opportunities)};
}

export function filterRecordRows(visits, {source = 'all', customer = '', opportunity = ''}) {
  return visits.filter(visit => (source === 'all' || visit.source === source)
    && (!customer || (visit.customerIds || [visit.customerId]).includes(customer))
    && (!opportunity || (opportunity === 'none' ? !visit.opportunityIds.length : visit.opportunityIds.includes(opportunity))));
}

export function facetValue(value, options, noun) {
  return value ? {value, label: options.find(option => option.value === value)?.label || `所选${noun}在当前结果中无记录`} : undefined;
}

export async function loadFollowupDetail(visit, signal) {
  if (!visit) return null;
  const owner = identity();
  const raw = await api().getVisit(visit.id);
  if (signal?.aborted || owner !== identity()) throw Object.assign(Error('读取已取消'), {name: 'AbortError'});
  if (!raw || String(raw.id) !== visit.id || raw.is_summary === true) throw Error('完整跟进内容未能读取，请重试');
  const full = normalizeVisit(raw);
  // GET detail supplies body fields; keep the range projection's explicit link grants.
  return {...visit, ...full, source: full.source === 'unknown' ? visit.source : full.source,
    canOpenCustomer: raw.can_open_customer == null ? visit.canOpenCustomer : full.canOpenCustomer,
    canOpenOpportunity: raw.can_open_opportunity == null ? visit.canOpenOpportunity : full.canOpenOpportunity,
    opportunityAccess: {...visit.opportunityAccess, ...full.opportunityAccess},
    opportunityCustomers: {...visit.opportunityCustomers, ...full.opportunityCustomers},
    customerIds: [...new Set([...(visit.customerIds || []),...(full.customerIds || [])])],
    customerNames: {...visit.customerNames,...full.customerNames},
    customerAccess: raw.can_open_customer == null ? visit.customerAccess : {...visit.customerAccess,...full.customerAccess},
    isSummary: false};
}
