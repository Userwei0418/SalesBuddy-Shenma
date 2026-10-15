import {state, $, api, esc, options, head, table, bindCommon, hasPermission, requirePermission, dialog, dialogIntent} from './core.js';

const PAGE_SIZE = 50;
const DAY = 86400000;
const labels = {pending_confirm:'未领取 / 待接受', pending_execution:'待执行', in_progress:'执行中', deferred:'延期', pending_review:'待验收', completed:'已完成', cancelled:'已取消'};
const terminal = new Set(['completed', 'cancelled']);
const datePattern = /^\d{4}-\d{2}-\d{2}$/;
const dayString = value => new Date(value).toISOString().slice(0, 10);
const dayNumber = value => {
  if (!datePattern.test(value || '')) return null;
  const n = Date.parse(value + 'T00:00:00Z');
  return Number.isFinite(n) && dayString(n) === value ? n : null;
};
const beijingDay = value => {
  const n = Date.parse(value);
  return Number.isFinite(n) ? dayString(n + 8 * 3600000) : null;
};
const shortDay = value => value?.slice(5).replace('-', '/') || '未设置';
const beijingTime = value => {
  const n = Date.parse(value);
  return Number.isFinite(n) ? new Date(n + 8*3600000).toISOString().slice(0,16).replace('T',' ') : '未设置';
};
const queryString = values => '?' + new URLSearchParams(Object.entries(values).filter(([,v]) => v !== '' && v !== null && v !== undefined));

export function calendarWindow(anchor, period = 'week') {
  const day = dayNumber(anchor);
  if (day === null) throw Error('请选择有效的日期');
  const d = new Date(day);
  let start, end;
  if (period === 'month') {
    start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
    end = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 0);
  } else {
    start = day - ((d.getUTCDay() + 6) % 7) * DAY;
    end = start + 6 * DAY;
  }
  return {from:dayString(start), to:dayString(end), days:Array.from({length:(end-start)/DAY+1}, (_,i) => dayString(start+i*DAY))};
}

// Calendar dates use Beijing day boundaries, independent of the browser timezone.
// A bar represents the planned interval only; acceptance and completion do not move it.
export function plannedInterval(task, window) {
  const start = beijingDay(task.schedule_start_at), due = beijingDay(task.due_at);
  if (!due) return {kind:'undated'};
  if (!start || task.schedule_invalid || Date.parse(task.due_at) < Date.parse(task.schedule_start_at)) return {kind:'invalid', start, due};
  const left = dayNumber(start), right = dayNumber(due), from = dayNumber(window.from), to = dayNumber(window.to);
  if (right < from || left > to) return {kind:'outside', start, due};
  const first = Math.max(left, from), last = Math.min(right, to);
  return {kind:'scheduled', start, due, column:Math.round((first-from)/DAY)+1, span:Math.round((last-first)/DAY)+1, before:left<from, after:right>to};
}

export function managementFilters(filters, now = new Date().toISOString()) {
  const result = {scope:filters.scope === 'mine' ? 'mine' : 'all'};
  for (const name of ['status', 'member_id', 'batch_id']) if (filters[name]) result[name] = filters[name];
  if (filters.unassigned === true || filters.unassigned === 'true') { result.unassigned = true; delete result.member_id; }
  if (filters.overdue === true || filters.overdue === 'true') result.overdue = true;
  result.undated = ['exclude', 'only'].includes(filters.undated) ? filters.undated : 'include';
  for (const name of ['date_from', 'date_to']) if (filters[name] && dayNumber(filters[name]) !== null) result[name] = filters[name];
  if (filters.layout === 'timeline') {
    const window = calendarWindow(result.date_from || beijingDay(now), filters.period);
    result.date_from = window.from; result.date_to = window.to;
  }
  if (result.date_from && result.date_to && result.date_from > result.date_to) throw Error('开始日期不能晚于结束日期');
  return result;
}

