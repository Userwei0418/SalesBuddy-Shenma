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
  const allowed = accessModule().can(s,permission);
  const scopes = allowed ? [...new Set((s?.permissionGrants || []).filter(g => g.permission_code === permission && g.effect === 'allow').map(g => g.scope_code))] : [];
  return {allowed, scopes};
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
    customerIds: [...new Set([raw.customer_id,...(raw.linked_opportunities || []).map(o=>o.customer_id)].filter(Boolean).map(String))],
    customerNames: {...Object.fromEntries((raw.linked_opportunities || []).filter(o=>o.customer_id).map(o=>[String(o.customer_id),o.customer_name || '关联客户'])), ...(raw.customer_id ? {[String(raw.customer_id)]:raw.customer_name || '关联客户'} : {})},
    customerAccess: raw.customer_id ? {[String(raw.customer_id)]:raw.can_open_customer === true} : {},
    customerName: text(raw.customer_name) || text(raw.partner_name_snapshot) || '未关联客户',
    opportunityId: opportunityIds[0] || '',
    opportunityIds,
    opportunityNames: Object.fromEntries((raw.linked_opportunities || []).filter(o => o.id && o.name).map(o => [String(o.id), o.name])),
    opportunityAccess: Object.fromEntries((raw.linked_opportunities || []).filter(o => o.id && typeof o.can_open === 'boolean').map(o => [String(o.id), o.can_open])),
    opportunityCustomers: Object.fromEntries((raw.linked_opportunities || []).filter(o => o.id && o.customer_id).map(o => [String(o.id), {id:String(o.customer_id),name:o.customer_name || ''}])),
    canOpenCustomer: raw.can_open_customer !== false,
    canOpenOpportunity: raw.can_open_opportunity !== false,
    isSummary: raw.is_summary === true,
    opportunityName: text(raw.opportunity_name) || (raw.linked_opportunities || []).map(o => o.name).filter(Boolean).join('、'),
    recorderId: recorderId ? String(recorderId) : '',
    recorderName: text(raw.original_recorder_name) || text(raw.recorder_name) || text(raw.creator_name) || recorder?.name || '未记录',
    teamId: text(raw.recording_team_id_snapshot || raw.recorder_team_id || raw.recording_team_id || raw.team_id),
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
  const s=session(), owner=identity(s);
  if(!s || !can(permission)) throw Error('当前身份没有跟进记录读取权限');
  const self={id:String(s.userId),name:s.userName || s.displayName || '本人',team_ids:s.teamIds || [],role:s.role};
  if(!can('directory.read')) return {members:[self],teams:[],permission,people:new Map([[self.id,self]]),complete:false};
  // Shenma directory keeps its existing RLS/grants; it is only a selector, never an authorizer.
  const response=await api().getDirectoryMembers();
  if(owner !== identity()) throw Error('身份或权限已变化，请刷新后重试');
  if(!Array.isArray(response?.items) || !Array.isArray(response?.teams)) throw Error('人员目录响应不完整，请重试');
  const teams=response.teams.filter(row=>row.id).map(row=>({id:String(row.id),name:row.name || row.label || ''}));
  const members=response.items.filter(row=>row.id && row.active !== false).map(row=>({...row,id:String(row.id),name:row.name || row.display_name || '未登记',team_ids:[...new Set([...(row.team_ids || []),row.team_id].filter(Boolean).map(String))],teamLabel:row.team || ''}));
  return {members,teams,permission,people:new Map(members.map(row=>[row.id,row])),complete:false};
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

