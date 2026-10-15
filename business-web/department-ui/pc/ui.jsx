import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Button, Empty, Segmented, Select, Skeleton, Tooltip} from 'antd';
import {NavIcon} from './icons.jsx';
import {PERIODS} from './period.js';
import {identity, readScopeFor, session} from './data.js';
import './pc-ui.css';

// ---- 状态 ----
// 异步读取：账号切换或组件卸载后丢弃旧结果；reload 重新读取。
export function useAsync(loader, deps) {
  const owner = identity();
  const request = useMemo(() => ({owner, loader}), [owner, ...deps]);
  const [state, setState] = useState({request: null, loading: true, error: '', value: null});
  const serial = useRef(0), controller = useRef(null);
  const run = useCallback(() => {
    // 离开页面、换条件或重新读取时中止上一轮：逐客户聚合跟进这类长读取不再在后台继续发请求
    controller.current?.abort();
    const id = ++serial.current, current = controller.current = new AbortController();
    setState({request, loading: true, error: '', value: null});
    Promise.resolve().then(() => request.loader(current.signal)).then(value => {
      if (id === serial.current && request.owner === identity()) setState({request, loading: false, error: '', value});
    }, error => {
      if (id === serial.current && request.owner === identity()) setState({request, loading: false, error: error?.message || '读取失败，请重试', value: null});
    });
  }, [request]);
  useEffect(() => { run(); return () => { serial.current++; controller.current?.abort(); }; }, [run]);
  // render 发生在 effect 之前：范围或权限变化后的第一帧也不能沿用上一范围的数据。
  return {...(state.request === request ? state : {loading: true, error: '', value: null}), reload: run};
}

// 页面内的筛选偏好按账号保存在本标签页（sessionStorage），刷新后保留，换账号不串。
export function useSessionState(name, initial, entry = null) {
  const key = `sales-web:pc:${name}:${identity()}`;
  const read = () => {
    // 显式下钻条件优先于该页面上次的浏览偏好；本次进入后仍可正常改筛选。
    if (entry) return entry._pcFilterState?.[key] ?? initial;
    try { const saved = JSON.parse(sessionStorage.getItem(key) || 'null'); return saved ?? initial; } catch (_) { return initial; }
  };
  const [saved, setSaved] = useState(() => ({key, value: read()}));
  const value = saved.key === key ? saved.value : read();
  useEffect(() => { if (saved.key !== key) setSaved({key, value}); }, [key, saved.key, value]);
  useEffect(() => {
    // Runtime 返回原 page 对象；只在这一次下钻首次进入时覆盖历史偏好，
    // 从实体详情返回则恢复用户在该页后来修改的条件。
    if (entry) { entry._pcFilterState ||= {}; entry._pcFilterState[key] = value; }
  }, [entry, key, value]);
  const update = useCallback(next => setSaved(previous => {
    const current = previous.key === key ? previous.value : read();
    const resolved = typeof next === 'function' ? next(current) : next;
    try { sessionStorage.setItem(key, JSON.stringify(resolved)); } catch (_) {}
    return {key, value: resolved};
  }), [key]);
  return [value, update];
}

// ---- 拼音首字母匹配（不引入依赖）：用中文拼音排序规则定位声母，支持「xh」找到「星河」。 ----
const ZH_BOUNDARY = [...'阿八嚓哒妸发旮哈讥咔垃妈拏噢妑七呥扨它穵夕丫帀'];
const ZH_LETTER = [...'ABCDEFGHJKLMNOPQRSTWXYZ'];
const initialCache = new Map();
let collator = null;
function initialOf(char) {
  if (!/[一-鿿]/.test(char)) return char.toUpperCase();
  if (initialCache.has(char)) return initialCache.get(char);
  collator ||= new Intl.Collator('zh-Hans-CN', {sensitivity: 'base'});
  let letter = '';
  for (let i = ZH_BOUNDARY.length - 1; i >= 0; i--) if (collator.compare(char, ZH_BOUNDARY[i]) >= 0) { letter = ZH_LETTER[i]; break; }
  initialCache.set(char, letter);
  return letter;
}
export const pinyinInitials = value => [...String(value || '')].map(initialOf).join('');
export function fuzzyMatch(value, query) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return true;
  const target = String(value || '').toLowerCase();
  if (target.includes(q)) return true;
  if (/^[a-z]+$/i.test(q)) return pinyinInitials(value).toLowerCase().includes(q);
  return false;
}

