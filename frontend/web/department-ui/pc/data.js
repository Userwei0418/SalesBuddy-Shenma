// PC 专属页面（工作台、跟进记录、工作报告、知识库）的只读取数层。
// 所有请求经 utils/apiClient，沿用登录态、刷新、幂等与演示数据适配。
// 跨客户的跟进记录优先使用 Web 范围读取接口；旧后端没有该接口时再按客户回退，保证历史详情契约不变。
// 销售与 FDE 统一走 visit.read 范围；仅旧后端缺少范围接口时使用兼容读取。
import {dayKey, periodRange, statisticsRange, todayKey} from './period.js';

export {dayKey, periodRange, todayKey};
export const runtime = () => globalThis.SalesRuntime;
export const api = () => runtime().require('utils/apiClient');
export const accessModule = () => runtime().require('utils/access');
export const session = () => runtime()?.app?.globalData?.session || null;
export const isPreview = () => globalThis.SALES_MODE === 'preview';
export const isFdeRole = role => role === 'fde' || role === 'fde_lead';
export const identity = (s = session()) => {
  if (!s) return '';
  const access = accessModule();
  return typeof access.identity === 'function' ? access.identity(s) : [s.workspaceId, s.userId, s.permissionVersion, s.loginAt].join(':');
};
export const can = key => Boolean(runtime()?.app?.can?.(key));
// The server separately authorizes whole-page file/clipboard exports.
export const clientExportAllowed = key => can(key) && session()?.clientExportPermissions?.[key] === true;

// 范围来自服务端对当前 action 的有效 grant；角色和账号总 data_scope 不作授权依据。
export function readScopeFor(permission = 'visit.read', s = session()) {
  const access = accessModule();
  if (typeof access.scopeFor !== 'function') throw Error('共享权限模块尚未就绪，请刷新后重试');
  return access.scopeFor(s, permission);
}

export const INTERACTION_MODE = {offline_meeting: '线下会议', online_meeting: '线上会议', phone_voice: '电话/语音', social_meal: '饭局/聚会'};
export const EXPECTATION = {exceeded: '超出预期', met: '达成预期', met_50_100: '达成 50%–100%', met_30_50: '达成 30%–50%', met_10_30: '达成 10%–30%', not_met: '未达成'};
export const VISIT_STATUS = {archived: '已归档', confirmed: '已确认', draft: '草稿', pending: '待确认'};
export const SOURCE_LABEL = {sales: '销售跟进', fde: '售前 / FDE', projectbuddy: 'ProjectBuddy 同步', unknown: '来源未记录'};

const text = value => value == null ? '' : String(value).trim();

// 来源：ProjectBuddy 同步的售前记录需要后端带上 source_system（或 source）= projectbuddy，字段待后端确认；
// 其余按记录人身份区分销售跟进与售前 / FDE 跟进。
export function visitSource(raw, people = new Map()) {
  const system = String(raw.source_system || raw.source || raw.origin || raw.import_meta?.source_system || '').toLowerCase();
  if (system.includes('projectbuddy')) return 'projectbuddy';
  if (Object.hasOwn(raw, 'recording_role_code_snapshot')) return raw.recording_role_code_snapshot ? (isFdeRole(raw.recording_role_code_snapshot) ? 'fde' : 'sales') : 'unknown';
  const recorderId = raw.recorder_id || raw.recorder_user_id;
  const recorder = people.get(String(recorderId || ''));
  if (raw.recorder_role || raw.recording_role_code_snapshot ? isFdeRole(raw.recorder_role || raw.recording_role_code_snapshot) : recorder ? isFdeRole(recorder.role) : (raw.fde_participant_ids || (raw.fde_participants || []).map(p => p.id)).map(String).includes(String(recorderId || ''))) return 'fde';
  return raw.recorder_role || raw.recording_role_code_snapshot || recorder ? 'sales' : 'unknown';
}

