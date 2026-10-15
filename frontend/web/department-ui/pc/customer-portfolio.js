import {api, identity, periodRange, todayKey} from './data.js';
import {statisticsRange} from './period.js';

export const CUSTOMER_PERIODS = [
  {value: 'year', label: '本年'}, {value: 'day', label: '今日'}, {value: 'week', label: '本周'},
  {value: 'biweek', label: '双周'}, {value: 'month', label: '本月'}, {value: 'quarter', label: '本季度'},
];
export const CUSTOMER_SEGMENTS = [
  {value: 'claimed', label: '全部已认领'}, {value: 'active', label: '活跃客户', requires: 'activity'},
  ...[10, 30, 50, 70, 90].map(n => ({value: `pipeline:${n}`, label: n === 10 ? '有商机 · ≥10%' : `在推商机 · ≥${n}%`, requires: 'opportunity'})),
  {value: 'won', label: '已成交客户', requires: 'opportunity'},
];
export const CUSTOMER_QUADRANTS = [
  {value: '', label: '全部象限'}, {value: 'attack', label: '主攻区'}, {value: 'asset', label: '客户资产'},
  {value: 'spot', label: '见单打单'}, {value: 'resource', label: '客户资源'},
];
export const SUMMARY_FIELDS = ['claimed', 'active', ...[10, 30, 50, 70, 90].map(n => `pipeline_ge_${n}`), 'won', 'pool_total', 'activity_unavailable_count', 'opportunity_unavailable_count'];

export function customerRange(period = 'year', now = Date.now()) {
  if (period !== 'year') return periodRange(period, now);
  const today = todayKey(now), year = Number(today.slice(0, 4));
  return {kind: 'year', name: '本年', start: `${year}-01-01`, end: `${year}-12-31`, today,
    year, title: `${year} 年`, elapsed: Math.floor((Date.parse(today) - Date.parse(`${year}-01-01`)) / 86400000) + 1};
}

export function initialCustomerFilters(scope) {
  return {scope, period: 'year', selection: 'claimed', businessIdentity: 'all', q: '', quadrant: '', level: '', amount: '', plan: '', unscored: false, offset: 0};
}

export function portfolioParams(filters, {limit = 20, now = Date.now()} = {}) {
  const range = statisticsRange(customerRange(filters.period, now));
  const selection = CUSTOMER_SEGMENTS.some(o => o.value === filters.selection) ? filters.selection : 'claimed';
  const [segment, probability] = selection.split(':');
  const params = {scope: filters.scope?.kind === 'self' ? 'self' : 'team', segment,
    activity_start: range.start, activity_end: range.end, offset: Math.max(0, Number(filters.offset) || 0), limit};
  if (filters.scope?.kind === 'member') params.member_id = filters.scope.memberId;
  if (filters.scope?.kind === 'team' && filters.scope.teamId && filters.scope.teamId !== 'all') params.team_id = filters.scope.teamId;
  if (segment === 'pipeline') params.min_probability = Number(probability);
  if (filters.businessIdentity === 'partner') params.business_identity = 'partner';
  if (filters.q?.trim()) params.q = filters.q.trim();
  if (filters.quadrant) params.quadrant = filters.quadrant;
  if (filters.level) params.level = filters.level;
  if (filters.plan) params.plan = filters.plan;
  if (filters.amount) {
    const [min, max] = filters.amount.split(':');
    if (min) params.amount_min = Number(min);
    if (max) params.amount_max = Number(max);
  }
  if (filters.unscored) params.unscored = true;
  return params;
}

const isCount = value => Number.isInteger(value) && value >= 0;
export function normalizePortfolio(response) {
  if (!response || !Array.isArray(response.items) || !Array.isArray(response.map_points) ||
      !isCount(response.total) || !isCount(response.offset) || !isCount(response.limit) || response.limit < 1 || !isCount(response.map_unavailable_count) ||
      !response.summary || SUMMARY_FIELDS.some(key => response.summary[key] !== null && !isCount(response.summary[key])) ||
      !response.availability || ['activity', 'opportunity', 'map', 'pool'].some(key => typeof response.availability[key] !== 'boolean')) {
    throw Error('客户范围统计响应不完整，请更新配套本地服务后重试');
  }
  if (response.items.length > response.limit || new Set(response.items.map(row => row.id)).size !== response.items.length ||
      response.items.some(row => !row.id || !row.name || ['can_open', 'activity_readable', 'opportunity_readable', 'map_readable'].some(key => typeof row[key] !== 'boolean')) || response.items.length > response.total) {
    throw Error('客户分页数据不完整，请刷新后重试');
  }
  if (response.availability.map && (!isCount(response.unscored_count) ||
      response.map_points.length + response.unscored_count + response.map_unavailable_count !== response.total ||
      new Set(response.map_points.map(row => row.id)).size !== response.map_points.length)) {
    throw Error('客户地图与列表范围不一致，请刷新后重试');
  }
  return response;
}

export async function loadCustomerPortfolio(params) {
  const owner = identity();
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value !== undefined && value !== null));
  const response = await api().request({path: `/customers/portfolio?${query}`, fresh: true});
  if (owner !== identity()) throw Error('身份或权限已变化，请重新读取客户');
  return normalizePortfolio(response);
}

export function selectionLabel(selection, range) {
  if (selection === 'active') return `${range.name}活跃客户`;
  return CUSTOMER_SEGMENTS.find(option => option.value === selection)?.label || '全部已认领';
}

// Backend owns scope and classification. Never recalculate totals from a page of rows.
export function selectionCount(summary, selection) {
  return summary?.[selection.startsWith('pipeline:') ? `pipeline_ge_${selection.split(':')[1]}` : selection] ?? null;
}

export function mapPoints(rows) {
  const score = value => value !== null && value !== '' && Number.isFinite(Number(value)) ? Math.max(0, Math.min(10, Number(value) / 10)) : null;
  // Bubble sizes use the complete map result, never the current table page.
  const amounts = rows.map(row => Number(row.acv_amount)).filter(value => value > 0).sort((a, b) => b - a);
  const large = amounts[Math.floor(amounts.length / 3)] ?? Infinity;
  const medium = amounts[Math.floor(amounts.length * 2 / 3)] ?? Infinity;
  const band = row => Number(row.acv_amount) > 0 ? Number(row.acv_amount) >= large ? 'large' : Number(row.acv_amount) >= medium ? 'medium' : 'small' : 'small';
  return rows.map(row => ({id: row.id, name: row.name, potential: score(row.potential_score), relationship: score(row.relationship_score),
    tone: 'pending', amountBand: band(row), canOpen: row.can_open === true,
    summary: [row.team_name, row.owner_name, row.max_open_probability != null ? `最高在推 ${row.max_open_probability}%` : row.open_count === null ? '商机未授权' : '暂无在推商机'].filter(Boolean).join(' · ')}));
}