function owners(task) {
  if (Array.isArray(task.owners) && task.owners.length) return task.owners.map(p=>({id:p.id,name:p.name,inactive:p.active===false}));
  const people = (task.assignees || []).filter(a => a.responsibility === 'owner');
  if (people.length) return people.map(p=>({id:p.user_id,name:p.name,inactive:p.active===false || (p.user_id===task.owner_id && task.owner_active===false)}));
  return [{id:task.owner_id || '', name:task.owner_name || '待领取 · 未分配负责人', inactive:task.owner_active === false}];
}
function status(task, asOf) {
  const overdue = !terminal.has(task.status) && task.due_at && Date.parse(task.due_at) < Date.parse(asOf);
  return `<span class="tm-status tm-${esc(task.status)}">${esc(labels[task.status] || task.status)}</span>${overdue?'<span class="tm-flag tm-overdue">逾期</span>':''}${task.handover_required?'<span class="tm-flag tm-overdue">待交接</span>':''}`;
}
function ownerLabel(task) {
  return owners(task).map(who=>`<div class="tm-owner-name"><strong>${esc(who.name)}</strong>${who.inactive?'<span class="tm-flag tm-overdue">账号停用</span>':''}</div>`).join('');
}
function summaryCards(summary) {
  return `<div class="tm-stats" aria-label="全部筛选结果统计">${[
    ['未完成', 'pending_count', '含未领取、执行中及待验收'],
    ['待验收', 'pending_review_count', '已提交，待发起人确认'],
    ['逾期', 'overdue_count', '超出截止时间且尚未结束'],
    ['已完成', 'completed_count', '已确认完成'],
    ['已取消', 'cancelled_count', '保留任务与处理记录'],
  ].map(([label,key,help]) => `<div class="tm-stat"><span>${label}</span><strong>${esc(summary[key] ?? '—')}</strong><small>${help}</small></div>`).join('')}</div>`;
}
function list(items, asOf) {
  return table(['任务 / 批次', '当前负责人', '安排日期', '状态', '操作'], items, task => `<tr>
    <td class="tm-task-title"><button class="link" data-action="detail" data-id="${esc(task.id)}">${esc(task.title)}</button><small>${esc(task.batch_name || '独立任务')} · ${esc(task.creator_name || '发起人')}</small></td>
    <td>${ownerLabel(task)}</td><td><span>${beijingDay(task.schedule_start_at) || '未设置'} ${task.start_kind==='dispatched'?'下发':'创建'}</span><small>${task.due_at?`${beijingTime(task.due_at)} 截止`:'未设置截止日期'}${task.schedule_invalid?' · 日期待核对':''}</small></td>
    <td><div class="tm-statuses">${status(task, asOf)}</div></td><td><button class="link" data-action="detail" data-id="${esc(task.id)}">查看 / 处理</button></td></tr>`);
}
function timeline(items, window, asOf) {
  const groups = new Map();
  for (const task of items) {
    for (const who of owners(task)) {
      const key = who.id || 'unassigned';
      if (!groups.has(key)) groups.set(key, {who, tasks:[]});
      groups.get(key).tasks.push(task);
    }
  }
  const columns = window.days.length;
  const header = `<div class="tm-timeline-head"><div class="tm-owner-heading">当前负责人</div><div class="tm-days">${window.days.map(d => `<span class="${[0,6].includes(new Date(d+'T00:00:00Z').getUTCDay())?'is-weekend':''}">${shortDay(d)}<small>${['日','一','二','三','四','五','六'][new Date(d+'T00:00:00Z').getUTCDay()]}</small></span>`).join('')}</div><div class="tm-unscheduled-heading">无截止 / 日期待核对</div></div>`;
  const rows = [...groups.values()].map(({who,tasks}) => {
    const scheduled = [], unscheduled = [];
    for (const task of tasks) {
      const range = plannedInterval(task, window);
      const title = `${task.title}；${labels[task.status] || task.status}；${range.start || '未设置'} ${task.start_kind==='dispatched'?'下发':'创建'}；${range.due || '未设置'} 截止`;
      if (range.kind !== 'scheduled') {
        const note = {undated:'未设置截止日期', invalid:'日期待核对', outside:'安排在当前日期范围外'}[range.kind];
        unscheduled.push(`<button class="tm-unscheduled-task" data-action="detail" data-id="${esc(task.id)}"><strong>${esc(task.title)}</strong><span>${note}</span>${status(task,asOf)}</button>`);
      } else {
        const overdue = !terminal.has(task.status) && Date.parse(task.due_at)<Date.parse(asOf);
        scheduled.push(`<div class="tm-lane"><button class="tm-bar tm-bar-${esc(task.status)}" style="grid-column:${range.column} / span ${range.span}" data-action="detail" data-id="${esc(task.id)}" title="${esc(title)}" aria-label="${esc(title)}"><strong>${range.before?'‹ ':''}${esc(task.title)}${range.after?' ›':''}</strong><span>${esc(labels[task.status] || task.status)}${overdue?' · 逾期':''}${task.handover_required?' · 待交接':''}</span></button></div>`);
      }
    }
    return `<section class="tm-owner-row" aria-label="${esc(who.name)}"><div class="tm-owner-heading"><strong>${esc(who.name)}</strong>${who.inactive?'<span class="tm-flag tm-overdue">账号停用</span>':''}<small>本页 ${tasks.length} 项</small></div><div class="tm-lanes">${scheduled.join('') || '<p class="tm-lane-empty">本页暂无可绘制的计划区间</p>'}</div><div class="tm-unscheduled">${unscheduled.join('') || '<span class="muted">—</span>'}</div></section>`;
  }).join('');
  return `<div class="tm-timeline-scroll" tabindex="0" aria-label="按负责人查看计划时间线，可横向滚动"><div class="tm-timeline" style="--tm-days:${columns}">${header}${rows || '<div class="empty">当前筛选没有任务</div>'}</div></div>`;
}