export function normalizeVisit(raw, people = new Map()) {
  const opportunityIds = [...new Set([raw.opportunity_id, ...(raw.opportunity_ids || []), ...((raw.linked_opportunities || []).map(o => o.id))].filter(Boolean).map(String))];
  // A range projection explicitly returns null for an unknown historical date.
  const date = dayKey(raw.visit_date || raw.interaction_at) || (Object.hasOwn(raw, 'visit_date') ? '' : dayKey(raw.created_date || raw.created_at));
  const recorderId = raw.recorder_id || raw.recorder_user_id;
  const recorder = people.get(String(recorderId || ''));
  return {
    id: String(raw.id),
    customerId: raw.customer_id ? String(raw.customer_id) : '',
    customerName: text(raw.customer_name) || text(raw.partner_name_snapshot) || '未关联客户',
    opportunityId: opportunityIds[0] || '',
    opportunityIds,
    opportunityNames: Object.fromEntries((raw.linked_opportunities || []).filter(o => o.id && o.name).map(o => [String(o.id), o.name])),
    opportunityAccess: Object.fromEntries((raw.linked_opportunities || []).filter(o => o.id).map(o => [String(o.id), o.can_open !== false])),
    canOpenCustomer: raw.can_open_customer !== false,
    canOpenOpportunity: raw.can_open_opportunity !== false,
    isSummary: raw.is_summary === true,
    opportunityName: text(raw.opportunity_name) || (raw.linked_opportunities || []).map(o => o.name).filter(Boolean).join('、'),
    recorderId: recorderId ? String(recorderId) : '',
    recorderName: text(raw.original_recorder_name) || text(raw.recorder_name) || text(raw.creator_name) || recorder?.name || '未记录',
    teamId: text(raw.recorder_team_id || raw.recording_team_id || raw.team_id),
    date,
    at: raw.interaction_at || raw.visit_date || '',
    createdAt: raw.created_at || raw.created_date || '',
    content: text(raw.follow_up_record),
    nextAction: text(raw.next_action),
    goal: text(raw.visit_goal),
    mode: INTERACTION_MODE[raw.interaction_mode_code] || text(raw.interaction_mode) || '',
    expectation: EXPECTATION[raw.expectation_code] || '',
    contacts: text(raw.contact_name_snapshot || raw.contact_name),
    fdeNames: (raw.fde_participants || []).map(p => p.name || p.display_name).filter(Boolean),
    collaborators: (raw.collaborators || []).map(p => p.name || p.display_name).filter(Boolean),
    status: raw.status || '',
    statusLabel: VISIT_STATUS[raw.status] || '',
    isFirst: raw.is_first_visit === true,
    duration: Number(raw.duration_minutes) || null,
    source: visitSource(raw, people),
    canOpen: raw.can_read_detail !== false,
  };
}

const byNewest = (a, b) => String(b.date).localeCompare(String(a.date)) || String(b.createdAt).localeCompare(String(a.createdAt)) || String(b.id).localeCompare(String(a.id));

// 有并发上限的顺序执行：逐个客户读取跟进时，不一次打满后端。
export async function pool(items, size, worker, signal) {
  let index = 0;
  const runners = Array.from({length: Math.min(size, items.length)}, async () => {
    while (index < items.length) { stopIfAborted(signal); const item = items[index++]; await worker(item); }
  });
  await Promise.all(runners);
}
const stopIfAborted = signal => { if (signal?.aborted) throw Object.assign(new Error('读取已取消'), {name: 'AbortError'}); };

// ---- 人员与团队目录 ----
export async function loadDirectory(permission = 'visit.read') {
  const s = session();
  const owner = identity(s);
  const picker = runtime().require('utils/personPicker');
  const response = await api().getDirectoryMembers({permission, include_stats: false});
  if (owner !== identity()) throw Error('身份或权限已变化，请刷新后重试');
  if (!Array.isArray(response?.items) || !Array.isArray(response?.teams)) throw Error('人员目录响应不完整，请重试');
  const teams = picker.normalizeTeams(response.teams);
  const members = picker.normalizeMembers(response.items || [], teams).filter(row => row.active !== false);
  const defaultTeamId = teams.some(t => t.id === String(response.defaults?.team_id)) ? String(response.defaults.team_id) : '';
  return {members, teams, defaultTeamId, permission, people: new Map(members.map(row => [row.id, row]))};
}

