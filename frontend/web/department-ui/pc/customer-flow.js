import {api, identity} from './data.js';
import {normalizePageSize} from './list-pagination.js';

export const REQUEST_STATUSES = {pending: '待审批', approved: '已通过', rejected: '已驳回', cancelled: '已取消'};
export const initialHistoryFilters = () => ({scope: {kind: 'self'}, kind: '', status: '', q: '', offset: 0});
export function historyParams(filters) {
  const params = {scope: filters.scope?.kind === 'self' ? 'self' : 'team', limit: normalizePageSize(filters.pageSize), offset: Math.max(0, Number(filters.offset) || 0)};
  if (filters.scope?.kind === 'member') params.member_id = filters.scope.memberId;
  if (filters.scope?.kind === 'team' && filters.scope.teamId && filters.scope.teamId !== 'all') params.team_id = filters.scope.teamId;
  for (const key of ['kind', 'status', 'q']) if (filters[key]?.trim()) params[key] = filters[key].trim();
  return params;
}

const isCount = value => Number.isInteger(value) && value >= 0;
export function normalizeHistory(value) {
  if (!value || !Array.isArray(value.items) || !isCount(value.total) || !value.summary ||
      Object.keys(REQUEST_STATUSES).some(key => !isCount(value.summary[key])) ||
      value.items.length > value.total || value.items.some(row => !row.id || !['claim', 'creation'].includes(row.kind) ||
        !REQUEST_STATUSES[row.status] || typeof row.can_read_customer !== 'boolean')) {
    throw Error('申请历史响应不完整，请更新配套服务后重试');
  }
  return value;
}
export async function loadRequestHistory(filters) {
  const owner = identity();
  const value = await api().request({path: `/customers/requests/history?${new URLSearchParams(historyParams(filters))}`, fresh: true});
  if (owner !== identity()) throw Error('身份或权限已变化，请重新读取申请记录');
  return normalizeHistory(value);
}

export function normalizeExistingCustomers(value) {
  if (!value || !Array.isArray(value.items) || !isCount(value.total) || value.items.length > value.total ||
      value.items.some(row => !row.id || !row.name)) throw Error('存量客户检索结果不完整，请重试');
  return value;
}
export const existingCustomerQuery = name => [...String(name || '').trim()].slice(0, 100).join('');
export async function loadExistingCustomers(name) {
  const owner = identity();
  const params = new URLSearchParams({q: existingCustomerQuery(name), page_size: 50, offset: 0});
  const value = await api().request({path: `/customers/claim-pool?${params}`, fresh: true});
  if (owner !== identity()) throw Error('身份或权限已变化，请重新检索客户');
  return normalizeExistingCustomers(value);
}
// This is only a UI hint. Submission and approval enforce the authoritative duplicate rule.
export const exactCustomerName = (a, b) => String(a || '').trim().toLocaleLowerCase() === String(b || '').trim().toLocaleLowerCase();
export function creationSearchBlock({enabled, name, loading, error, value}) {
  if (!enabled) return '';
  if (!name.trim()) return '先填写客户全称并检索存量客户';
  if (loading) return '正在检索存量客户';
  if (error || !value) return '存量客户检索失败，请重试';
  if (value.items.some(row => exactCustomerName(row.name, name))) return '已有同名客户，请使用已有客户';
  return '';
}