// Read-only Shenma range endpoint; no client-side legacy fallback can broaden a filter.
export function clearCache() {}
export async function collectFollowups({range, scope, directory, onProgress, signal, query = ''}) {
  const owner = identity(), s = session();
  if (!s || !can('visit.read')) throw Error('当前身份没有跟进记录读取权限');
  range = statisticsRange(range);
  const params = {date_basis:'interaction', date_from:range.start, date_to:range.end,
    page_size:100, scope:scope.kind === 'self' ? 'mine' : 'all', ...(query ? {q:query} : {})};
  if(scope.kind === 'member') params.recorder_ids = [scope.memberId];
  if(scope.kind === 'team' && scope.teamId !== 'all') params.team_id = scope.teamId;
  const rows = new Map(); let offset=0, expectedTotal=null;
  for(let page=0;page<100;page++) {
    stopIfAborted(signal);
    if(owner !== identity()) throw Error('身份或权限已变化，请刷新后重试');
    const response=await api().listVisitRange({...params,offset});
    stopIfAborted(signal);
    if(owner !== identity()) throw Error('身份或权限已变化，请刷新后重试');
    if(!Array.isArray(response?.items) || typeof response.has_more !== 'boolean' || !Number.isInteger(response.summary?.total)) throw Error('跟进记录响应不完整，请重试');
    if(expectedTotal !== null && response.summary.total !== expectedTotal) throw Error('记录已变化，请刷新后重试');
    expectedTotal=response.summary.total;
    for(const raw of response.items) {
      if(!raw.id || rows.has(String(raw.id))) throw Error('记录分页已变化，请刷新后重试');
      rows.set(String(raw.id),normalizeVisit(raw,directory?.people));
    }
    onProgress?.(rows.size,expectedTotal);
    if(!response.has_more) {
      if(rows.size !== expectedTotal) throw Error('记录尚未读取完整，请刷新后重试');
      return {items:[...rows.values()].sort(byNewest),coverage:{source:'range',partial:false}};
    }
    if(!Number.isInteger(response.next_offset) || response.next_offset <= offset) throw Error('记录分页异常，请重试');
    offset=response.next_offset;
  }
  return {items:[...rows.values()].sort(byNewest),coverage:{source:'range',partial:true}};
}

export function summarize(visits, members = []) {
  const recorderIds = new Set(visits.map(v => v.recorderId).filter(Boolean));
  const memberIds = new Set(members.map(m => m.id));
  const activeMembers = members.filter(m => recorderIds.has(m.id));
  const memberVisits = visits.filter(v => memberIds.has(v.recorderId));
  return {
    visits: visits.length,
    customers: new Set(visits.flatMap(v => v.customerIds || [v.customerId]).filter(Boolean)).size,
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
    (visit.customerIds || [visit.customerId]).filter(Boolean).forEach(id=>row.customers.add(id));
    visit.opportunityIds.forEach(o => row.opportunities.add(o));
  }
  return [...rows.values()].map(row => ({...row, count: row.visits.length, customerCount: row.customers.size, opportunityCount: row.opportunities.size,
    lastDate: row.visits.map(v => v.date).sort().at(-1) || ''})).sort((a, b) => b.count - a.count || String(a.name).localeCompare(String(b.name), 'zh-CN'));
}

export function groupBy(visits, kind) {
  const groups = new Map();
  for (const visit of visits) {
    const keys = kind === 'opportunity' ? (visit.opportunityIds.length ? visit.opportunityIds : ['none']) : kind === 'customer' ? (visit.customerIds?.length ? visit.customerIds : [visit.customerId || 'none']) : [kind === 'date' ? visit.date : visit.recorderId || visit.recorderName];
    for (const key of keys) {
      if (!groups.has(key)) groups.set(key, {key, visits: [],
        title: kind === 'customer' ? (key === 'none' ? '未关联客户' : visit.customerNames?.[key] || visit.customerName) : kind === 'opportunity' ? (key === 'none' ? '未关联商机' : visit.opportunityNames?.[key] || (visit.opportunityIds.length === 1 ? visit.opportunityName : '') || '关联商机（名称待补充）') : kind === 'date' ? visit.date : visit.recorderName,
        customerId: kind === 'opportunity' ? (visit.opportunityCustomers?.[key]?.id || (key === visit.opportunityId ? visit.customerId : '')) : kind === 'customer' ? key : visit.customerId, customerName: kind === 'opportunity' ? (visit.opportunityCustomers?.[key]?.name || (key === visit.opportunityId ? visit.customerName : '')) : visit.customerName});
      groups.get(key).visits.push(visit);
    }
  }
  return [...groups.values()].map(group => ({...group, count: group.visits.length, lastDate: group.visits.map(v => v.date).sort().at(-1) || '',
    people: [...new Set(group.visits.map(v => v.recorderName))]}));
}

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