// scope：{kind: 'self'} | {kind: 'team', teamId: 'all' | id} | {kind: 'member', memberId}
export function defaultScope(s = session(), directory, permission = 'visit.read') {
  if (!s) return {kind: 'self'};
  return readScopeFor(permission, s).scopes.includes('workspace') ? {kind: 'team', teamId: 'all'} : {kind: 'self'};
}
export function scopeMembers(scope, directory, s = session()) {
  const members = directory?.members || [];
  if (scope.kind === 'self') return members.filter(row => row.id === String(s?.userId));
  if (scope.kind === 'member') return members.filter(row => row.id === String(scope.memberId));
  if (scope.teamId && scope.teamId !== 'all') return members.filter(row => row.team_ids.includes(String(scope.teamId)));
  return members;
}
export function filterVisitsToScope(visits, scope, directory, s = session()) {
  // 这里只应用明确的页面条件；可读集合由后端决定，不以当前人员目录做二次授权。
  if (scope?.kind === 'self') return visits.filter(visit => visit.recorderId === String(s?.userId));
  if (scope?.kind === 'member') return visits.filter(visit => visit.recorderId === String(scope.memberId));
  if (scope?.teamId && scope.teamId !== 'all') return visits.filter(visit => visit.teamId === String(scope.teamId));
  return visits;
}
export function scopeLabel(scope, directory, s = session()) {
  if (scope.kind === 'self') return '本人';
  if (scope.kind === 'member') return directory?.people.get(String(scope.memberId))?.name || '指定成员';
  if (!scope.teamId || scope.teamId === 'all') return readScopeFor(directory?.permission || 'visit.read', s).scopes.includes('workspace') ? '全公司' : '全部可见部门';
  return directory?.teams.find(team => team.id === String(scope.teamId))?.name || '所选团队';
}
export const scopeKey = scope => [scope.kind, scope.teamId || '', scope.memberId || ''].join(':');

// 请求参数仅表达筛选，授权范围由对应业务接口决定。
export function mapParams(scope, s, range) {
  const params = {};
  // customer-assets/map 只接受活动窗口；季度和自定义区间先取当年活跃客户，
  // 再由逐客户的 /visits 日期过滤收敛到精确区间。
  const window = {day: 'today', week: 'week', month: 'month', quarter: 'year', custom: 'year', all: 'year'}[range.kind];
  if (window) params.activity_window = window;
  if (scope.kind === 'self') params.scope = 'self';
  else if (scope.kind === 'member') { params.scope = 'person'; params.member_id = scope.memberId; }
  else if (scope.teamId && scope.teamId !== 'all') { params.scope = 'team'; params.team_id = scope.teamId; }
  return params;
}
export function opportunityParams(scope, s = session()) {
  if (scope.kind === 'self') return {scope: 'self'};
  if (scope.kind === 'member') return {scope: 'team', ownerId: scope.memberId};
  if (scope.teamId && scope.teamId !== 'all') return {scope: 'team', teamId: scope.teamId};
  return {scope: 'team'};
}

// ---- 跟进记录汇总 ----
const cache = new Map();
export function clearCache() { cache.clear(); }

export async function collectFollowups({range, scope, directory, onProgress, maxCustomers = 400, signal, query = ''}) {
  const s = session();
  if (!s) throw Error('请先登录');
  range = statisticsRange(range);
  query = String(query).trim();
  const key = JSON.stringify([identity(s), range.kind, range.start, range.end, scopeKey(scope), query]);
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < 60000) return hit.value;
  const people = directory?.people || new Map();
  const progress = (done, total) => { if (!signal?.aborted) onProgress?.(done, total); };
  const value = await readFollowups(range, scope, s, people, progress, maxCustomers, signal, query);
  stopIfAborted(signal);
  cache.set(key, {at: Date.now(), value});
  return value;
}