// ---- 版式 ----
export function PageHead({title, subtitle, extra, children}) {
  return <header className="pc-page-head">
    <div className="pc-page-head-main">
      <h1>{title}</h1>
      {subtitle && <p>{subtitle}</p>}
    </div>
    {extra && <div className="pc-page-head-extra">{extra}</div>}
    {children}
  </header>;
}

export function Panel({title, extra, children, className = '', bodyClassName = '', id, note}) {
  return <section className={`pc-panel ${className}`} aria-label={typeof title === 'string' ? title : undefined} id={id}>
    {(title || extra) && <header className="pc-panel-head"><div className="pc-panel-title"><h2>{title}</h2>{note && <span>{note}</span>}</div>{extra && <div className="pc-panel-extra">{extra}</div>}</header>}
    <div className={`pc-panel-body ${bodyClassName}`}>{children}</div>
  </section>;
}

export function Kpi({label, value, unit, hint, onClick, tone, loading, sub, missingText = '—'}) {
  const content = <>
    <span className="pc-kpi-label">{label}</span>
    <span className="pc-kpi-value">{loading ? <Skeleton.Button active size="small" /> : <><b>{value ?? missingText}</b>{unit && <small>{unit}</small>}</>}</span>
    {(hint || sub) && <span className="pc-kpi-hint" title={typeof (sub || hint) === 'string' ? (sub || hint) : undefined}>{sub || hint}</span>}
  </>;
  return onClick
    ? <button type="button" className={`pc-kpi is-link${tone ? ' tone-' + tone : ''}`} onClick={onClick} aria-label={`${label} ${value ?? ''}${unit || ''}，查看详情`}>{content}<span className="pc-kpi-go" aria-hidden="true"><NavIcon name="arrowRight" /></span></button>
    : <div className={`pc-kpi${tone ? ' tone-' + tone : ''}`}>{content}</div>;
}

export function PeriodSwitch({value, onChange, options = PERIODS}) {
  return <Segmented className="pc-period" value={value} onChange={onChange} options={options.map(p => ({value: p.key, label: p.label}))} />;
}

export function Empty2({title = '暂无数据', description, action}) {
  return <div className="pc-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<><b>{title}</b>{description && <span>{description}</span>}</>} />{action}</div>;
}

export function LoadState({loading, error, onRetry, rows = 4, children}) {
  if (loading) return <div className="pc-loading"><Skeleton active paragraph={{rows}} title={false} /></div>;
  if (error) return <div className="pc-error" role="alert"><span>{error}</span>{onRetry && <Button size="small" onClick={onRetry}>重试</Button>}</div>;
  return children;
}

export function Tag2({tone = 'neutral', children, title}) {
  return <span className={`pc-tag tone-${tone}`} title={title}>{children}</span>;
}

export function SourceTag({source}) {
  if (source === 'projectbuddy') return <Tag2 tone="accent" title="由 ProjectBuddy 售前项目同步">ProjectBuddy</Tag2>;
  if (source === 'fde') return <Tag2 tone="teal" title="售前或 FDE 填写的跟进">售前 / FDE</Tag2>;
  if (source === 'unknown') return <Tag2>来源未记录</Tag2>;
  return null;
}