function validatePage(result, offset) {
  if (!Array.isArray(result.items) || (result.has_more && (!Number.isInteger(result.next_offset) || result.next_offset <= offset))) throw Error('任务列表已变化，请刷新后重试');
}

// Read-only picker: it never fetches a batch's source text and never posts a draft.
export function openManagementPicker(kind, filters, onSelect) {
  requirePermission('task.read');
  const isOwner = kind === 'owner';
  const root = dialog(isOwner?'选择负责人':'选择已下发批次', `${isOwner?'<div class="tm-picker-search"><input type="search" id="tm-owner-search" aria-label="搜索负责人" placeholder="姓名或账号"><button type="button" id="tm-picker-search">搜索</button></div>':''}<div id="tm-picker-results" aria-live="polite"></div><div class="tm-picker-error" role="alert"></div><div class="tm-picker-pager"><button type="button" id="tm-picker-prev">上一页</button><span id="tm-picker-page"></span><button type="button" id="tm-picker-next">下一页</button></div>`, {wide:true});
  const current = dialogIntent({liveForm:true});
  let serial = 0, offset = 0, nextOffset = null, q = '';
  const live = () => current() && hasPermission('task.read');
  const choose = patch => { if (!live()) return; root.close(); onSelect(patch); };
  async function load(next = 0) {
    const request = ++serial;
    $('#tm-picker-prev', root).disabled = true; $('#tm-picker-next', root).disabled = true;
    $('.tm-picker-error',root).textContent = '';
    $('#tm-picker-results',root).innerHTML = '<p class="muted">正在加载…</p>';
    try {
      let values;
      if (isOwner) { values = {...filters, page_size:100, offset:next, q}; delete values.member_id; delete values.unassigned; }
      else values = {status:'confirmed', page_size:50, offset:next};
      const data = await api((isOwner?'/api/v1/tasks/management/owners':'/api/v1/task-imports') + queryString(values));
      if (!live() || request !== serial) return;
      validatePage(data,next); offset = next; nextOffset = data.has_more ? data.next_offset : null;
      const choices = [{value:'',name:isOwner?'全部负责人':'全部批次'}, ...(isOwner?[{value:'unassigned',name:`待领取 · 未分配负责人（${data.unassigned_count ?? 0} 项）`}]:[]), ...data.items.map(row => ({value:row.id, name:isOwner?`${row.name} · ${row.account_code}${row.active===false?' · 账号停用':''}（${row.task_count} 项）`:`${row.batch_name} · ${row.meeting_date}`}))];
      $('#tm-picker-results',root).innerHTML = choices.map((choice,i)=>`<button type="button" class="tm-picker-choice" data-choice="${i}">${esc(choice.name)}</button>`).join('') + (!data.items.length?'<p class="muted">暂无符合条件的记录</p>':'');
      root.querySelectorAll('[data-choice]').forEach(button => { button.onclick = () => {
        const choice = choices[Number(button.dataset.choice)];
        if (isOwner) choose({member_id:choice.value==='unassigned'?'':choice.value, member_name:choice.value?choice.name:'', unassigned:choice.value==='unassigned'?'true':''});
        else choose({batch_id:choice.value, batch_name:choice.value?choice.name:''});
      }; });
      $('#tm-picker-page',root).textContent = `第 ${Math.floor(offset/(isOwner?100:50))+1} 页${data.has_more?' · 后面还有更多记录':''}`;
      $('#tm-picker-prev',root).disabled = !offset; $('#tm-picker-next',root).disabled = nextOffset === null;
    } catch(error) { if(live() && request === serial) $('.tm-picker-error',root).textContent = error.message; }
  }
  $('#tm-picker-prev',root).onclick = () => load(Math.max(0, offset-(isOwner?100:50)));
  $('#tm-picker-next',root).onclick = () => nextOffset === null ? undefined : load(nextOffset);
  if (isOwner) {
    const search = () => { if(!live()) return; q=$('#tm-owner-search',root).value.trim(); return load(); };
    $('#tm-picker-search',root).onclick = search;
    $('#tm-owner-search',root).onkeydown = event => { if(event.key==='Enter') {event.preventDefault(); search();} };
  }
  return load();
}