async function fdeFollowups(range, scope, s, people, onProgress, signal) {
  const base = {date_from: range.start, date_to: range.end, limit: 50};
  if (scope.kind === 'member') Object.assign(base, {scope: 'team', member_id: scope.memberId});
  else if (scope.kind === 'team') Object.assign(base, {scope: 'team', ...(scope.teamId && scope.teamId !== 'all' ? {team_id: scope.teamId} : {})});
  else base.scope = 'self';
  const rows = new Map(); let offset = 0; let truncated = false;
  for (let i = 0; i < 40; i++) {
    stopIfAborted(signal);
    const response = await api().getFdeActivity({...base, offset});
    if (!Array.isArray(response?.items)) throw Error('FDE 跟进记录暂未加载完成，请重试');
    // Keep the wire shape until optional detail hydration; normalizing here
    // would discard recorder/date fields needed to protect the activity row.
    for (const raw of response.items) if (raw?.id) rows.set(String(raw.id), raw);
    onProgress?.(rows.size, response.total ?? rows.size);
    if (!response.has_more) break;
    const next = Number(response.next_offset);
    if (!Number.isInteger(next) || next <= offset || i === 39) { truncated = true; break; }
    offset = next;
  }
  const rawItems = [...rows.values()];
  const client = api();
  const readVisit = typeof client.getVisit === 'function' ? client.getVisit.bind(client) : null;
  let detailPartial = false;
  const hydrated = Array(rawItems.length);
  await pool(rawItems.map((raw, index) => ({raw, index})), 4, async ({raw, index}) => {
    const base = normalizeVisit(raw, people);
    if (!readVisit || raw.can_read_detail === false) {
      if (!readVisit && raw.can_read_detail !== false) detailPartial = true;
      hydrated[index] = base;
      return;
    }
    try {
      stopIfAborted(signal);
      const detail = await readVisit(base.id);
      stopIfAborted(signal);
      const full = normalizeVisit({...raw, ...(detail || {})}, people);
      // The activity row is the authoritative identity for this page. A detail
      // response must only fill missing/body fields, never move a row to another
      // visit, date, or recorder if an upstream projection is inconsistent.
      hydrated[index] = {
        ...base,
        ...full,
        id: base.id,
        customerId: base.customerId || full.customerId,
        customerName: base.customerName !== '未关联客户' ? base.customerName : full.customerName,
        opportunityId: base.opportunityId || full.opportunityId,
        opportunityIds: base.opportunityIds.length ? base.opportunityIds : full.opportunityIds,
        opportunityName: base.opportunityName || full.opportunityName,
        recorderId: base.recorderId || full.recorderId,
        recorderName: base.recorderName !== '未记录' ? base.recorderName : full.recorderName,
        date: base.date || full.date,
        at: base.at || full.at,
        createdAt: base.createdAt || full.createdAt,
        source: base.source !== 'unknown' ? base.source : full.source,
        canOpen: base.canOpen,
      };
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      detailPartial = true;
      hydrated[index] = base;
    }
  }, signal);
  const items = filterVisitsToScope(hydrated.filter(Boolean), scope, {members: [...people.values()]}, s)
    .filter(v => v.date >= range.start && v.date <= range.end).sort(byNewest);
  return {items, coverage: {source: 'fde', label: 'FDE 协作记录（/fde/activity）', customers: new Set(items.map(v => v.customerId)).size, truncated, detailPartial, partial: truncated || detailPartial}};
}

