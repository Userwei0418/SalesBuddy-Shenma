import {api, identity} from './data.js';
import {normalizePageSize} from './list-pagination.js';

export const CREATION_PAGE_SIZE = 20;
export const initialCreationFilters = () => ({scope: {kind: 'self'}, q: '', created_from: '', created_to: '', offset: 0});
const count = value => Number.isSafeInteger(value) && value >= 0;
export function validCreationOffset(offset, total, pageSize = CREATION_PAGE_SIZE) {
  if (!count(total) || !count(offset)) return offset;
  return Math.min(offset, Math.floor((Math.max(total, 1) - 1) / normalizePageSize(pageSize)) * normalizePageSize(pageSize));
}
const text = value => typeof value === 'string' && Boolean(value.trim());
const nullableText = value => value === null || text(value);
const dateOnly = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const timestamp = value => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));

export function creationHistoryParams(filters) {
  const scope = filters.scope || {kind: 'self'};
  if (!['self', 'team', 'member'].includes(scope.kind)) throw Error('请选择有效的创建人范围');
  const params = {scope: scope.kind === 'self' ? 'self' : 'team', limit: normalizePageSize(filters.pageSize), offset: count(filters.offset) ? filters.offset : 0};
  if (scope.kind === 'member') {
    if (!text(scope.memberId)) throw Error('请选择创建人');
    params.member_id = scope.memberId;
  }
  if (scope.kind === 'team' && scope.teamId && scope.teamId !== 'all') params.team_id = scope.teamId;
  if (filters.q?.trim()) params.q = filters.q.trim();
  for (const key of ['created_from', 'created_to']) if (filters[key]) {
    if (!dateOnly(filters[key])) throw Error('请选择有效的创建日期');
    params[key] = filters[key];
  }
  if (params.created_from && params.created_to && params.created_from > params.created_to) throw Error('创建开始日期不能晚于结束日期');
  return params;
}

function validPage(value, items, offset, limit) {
  return Array.isArray(items) && count(value.total) && items.length <= value.total && items.length <= limit && typeof value.has_more === 'boolean' &&
    (value.has_more ? items.length > 0 && count(value.next_offset) && value.next_offset === offset + items.length && value.next_offset < value.total : value.next_offset === null && offset + items.length >= value.total);
}
export function normalizeCreationHistory(value, offset = 0, pageSize = CREATION_PAGE_SIZE) {
  if (!value || !validPage(value, value.items, offset, normalizePageSize(pageSize)) || new Set(value.items.map(row => row.id)).size !== value.items.length || value.items.some(row =>
    !text(row.id) || !text(row.customer_id) || !text(row.name) || !nullableText(row.customer_name) || !nullableText(row.creator_id) || !nullableText(row.creator_name) ||
    (row.creator_id === null && row.creator_name !== null) || !nullableText(row.owner_name) || typeof row.can_read_customer !== 'boolean' ||
    !(row.business_created_on === null || dateOnly(row.business_created_on)) || !(row.created_at === null || timestamp(row.created_at)))) {
    throw Error('商机创建历史响应不完整，请更新配套服务后重试');
  }
  return value;
}

async function read(path, owner) {
  const value = await api().request({path, fresh: true});
  if (owner !== identity()) throw Error('身份或权限已变化，请重新读取创建历史');
  return value;
}
export async function loadCreationHistory(filters) {
  const owner = identity(), params = creationHistoryParams(filters);
  return normalizeCreationHistory(await read(`/opportunities/creation-history?${new URLSearchParams(params)}`, owner), params.offset, params.limit);
}

// Only the small creator directory is collected; business rows remain server-paginated.
// Follow the server cursor so creators after the first 100 are still selectable.
export async function loadCreationDirectory(signal) {
  const owner = identity(), members = [], ids = new Set();
  let offset = 0, teams = null;
  for (;;) {
    if (signal?.aborted) throw Object.assign(Error('读取已取消'), {name: 'AbortError'});
    const value = await read(`/opportunities/creation-history/options?${new URLSearchParams({limit: 100, offset})}`, owner);
    if (!value || value.default_scope !== 'self' || value.team_basis !== 'current_creator_membership' || !validPage(value, value.members, offset, 100) ||
        !Array.isArray(value.teams) || value.teams.some(team => !text(team.id) || !text(team.name)) ||
        value.members.some(member => !text(member.id) || !text(member.name) || typeof member.is_active !== 'boolean' || !Array.isArray(member.team_ids) || member.team_ids.some(id => !text(id)))) {
      throw Error('创建人目录响应不完整，请更新配套服务后重试');
    }
    teams ||= value.teams;
    for (const member of value.members) {
      if (ids.has(member.id)) throw Error('创建人目录已变化，请刷新后重试');
      ids.add(member.id); members.push(member);
    }
    if (!value.has_more) return {teams, members, people: new Map(members.map(member => [member.id, member]))};
    offset = value.next_offset;
  }
}

export function creationTimeLabel(row) {
  // Never use a timestamp to fill an unknown business date, and never invent midnight.
  if (!row.business_created_on) return '日期未记录';
  if (!row.created_at) return row.business_created_on;
  const value = new Intl.DateTimeFormat('sv-SE', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false}).format(new Date(row.created_at));
  return value.startsWith(row.business_created_on) ? value : row.business_created_on;
}

export function creationScopeLabel(scope, directory) {
  if (scope.kind === 'self') return '本人创建';
  if (scope.kind === 'member') return directory?.people.get(scope.memberId)?.name || (directory ? '所选创建人已不可用，请重新选择' : '所选创建人');
  if (!scope.teamId || scope.teamId === 'all') return '全部可见创建记录';
  return directory?.teams.find(team => team.id === scope.teamId)?.name || (directory ? '所选部门已不可用，请重新选择' : '所选部门');
}

export function creationScopeOption(scope, directory) {
  const value = scope.kind === 'self' ? 'self' : scope.kind === 'member' ? `member:${scope.memberId}` : `team:${scope.teamId || 'all'}`;
  const inactive = scope.kind === 'member' && directory?.people.get(scope.memberId)?.is_active === false;
  return {value, label: creationScopeLabel(scope, directory) + (inactive ? '（已停用）' : '')};
}