export async function taskManagement({showTask, newTaskImport}, {isCurrent = () => true} = {}) {
  requirePermission('task.read');
  const saved = {...state.filters}, offset = state.offset, filters = managementFilters(saved);
  const result = await api('/api/v1/tasks/management' + queryString({...filters, page_size:PAGE_SIZE, offset}));
  if (!isCurrent()) return {html:''};
  validatePage(result,offset);
  const summary = result.summary || {}, layout = saved.layout === 'timeline' ? 'timeline' : 'list';
  const period = saved.period === 'month' ? 'month' : 'week', window = layout==='timeline'?calendarWindow(filters.date_from, period):null;
  const canCreate = hasPermission('task.create_daily') || hasPermission('task.create_customer');
  const count = `本页 ${result.items.length} 项 · 全部筛选结果 ${summary.total ?? '—'} 项`;
  const ownerText = filters.unassigned?'待领取 · 未分配负责人':filters.member_id?(saved.member_name || '已选负责人'):'全部负责人';
  const batchText = filters.batch_id?(saved.batch_name || '已选批次'):'全部批次';
  const form = `<form id="filters" class="toolbar tm-filters"><label><span>负责人</span><button type="button" id="tm-owner-filter">${esc(ownerText)}</button></label><label><span>批次</span><button type="button" id="tm-batch-filter">${esc(batchText)}</button></label><label><span>任务状态</span><select name="status">${options(Object.entries(labels), filters.status, '全部状态')}</select></label><label><span>截止日期</span><select name="undated">${options([['include','含无截止日期'],['exclude','仅有截止日期'],['only','仅无截止日期']], filters.undated, null)}</select></label><label><span>安排日期 · 从</span><input type="date" name="date_from" value="${filters.date_from || ''}"></label><label><span>至</span><input type="date" name="date_to" value="${filters.date_to || ''}" ${layout==='timeline'?'readonly':''}></label><label class="tm-overdue-filter"><input type="checkbox" name="overdue" value="true" ${filters.overdue?'checked':''}>仅看逾期</label><button type="submit">筛选</button><button type="button" id="tm-reset" class="link">重置</button><p class="tm-filter-note">按计划区间筛选${layout==='timeline'?'；时间线按所选日期所在周 / 月展示':''}，无截止日期的任务单独标注。日期均为北京时间。</p><div id="tm-filter-error" role="alert"></div></form>`;
  return {html:`<div class="task-management">${head('任务管理','按负责人和安排日期查看任务，跟进领取、执行与验收。',`${canCreate?'<button class="primary" id="new-task-import">发放周任务</button>':''}<button id="task-import-batches">批次与进度</button>`)}${summaryCards(summary)}
    <section class="card tm-card"><div class="tm-viewbar"><div class="tm-segment" aria-label="任务范围"><button type="button" data-tm-scope="all" aria-pressed="${filters.scope==='all'}">全部可见任务</button><button type="button" data-tm-scope="mine" aria-pressed="${filters.scope==='mine'}">与我有关</button></div><div class="tm-segment" aria-label="展示方式"><button type="button" data-tm-layout="list" aria-pressed="${layout==='list'}">列表</button><button type="button" data-tm-layout="timeline" aria-pressed="${layout==='timeline'}">按负责人时间线</button></div></div>${form}
    ${layout==='timeline'?`<div class="tm-calendar-toolbar"><div class="tm-segment" aria-label="时间线范围"><button type="button" data-tm-period="week" aria-pressed="${period==='week'}">周</button><button type="button" data-tm-period="month" aria-pressed="${period==='month'}">月</button></div><div class="tm-calendar-navigation"><button type="button" data-tm-shift="-1" aria-label="上${period==='month'?'月':'周'}">‹</button><strong>${window.from} — ${window.to}</strong><button type="button" data-tm-shift="1" aria-label="下${period==='month'?'月':'周'}">›</button><button type="button" id="tm-today">回到本${period==='month'?'月':'周'}</button></div></div>`:''}
    <div class="tm-result-note"><strong>${count}</strong><span>统计覆盖全部筛选结果${layout==='timeline'?'；下方仅绘制本页任务，更多负责人和任务请翻页':''}。</span></div>
    ${layout==='timeline'?`<p class="tm-timeline-note">色条表示下发 / 创建至截止的计划区间，不代表实际开工或完成进度。多人负责的任务在各负责人行展示，统计只计一项。横向滚动查看完整日期与无截止任务，点击任务查看详情。</p>${timeline(result.items,window,result.as_of)}`:list(result.items,result.as_of)}
    <div class="pager"><span>第 ${Math.floor(offset/PAGE_SIZE)+1} 页 · 每页最多 ${PAGE_SIZE} 项</span><div><button id="task-prev" ${offset?'':'disabled'}>上一页</button><button id="task-next" ${result.has_more?'':'disabled'}>下一页</button></div></div></section></div>`, bind:root=>{
      const identity = JSON.stringify(state.actor), company = state.company, page = state.page;
      const current = () => isCurrent() && root === $('#content') && state.page===page && state.company===company && JSON.stringify(state.actor)===identity && hasPermission('task.read');
      const refresh = (patch, next=0) => { if(!current()) return; state.filters={...saved,...patch};state.offset=next;return state.refresh(); };
      bindCommon(root,{detail:async id => {if(current())return showTask(id);}});
      $('#filters',root).onsubmit = event => {
        event.preventDefault(); if(!current())return;
        const patch = Object.fromEntries(new FormData(event.currentTarget)); patch.overdue ||= '';
        try {
          const parsed = managementFilters({...saved,...patch});
          if(layout==='timeline') {patch.date_from=parsed.date_from;patch.date_to=parsed.date_to;}
          return refresh(patch);
        } catch(error) { $('#tm-filter-error',root).textContent=error.message; }
      };
      $('#tm-reset',root).onclick = () => {if(!current())return;state.filters={};state.offset=0;return state.refresh();};
      root.querySelectorAll('[data-tm-scope]').forEach(b=>b.onclick=()=>refresh({scope:b.dataset.tmScope}));
      root.querySelectorAll('[data-tm-layout]').forEach(b=>b.onclick=()=>refresh({layout:b.dataset.tmLayout}));
      root.querySelectorAll('[data-tm-period]').forEach(b=>b.onclick=()=>refresh({period:b.dataset.tmPeriod}));
      root.querySelectorAll('[data-tm-shift]').forEach(b=>b.onclick=()=>{
        const d = new Date(window.from+'T00:00:00Z'), shift = Number(b.dataset.tmShift);
        if(period==='month')d.setUTCMonth(d.getUTCMonth()+shift);else d.setUTCDate(d.getUTCDate()+7*shift);
        const next = calendarWindow(dayString(d),period); return refresh({date_from:next.from,date_to:next.to});
      });
      if($('#tm-today',root))$('#tm-today',root).onclick=()=>{const next=calendarWindow(beijingDay(new Date().toISOString()),period);return refresh({date_from:next.from,date_to:next.to});};
      $('#tm-owner-filter',root).onclick=()=>current()?openManagementPicker('owner',filters,patch=>refresh(patch)):undefined;
      $('#tm-batch-filter',root).onclick=()=>current()?openManagementPicker('batch',filters,patch=>refresh(patch)):undefined;
      if($('#new-task-import',root))$('#new-task-import',root).onclick=()=>{if(current() && (hasPermission('task.create_daily')||hasPermission('task.create_customer')))return newTaskImport();};
      $('#task-import-batches',root).onclick=()=>refresh({view:'imports'});
      $('#task-prev',root).onclick=()=>offset?refresh({},Math.max(0,offset-PAGE_SIZE)):undefined;
      $('#task-next',root).onclick=()=>result.has_more?refresh({},result.next_offset):undefined;
    }};
}