async function readFollowups(range, scope, s, people, onProgress, maxCustomers, signal, query) {
  const client = api();
  if (typeof client.listVisitRange === 'function') {
    try {
      return await visitRangeFollowups(range, scope, s, people, onProgress, signal, query);
    } catch (error) {
      // Older local mirrors may not have the Web 1.2.0 range route yet. Keep
      // the old path as a compatibility fallback; all authorization still
      // comes from the backend and the fallback remains read-only.
      if (!(error?.statusCode === 404 || error?.code === 'HTTP_404')) throw error;
    }
  }
  if (query) throw Error('当前服务不支持完整记录搜索，请更新配套服务后重试');
  if (isFdeRole(s.role)) return fdeFollowups(range, scope, s, people, onProgress, signal);
  const map = await api().getCustomerMap(mapParams(scope, s, range));
  const customers = Array.isArray(map?.items) ? map.items : [];
  // 今日 / 本周 / 本月由作战地图的 activity_window 在后端筛出活跃客户；本季度取当年活跃客户后逐个核对跟进日期。
  // 不用 latest_visit_at 预先剔除客户（各端对「最近跟进」的口径可能不同），只用它决定读取顺序。
  let candidates = customers.filter(c => c?.id);
  candidates.sort((a, b) => String(b.latest_visit_at || '').localeCompare(String(a.latest_visit_at || '')));
  const truncated = candidates.length > maxCustomers;
  if (truncated) candidates = candidates.slice(0, maxCustomers);
  const rows = new Map(); let done = 0; let pageTruncated = false;
  onProgress?.(0, candidates.length);
  await pool(candidates, 4, async customer => {
    let offset = 0;
    for (let i = 0; i < 20; i++) {
      stopIfAborted(signal);
      const response = await api().listVisits({customer_id: customer.id, page_size: 100, offset});
      const items = Array.isArray(response?.items) ? response.items : [];
      for (const raw of items) {
        if (!raw?.id) continue;
        const visit = normalizeVisit({customer_name: customer.name, ...raw}, people);
        if (visit.date >= range.start && visit.date <= range.end) rows.set(visit.id, visit);
      }
      if (!response?.has_more || !items.length) break;
      if (i === 19) { pageTruncated = true; break; }
      // 默认按跟进日期排序：本页最早的一条已早于本期起点，就不必再往后翻（升序返回时则继续翻完）。
      const first = dayKey(items[0].visit_date || items[0].interaction_at), last = dayKey(items[items.length - 1].visit_date || items[items.length - 1].interaction_at);
      if (first && last && first >= last && last < range.start) break;
      const next = Number(response.next_offset);
      offset = Number.isInteger(next) && next > offset ? next : offset + items.length;
    }
    onProgress?.(++done, candidates.length);
  }, signal);
  const items = filterVisitsToScope([...rows.values()], scope, people && {members: [...people.values()]}, s);
  const partial = truncated || pageTruncated;
  return {items: items.sort(byNewest), coverage: {source: 'customers', label: '作战地图本期活跃客户的跟进记录', customers: candidates.length, mapTotal: customers.length, truncated: partial, partial}};
}

async function visitRangeFollowups(range, scope, s, people, onProgress, signal, query = '') {
  const params = {date_basis: 'interaction', date_from: range.start, date_to: range.end, page_size: 100, offset: 0, scope: 'all', ...(query ? {q: query} : {})};
  if (scope.kind === 'self') params.scope = 'mine';
  else if (scope.kind === 'member' && scope.memberId) params.recorder_ids = [scope.memberId];
  else if (scope.kind === 'team') {
    const teamId = scope.teamId && scope.teamId !== 'all' ? scope.teamId : '';
    if (teamId) params.team_id = teamId;
  }
  const rows = new Map(); let offset = 0; let truncated = false; let total = 0;
  onProgress?.(0, 1);
  while (true) {
    stopIfAborted(signal);
    const response = await api().listVisitRange({...params, offset});
    if (!Array.isArray(response?.items)) throw Error('跟进记录响应不完整，请重试');
    const items = response.items;
    total = Number(response?.summary?.total) || total || items.length;
    for (const raw of items) if (raw?.id) rows.set(String(raw.id), normalizeVisit(raw, people));
    onProgress?.(rows.size, total || rows.size);
    if (!response?.has_more) break;
    const next = Number(response.next_offset);
    // Match the API's maximum offset, without the former arbitrary 4,000-row cutoff.
    if (!items.length || !Number.isInteger(next) || next <= offset || next > 100000) { truncated = true; break; }
    offset = next;
  }
  // range 接口已按日期和记录人/记录所属团队筛选；目录只提供名字和零记录成员。
  const items = [...rows.values()].sort(byNewest);
  return {items, coverage: {source: 'range', label: '后端跟进范围查询', customers: new Set(items.map(v => v.customerId).filter(Boolean)).size, total, truncated, partial: truncated}};
}

// ---- 汇总口径（工作台、工作报告共用） ----
export function summarize(visits, members = []) {
  const recorderIds = new Set(visits.map(v => v.recorderId).filter(Boolean));
  const memberIds = new Set(members.map(m => m.id));
  const activeMembers = members.filter(m => recorderIds.has(m.id));
  const memberVisits = visits.filter(v => memberIds.has(v.recorderId));
  return {
    visits: visits.length,
    customers: new Set(visits.map(v => v.customerId).filter(Boolean)).size,
    opportunities: new Set(visits.flatMap(v => v.opportunityIds)).size,
    members: members.length,
    activeMembers: activeMembers.length,
    perCapita: members.length ? Math.round(memberVisits.length / members.length * 10) / 10 : null,
    presales: visits.filter(v => v.source === 'fde' || v.source === 'projectbuddy').length,
    withNext: visits.filter(v => v.nextAction).length,
  };
}