// ---- 范围选择：本人 / 团队 / 成员。团队与成员都可搜索（支持拼音首字母），默认本人团队。 ----
export function ScopePicker({scope, onChange, directory, compact, selfLabel = '个人', permission = directory?.permission || 'visit.read'}) {
  const s = session();
  const access = readScopeFor(permission, s);
  const companyAllowed = access.scopes.includes('workspace');
  const teamAllowed = companyAllowed || access.scopes.includes('teams');
  const [teamFilter, setTeamFilter] = useState(scope.kind === 'member' ? '' : scope.kind === 'team' ? (scope.teamId === 'all' ? '' : scope.teamId || '') : directory?.defaultTeamId || '');
  const teams = directory?.teams || [];
  const members = directory?.members || [];
  const memberOptions = useMemo(() => {
    const groups = new Map();
    for (const member of members) {
      if (teamFilter && !member.team_ids.includes(teamFilter)) continue;
      const teamId = member.team_ids[0] || 'none';
      const name = teams.find(t => t.id === teamId)?.name || member.teamLabel || '未分组';
      if (!groups.has(teamId)) groups.set(teamId, {label: name, options: []});
      groups.get(teamId).options.push({value: member.id, label: member.name, search: `${member.name} ${member.account_code || ''} ${name}`});
    }
    return [...groups.values()];
  }, [members, teams, teamFilter]);
  if (!access.allowed) return <span className="pc-scope-fixed">当前无查看权限</span>;
  if (!teamAllowed && !access.scopes.includes('assigned')) return <span className="pc-scope-fixed"><NavIcon name="user" />本人</span>;
  const kind = scope.kind;
  const teamOptions = [{value: 'all', label: companyAllowed ? '全公司' : '全部可见部门'}, ...teams.map(t => ({value: t.id, label: t.name}))];
  return <div className={`pc-scope${compact ? ' is-compact' : ''}`} role="group" aria-label="查看范围">
    <Segmented size="small" value={kind} onChange={next => {
      if (next === 'self') onChange({kind: 'self'});
      else if (next === 'team') onChange({kind: 'team', teamId: teamFilter || directory?.defaultTeamId || 'all'});
      else { const first = memberOptions[0]?.options?.[0]?.value; if (first) onChange({kind: 'member', memberId: first}); }
    }} options={[{value: 'self', label: selfLabel}, ...(teamAllowed ? [{value: 'team', label: '部门'}] : []), {value: 'member', label: '成员', disabled: !memberOptions.length}]} />
    {kind === 'team' && <Select size="small" className="pc-scope-select" value={scope.teamId || 'all'} options={teamOptions} showSearch popupMatchSelectWidth={false}
      filterOption={(input, option) => fuzzyMatch(option?.label, input)} aria-label="选择团队"
      onChange={teamId => { setTeamFilter(teamId === 'all' ? '' : teamId); onChange({kind: 'team', teamId}); }} />}
    {kind === 'member' && <>
      {teams.length > 1 && <Select size="small" className="pc-scope-select" value={teamFilter || 'all'} options={[{value: 'all', label: '全部可见部门'}, ...teams.map(t => ({value: t.id, label: t.name}))]}
        showSearch filterOption={(input, option) => fuzzyMatch(option?.label, input)} popupMatchSelectWidth={false} aria-label="先选团队" onChange={value => setTeamFilter(value === 'all' ? '' : value)} />}
      <Select size="small" className="pc-scope-select is-member" value={scope.memberId} options={memberOptions} showSearch placeholder="搜索姓名 / 拼音首字母"
        filterOption={(input, option) => fuzzyMatch(option?.search || option?.label, input)} popupMatchSelectWidth={260} listHeight={320} aria-label="选择成员"
        onChange={memberId => onChange({kind: 'member', memberId})} notFoundContent="没有匹配的成员" />
    </>}
  </div>;
}

// 与部门组件 SbLabeledSelect 同样的外观，多了搜索（姓名 / 团队名 / 拼音首字母），给人数多的团队、成员筛选用（需求清单第 13 条）
export function LabeledSearchSelect({label, value, options = [], onChange, placeholder = '全部', allowClear = true, disabled, width, className = ''}) {
  return <label className={`sb-lselect ${disabled ? 'sb-lselect-disabled' : ''} ${className}`} style={width ? {width} : undefined}>
    <span className="sb-lselect-label">{label}</span>
    <Select className="sb-lselect-control" variant="borderless" value={value ?? undefined} onChange={next => onChange?.(next)} options={options} placeholder={placeholder}
      allowClear={allowClear} disabled={disabled} popupMatchSelectWidth={false} showSearch listHeight={320}
      filterOption={(input, option) => fuzzyMatch(option?.label, input)} notFoundContent="没有匹配项" />
  </label>;
}

export function ProgressNote({done, total, label = '正在汇总跟进记录'}) {
  if (!total) return null;
  return <span className="pc-progress-note" role="status"><i style={{width: `${Math.round(done / total * 100)}%`}} />{label} {done}/{total}</span>;
}

export function Copyable({text: value, label = '复制', onCopied, allowCopy = () => true}) {
  return <Tooltip title="复制到剪贴板"><Button size="small" icon={<NavIcon name="copy" />} onClick={async () => {
    if (!allowCopy()) return;
    try { await navigator.clipboard.writeText(value); onCopied?.(true); } catch (_) { onCopied?.(false); }
  }}>{label}</Button></Tooltip>;
}

export function downloadText(filename, content, type = 'text/plain;charset=utf-8') {
  const blob = new Blob([content], {type});
  const url = URL.createObjectURL(blob);
  const link = Object.assign(document.createElement('a'), {href: url, download: filename});
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function toCsv(rows, columns) {
  const cell = value => {
    const textValue = value == null ? '' : String(value);
    return /[",\n\r]/.test(textValue) ? `"${textValue.replace(/"/g, '""')}"` : textValue;
  };
  return '﻿' + [columns.map(c => cell(c.title)).join(','), ...rows.map(row => columns.map(c => cell(c.value(row))).join(','))].join('\r\n');
}