export function byMember(visits, members = []) {
  const rows = new Map(members.map(m => [m.id, {id: m.id, name: m.name, team: m.teamLabel || '', role: m.role || '', visits: [], customers: new Set(), opportunities: new Set()}]));
  for (const visit of visits) {
    const id = visit.recorderId || ('name:' + visit.recorderName);
    if (!rows.has(id)) rows.set(id, {id, name: visit.recorderName, team: '', role: visit.source === 'fde' ? 'fde' : '', visits: [], customers: new Set(), opportunities: new Set(), outside: true});
    const row = rows.get(id);
    row.visits.push(visit);
    if (visit.customerId) row.customers.add(visit.customerId);
    visit.opportunityIds.forEach(o => row.opportunities.add(o));
  }
  return [...rows.values()].map(row => ({...row, count: row.visits.length, customerCount: row.customers.size, opportunityCount: row.opportunities.size,
    lastDate: row.visits.map(v => v.date).sort().at(-1) || ''})).sort((a, b) => b.count - a.count || String(a.name).localeCompare(String(b.name), 'zh-CN'));
}

export function groupBy(visits, kind) {
  const groups = new Map();
  for (const visit of visits) {
    const keys = kind === 'opportunity' ? (visit.opportunityIds.length ? visit.opportunityIds : ['none']) : [kind === 'customer' ? visit.customerId || 'none' : kind === 'date' ? visit.date : visit.recorderId || visit.recorderName];
    for (const key of keys) {
      if (!groups.has(key)) groups.set(key, {key, visits: [],
        title: kind === 'customer' ? visit.customerName : kind === 'opportunity' ? (key === 'none' ? '未关联商机' : visit.opportunityNames?.[key] || (visit.opportunityIds.length === 1 ? visit.opportunityName : '') || '关联商机（名称待补充）') : kind === 'date' ? visit.date : visit.recorderName,
        customerId: visit.customerId, customerName: visit.customerName});
      groups.get(key).visits.push(visit);
    }
  }
  return [...groups.values()].map(group => ({...group, count: group.visits.length, lastDate: group.visits.map(v => v.date).sort().at(-1) || '',
    people: [...new Set(group.visits.map(v => v.recorderName))]}));
}

// ---- 其它只读数据 ----
export async function loadTasks(scope) {
  // 与任务页同一套参数：view=self 只看本人；团队视角由后端按权限返回，指定成员用 member_id。
  const params = {tab: 'pending', page_size: 50, order: 'due_asc', view: scope.kind === 'self' ? 'self' : 'team', ...(scope.kind === 'member' ? {member_id: scope.memberId} : {})};
  let response;
  try { response = await api().listTaskPage(params); } catch (error) { return {error: error.message || '任务暂不可用', items: [], total: 0, overdue: [], dueToday: [], hasMore: false}; }
  const items = Array.isArray(response?.items) ? response.items : [];
  const today = todayKey();
  const rows = items.map(task => ({
    id: String(task.id), title: text(task.description) || '任务', status: task.status, due: task.due_at || '', dueDay: dayKey(task.due_at),
    priority: task.priority_code, customerName: text(task.customer_name), opportunityName: text(task.opportunity_name),
    owner: text(task.owner_name || task.assignee_name || (task.assignees || []).map(a => a.name).join('、')), requiresAction: task.requires_action === true,
  }));
  return {
    error: '', items: rows,
    total: Number.isInteger(response?.summary?.filtered_total) ? response.summary.filtered_total : Number.isInteger(response?.total) ? response.total : rows.length,
    overdue: rows.filter(t => t.dueDay && t.dueDay < today),
    dueToday: rows.filter(t => t.dueDay === today),
    hasMore: response?.has_more === true,
    partial: response?.has_more === true,
    scope: {kind: scope?.kind || 'self', memberId: scope?.memberId || '', teamId: scope?.teamId || ''},
  };
}

// 本期完成的任务（工作报告用）：已完成列表按完成时间落在本期内筛选
export async function loadCompletedTasks(range, scope) {
  range = statisticsRange(range);
  const params = {tab: 'completed', page_size: 100, order: 'due_desc', view: scope.kind === 'self' ? 'self' : 'team', ...(scope.kind === 'member' ? {member_id: scope.memberId} : {})};
  if (scope.kind === 'self') params.member_id = session()?.userId;
  if (scope.kind === 'team' && scope.teamId && scope.teamId !== 'all') params.team_id = scope.teamId;
  const startYear = Number(String(range?.start || '').slice(0, 4));
  const endYear = Number(String(range?.end || '').slice(0, 4));
  if (startYear >= 2000 && startYear <= 2100 && startYear === endYear) {
    const startMonth = Number(String(range.start).slice(5, 7));
    const endMonth = Number(String(range.end).slice(5, 7));
    params.completed_year = startYear;
    params.completed_quarters = [...new Set([startMonth, endMonth].filter(Number.isFinite).map(month => Math.floor((month - 1) / 3) + 1))];
  }
  try {
    const response = await api().listTaskPage(params);
    const items = (response?.items || []).filter(task => { const day = dayKey(task.completed_at || task.updated_at); return day && day >= range.start && day <= range.end; });
    return {items: items.map(task => ({id: String(task.id), title: text(task.description) || '任务', owner: text(task.owner_name || task.assignee_name), customerName: text(task.customer_name), completedDay: dayKey(task.completed_at || task.updated_at)})), partial: response?.has_more === true, error: ''};
  } catch (error) { return {items: [], partial: false, error: error.message || '已完成任务暂不可用'}; }
}

// 本期新增的商机：按创建时间筛选，最多读取 500 条商机（超出时在页面注明）
export async function loadNewOpportunities(range, scope) {
  range = statisticsRange(range);
  const s = session(), rows = []; let offset = 0, partial = false;
  try {
    for (let i = 0; i < 5; i++) {
      const response = await api().listOpportunities({...opportunityParams(scope, s), includeClosed: true, pageSize: 100, offset});
      rows.push(...(response?.items || []));
      if (!response?.has_more) break;
      if (i === 4) partial = true;
      offset = Number(response.next_offset) || offset + 100;
    }
    const items = rows.filter(o => { const day = dayKey(o.created_at); return day && day >= range.start && day <= range.end; });
    return {items: items.map(o => ({id: String(o.id), customerId: String(o.customer_id || ''), name: text(o.name), customerName: text(o.customer_name), amount: Number(o.amount) || 0, stage: stageLabel(o), owner: text(o.owner_name), createdDay: dayKey(o.created_at)})), partial, error: ''};
  } catch (error) { return {items: [], partial: false, error: error.message || '商机暂不可用'}; }
}

export async function loadRisks() {
  try {
    const response = await api().listRisks('');
    const items = (response?.items || []).filter(r => r.status !== 'resolved');
    return {items: items.map(r => ({id: String(r.id), title: text(r.title || r.risk_title || r.summary) || '风险', level: r.level || r.severity || r.severity_code || '', customerName: text(r.customer_name), opportunityName: text(r.opportunity_name), at: r.created_at || r.detected_at || ''})), error: ''};
  } catch (error) {
    // An authorization error is not an empty authorized result.
    return {items: [], error: error.message || '风险暂不可用'};
  }
}

export async function loadActiveOpportunities(range, scope, visits) {
  const ids = new Set(visits.flatMap(v => v.opportunityIds));
  if (!ids.size) return {items: [], total: 0, error: ''};
  try {
    // 记录人不一定是商机负责人。范围由已授权、已按记录人过滤的 visits 决定；
    // 从当前授权商机集合补齐展示字段，不能再次套负责人筛选而漏掉协作商机。
    const found = new Map(); let offset = 0;
    for (let i = 0; i < 40; i++) {
      const response = await api().listOpportunities({scope: 'team', activityWindow: 'all', includeClosed: true, pageSize: 100, offset});
      for (const row of response?.items || []) if (ids.has(String(row.id))) found.set(String(row.id), row);
      if (found.size === ids.size || !response?.has_more) break;
      const next = Number(response.next_offset);
      if (!Number.isInteger(next) || next <= offset) break;
      offset = next;
    }
    const items = groupBy(visits, 'opportunity').filter(group => group.key !== 'none').map(group => found.get(group.key) || {
      id: group.key, customer_id: group.customerId, customer_name: group.customerName, name: group.title, stage_name: '详情暂不可用', amount: null});
    return {items: items.map(o => ({id: String(o.id), customerId: String(o.customer_id || ''), name: text(o.name), customerName: text(o.customer_name),
      stage: stageLabel(o), stageCode: o.stage_code, probability: o.probability, status: o.status,
      amount: o.amount == null ? null : Number(o.amount), close: o.expected_close_date || '', owner: text(o.owner_name), createdAt: o.created_at || '',
      lastVisit: visits.filter(v => v.opportunityIds.includes(String(o.id))).sort(byNewest)[0] || null})),
      total: ids.size, error: ''};
  } catch (error) { return {items: [], total: 0, error: error.message || '商机暂不可用'}; }
}

// 阶段名来自 /metadata/business-options（原接口的业务目录），不在前端写死
export function stageLabel(o) {
  if (o.stage_name || o.stage_label) return o.stage_name || o.stage_label;
  try { const stage = runtime().require('utils/businessOptions').stages.find(row => row.code === o.stage_code); if (stage) return stage.label; } catch (_) {}
  return o.stage_code || '';
}
export const amountWan = value => {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  const wan = number / 10000;
  return wan >= 100 ? Math.round(wan).toLocaleString('zh-CN') : (Math.round(wan * 10) / 10).toLocaleString('zh-CN');
};
export const dateLabel = key => {
  if (!key) return '';
  const [, m, d] = key.split('-').map(Number);
  const week = '日一二三四五六'[new Date(key + 'T00:00:00Z').getUTCDay()];
  return `${m}月${d}日 周${week}`;
};
export const relativeDay = key => {
  if (!key) return '';
  const today = todayKey(), diff = Math.round((Date.parse(today + 'T00:00:00Z') - Date.parse(key + 'T00:00:00Z')) / 86400000);
  return diff === 0 ? '今天' : diff === 1 ? '昨天' : diff === -1 ? '明天' : diff > 1 && diff < 7 ? `${diff} 天前` : dateLabel(key);
};

// 打开原页面：客户详情（含跟进时间轴）、拜访详情、商机详情、任务详情
export const open = {
  customer: id => id && runtime().userRoute('/pages/customer-detail/index?id=' + encodeURIComponent(id)),
  visit: (id, customerId = '') => id && runtime().userRoute('/pages/visit-detail/index?visit_id=' + encodeURIComponent(id) + '&customer_id=' + encodeURIComponent(customerId || '')),
  // 与商机列表一致：进入商机详情（经营实绩与跟进）只读查看
  opportunity: (customerId, opportunityId) => opportunityId && runtime().userRoute('/pages/customer-assets/index?customer_id=' + encodeURIComponent(customerId || '') + '&opportunity_id=' + encodeURIComponent(opportunityId) + '&period=all&readonly=1'),
  task: id => id && runtime().userRoute('/pages/task-detail/index?id=' + encodeURIComponent(id)),
  // 发起售前支持：带着客户与商机进入「创建任务」，接收人可选 FDE（ProjectBuddy 侧据此承接为售前项目）
  presales: (customerId, opportunityId) => runtime().userRoute('/pages/management-task-create/index?customerId=' + encodeURIComponent(customerId || '') + '&opportunityId=' + encodeURIComponent(opportunityId || '')),
  risk: id => id && runtime().userRoute('/pages/risk-detail/index?id=' + encodeURIComponent(id)),
  route: path => runtime().userRoute('/' + path.replace(/^\//, '')),
  // 同一页面换参数（日报 ↔ 周报）：替换当前页，不多一层返回
  replace: path => runtime().userRoute('/' + path.replace(/^\//, ''), {replace: true}),
};
