import FilterBar from './pc/FilterBar.jsx';
import React, {useEffect, useMemo, useState} from 'react';
import {App, Avatar, Button, Segmented, Table, Tooltip} from 'antd';
import {NavIcon} from './pc/icons.jsx';
import {FollowupDrawer, FollowupItem, initials} from './pc/followups.jsx';
import {Copyable, Empty2, Kpi, LoadState, PageHead, Panel, ProgressNote, ScopePicker, Tag2, downloadText, useAsync, useSessionState} from './pc/ui.jsx';
import {amountWan, byMember, clientExportAllowed, collectFollowups, defaultScope, identity, isFdeRole, loadActiveOpportunities, loadCompletedTasks, loadDirectory, loadNewOpportunities,
  open, periodRange, relativeDay, scopeKey, scopeLabel, scopeMembers, session, summarize, todayKey} from './pc/data.js';
import {composeWorkReport} from './pc/report.js';
import './pc/work-report.css';
import {readScope, workspacePath} from './pc/workspace-selection.js';
import {loadWorkspaceRisks, loadWorkspaceTasks} from './pc/workspace-data.js';

const REPORT_PERIODS = Object.freeze([
  {value: 'day', label: '日报 · 今日'},
  {value: 'week', label: '周报 · 本周'},
  {value: 'biweek', label: '双周报 · 上周＋本周'},
  {value: 'month', label: '月报 · 本月'},
  {value: 'quarter', label: '季度报 · 本季度'},
]);

// 工作报告：日会看今日、周会看本周。实时汇总系统里已录入的跟进、商机、任务和风险，按人展开；
// 可复制 / 下载报告正文，会议模式逐人翻页。不做 AI 推断，不生成没有来源的结论。
function MemberCard({row, onSelect, onFocus}) {
  return <article className={`pc-wr-member${row.count ? '' : ' is-idle'}`}>
    <header>
      <Avatar size={32}>{initials(row.name)}</Avatar>
      <div><b>{row.name}{row.role && isFdeRole(row.role) && <Tag2 tone="teal">FDE</Tag2>}</b><small>{row.team || '—'}</small></div>
      <div className="pc-wr-member-stats">{row.count ? <><span><b>{row.count}</b>跟进</span><span><b>{row.customerCount}</b>客户</span><span><b>{row.opportunityCount}</b>商机</span></> : <Tag2 tone="warning">本期未跟进</Tag2>}</div>
      {onFocus && row.count > 0 && <Tooltip title="只看这位成员"><Button type="text" size="small" icon={<NavIcon name="user" />} onClick={() => onFocus(row.id)} aria-label={`只看 ${row.name}`} /></Tooltip>}
    </header>
    {row.count > 0 && <div className="pc-wr-member-body">{row.visits.slice(0, 4).map(visit => <FollowupItem key={visit.id} visit={visit} showPerson={false} onSelect={onSelect} dense />)}
      {row.visits.length > 4 && <p className="pc-muted">还有 {row.visits.length - 4} 条，在「跟进记录 · 按人员」里查看</p>}</div>}
  </article>;
}

function MeetingMode({open: visible, onClose, slides}) {
  const [index, setIndex] = useState(0);
  useEffect(() => { if (visible) setIndex(0); }, [visible]);
  useEffect(() => {
    if (!visible) return undefined;
    const key = event => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'ArrowRight' || event.key === 'PageDown' || event.key === ' ') { event.preventDefault(); setIndex(i => Math.min(slides.length - 1, i + 1)); }
      if (event.key === 'ArrowLeft' || event.key === 'PageUp') { event.preventDefault(); setIndex(i => Math.max(0, i - 1)); }
    };
    document.addEventListener('keydown', key, true);
    return () => document.removeEventListener('keydown', key, true);
  }, [visible, slides.length]);
  if (!visible || !slides.length) return null;
  const slide = slides[Math.min(index, slides.length - 1)];
  return <div className="pc-wr-meeting" role="dialog" aria-modal="true" aria-label="会议模式">
    <header>
      <b>{slide.title}</b><span>{index + 1} / {slides.length}</span>
      <div className="pc-wr-meeting-nav">
        {slides.map((item, i) => <button type="button" key={item.key} className={i === index ? 'is-active' : ''} onClick={() => setIndex(i)} title={item.title}>{item.short}</button>)}
      </div>
      <Button onClick={() => setIndex(i => Math.max(0, i - 1))} disabled={index === 0}>上一位</Button>
      <Button type="primary" onClick={() => setIndex(i => Math.min(slides.length - 1, i + 1))} disabled={index === slides.length - 1}>下一位</Button>
      <Button type="text" onClick={onClose} aria-label="退出会议模式">退出 Esc</Button>
    </header>
    <div className="pc-wr-meeting-body">{slide.render()}</div>
  </div>;
}

export default function WorkReport({page}) {
  const s = session();
  const owner = identity();
  const {message} = App.useApp();
  const options = page?.options || {};
  const requestedKind = options.period || (page?.route === 'pages/weekly-report/index' ? 'week' : 'day');
  const kind = REPORT_PERIODS.some(item => item.value === requestedKind) ? requestedKind : 'day';
  const [savedScope, setScope] = useSessionState('report-scope', readScope(options), options.from === 'workspace' ? page : null);
  const [focus, setFocus] = useState(null);
  const [selected, setSelected] = useState(null);
  const [meeting, setMeeting] = useState(false);
  const [progress, setProgress] = useState(null);
  const today = todayKey();
  const range = useMemo(() => periodRange(kind), [kind, today]);
  const periodLabel = REPORT_PERIODS.find(item => item.value === kind)?.label || '工作报告';
  const directory = useAsync(() => loadDirectory(), [identity()]);
  const scope = savedScope || defaultScope(s, directory.value);
  const key = scopeKey(scope);
  const followups = useAsync(async signal => {
    if (directory.error) throw Error('人员目录加载失败：' + directory.error);
    if (!directory.value) return null;
    setProgress(null);
    return collectFollowups({range, scope, directory: directory.value, signal, onProgress: (done, total) => setProgress({done, total})});
  }, [directory.value, directory.error, range.start, range.end, key]);
  const visits = followups.value?.items || [];
  const tasks = useAsync(() => loadWorkspaceTasks(scope), [key, today, identity()]);
  const completed = useAsync(() => loadCompletedTasks(range, scope), [key, range.start, range.end, identity()]);
  const created = useAsync(() => loadNewOpportunities(range, scope), [key, range.start, range.end, identity()]);
  const risks = useAsync(() => loadWorkspaceRisks(scope), [key, identity()]);
  const opportunities = useAsync(() => followups.value ? loadActiveOpportunities(range, scope, visits) : null, [followups.value]);
  const members = directory.value ? scopeMembers(scope, directory.value, s) : [];
  const team = scope.kind === 'team';
  const memberRows = useMemo(() => byMember(visits, team ? members : members.filter(m => m.id === (scope.kind === 'member' ? String(scope.memberId) : String(s?.userId)))), [visits, members, key]);
  const shownRows = focus ? memberRows.filter(row => row.id === focus) : memberRows;
  const summary = summarize(visits, team ? members : memberRows.filter(row => !row.outside));
  const loading = directory.loading || followups.loading || (!followups.value && !followups.error && !directory.error);
  const readError = directory.error || followups.error;
  const reportReady = !loading && !readError && [tasks, risks, opportunities, created, completed].every(result => !result.loading && !result.error && result.value && !result.value.error);
  const label = scopeLabel(scope, directory.value, s);
  const generatedAt = new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Shanghai'});
  const report = useMemo(() => !reportReady ? null : composeWorkReport({kind, range, scopeName: label === '本人' ? (s?.userName || '我') + ' · ' : label + ' · ', author: s?.userName, summary, members: memberRows,
    opportunities: opportunities.value?.items || [], newOpportunities: created.value?.items || [], completedTasks: completed.value?.items || [], openTasks: tasks.value, risks: risks.value?.items || [], riskTotal: risks.value?.total, generatedAt: `${range.today} ${generatedAt}`}),
  [reportReady, kind, range, visits, opportunities.value, created.value, completed.value, tasks.value, risks.value, label, s?.userName]);
  const idle = memberRows.filter(row => !row.count && !row.outside);
  const opportunityRows = [...(opportunities.value?.items || [])].sort((a, b) => b.amount - a.amount);
  const nextActions = memberRows.flatMap(row => row.visits.filter(v => v.nextAction).map(v => ({...v, owner: row.name})));

  const kpis = <section className="pc-kpis pc-wr-kpis" aria-label="概览">
    <Kpi label="跟进记录" value={loading || readError ? null : summary.visits} unit="条" loading={loading} sub={readError ? '统计暂未取得' : loading ? '读取中' : team ? `人均 ${summary.perCapita ?? '—'} 条` : `下一步已写 ${summary.withNext} 条`} tone="primary" />
    <Kpi label="覆盖客户" value={loading || readError ? null : summary.customers} unit="家" loading={loading} sub="本期有跟进" />
    <Kpi label="推进商机" value={loading || readError ? null : summary.opportunities} unit="个" loading={loading} sub="有跟进记录的商机" />
    <Kpi label="新增商机" value={created.loading ? null : (created.value?.error || created.error ? null : created.value?.items.length) ?? '—'} unit="个" loading={created.loading} sub={created.value?.partial ? '已读取前 500 条商机' : '按创建时间'} />
    <Kpi label="完成任务" value={completed.loading ? null : (completed.value?.error || completed.error ? null : completed.value?.items.length) ?? '—'} unit="项" loading={completed.loading} sub={tasks.value && !tasks.error ? `待办 ${tasks.value.total} · 逾期 ${tasks.value.counts.overdue}` : '待办统计暂未取得'} tone={tasks.value?.counts.overdue ? 'danger' : undefined} />
    {team ? <Kpi label="活跃成员" value={loading || readError ? null : `${summary.activeMembers}/${summary.members}`} unit="人" loading={loading} sub={readError ? '统计暂未取得' : loading ? '读取中' : idle.length ? `未跟进：${idle.slice(0, 3).map(r => r.name).join('、')}${idle.length > 3 ? ' 等' : ''}` : '全员有跟进'} tone={idle.length ? 'warning' : 'success'} />
      : <Kpi label="风险预警" value={risks.error ? null : risks.value?.total} unit="项" loading={risks.loading} sub={risks.error ? '统计暂未取得' : '当前未关闭'} tone={risks.value?.total ? 'warning' : undefined} />}
  </section>;

  const opportunityTable = <Table size="small" rowKey="id" dataSource={opportunityRows} scroll={{x: 1000}} tableLayout="fixed" pagination={opportunityRows.length > 10 ? {pageSize: 10, size: 'small'} : false}
    locale={{emptyText: '本期没有有跟进记录的商机'}}
    columns={[
      {title: '商机 / 客户', key: 'name', width: 190, render: (_, row) => <div className="pc-ws-opp"><button type="button" className="pc-link" onClick={() => open.opportunity(row.customerId, row.id)}>{row.name}</button><small>{row.customerName}</small></div>},
      {title: '阶段', dataIndex: 'stage', key: 'stage', width: 110},
      {title: '金额（万元）', dataIndex: 'amount', key: 'amount', width: 112, align: 'right', render: value => <span className="pc-num">{amountWan(value)}</span>},
      {title: '预计关单', dataIndex: 'close', key: 'close', width: 112, render: value => value || <span className="pc-muted">未填写</span>},
      {title: '本期最新进展', key: 'last', render: (_, row) => row.lastVisit ? <span className="pc-wr-progress"><b>{relativeDay(row.lastVisit.date)} · {row.lastVisit.recorderName}</b><span className="pc-clamp-2">{row.lastVisit.content}</span></span> : <span className="pc-muted">—</span>},
      {title: '下一步', key: 'next', width: 240, render: (_, row) => <span className="pc-clamp-2">{row.lastVisit?.nextAction || <span className="pc-muted">未填写</span>}</span>},
    ]} />;

  const slides = [
    {key: 'overview', short: '概览', title: `${label} · ${periodLabel.split(' · ')[0]}概览（${range.title}）`, render: () => <div className="pc-wr-slide">{kpis}
      {idle.length > 0 && <Panel title="本期未跟进的成员"><p className="pc-wr-idle">{idle.map(row => row.name).join('、')}</p></Panel>}
      <Panel title="风险预警">{(risks.value?.items || []).length ? <ul className="pc-wr-list">{risks.value.items.map(r => <li key={r.id}><b>{r.title}</b><span>{[r.customerName, r.opportunityName].filter(Boolean).join(' · ')}</span></li>)}</ul> : <p className="pc-muted">没有待处理的风险</p>}</Panel>
    </div>},
    ...memberRows.filter(row => row.count || !row.outside).map(row => ({key: row.id, short: row.name.slice(0, 3), title: `${row.name} · ${row.count} 条跟进`, render: () => <div className="pc-wr-slide">
      <MemberCard row={{...row, visits: row.visits}} onSelect={setSelected} />
      {row.visits.length > 4 && <Panel title="其余跟进">{row.visits.slice(4).map(v => <FollowupItem key={v.id} visit={v} showPerson={false} dense />)}</Panel>}
    </div>})),
    {key: 'pipeline', short: '商机', title: '重点商机进展', render: () => <div className="pc-wr-slide"><Panel title="重点商机进展" bodyClassName="is-flush">{opportunityTable}</Panel></div>},
  ];

  return <div className="pc-page pc-list-page pc-report">
    <PageHead title={periodLabel} subtitle={`${range.title} · ${label} · 实时汇总，截至 ${generatedAt}`}
      />
    {followups.error && <div className="pc-error" role="alert"><span>{followups.error}</span><span>{scope.kind !== 'self' && <Button size="small" onClick={() => setScope({kind: 'self'})}>切回个人视角</Button>} <Button size="small" onClick={followups.reload}>重试</Button></span></div>}
    {kpis}
    {directory.error && <div className="pc-error" role="alert"><span>人员目录加载失败：{directory.error}</span><Button size="small" onClick={directory.reload}>重新加载人员目录</Button></div>}
    <FilterBar className="pc-page-filters" label="时间与查看范围" moreLabel="更多条件">
      <Segmented data-toolbar-width={288} value={kind} onChange={value => open.replace(workspacePath('work-report', {kind: value}, scope))} options={REPORT_PERIODS.map(option => ({...option, label: <span title={option.label}>{option.label.split(' · ')[0]}</span>}))} />
      {directory.value && <ScopePicker data-toolbar-width={scope.kind === 'member' ? 530 : scope.kind === 'team' ? 340 : 176} scope={scope} directory={directory.value} onChange={next => { setFocus(null); setScope(next); }} />}
      <Button data-toolbar-width={110} icon={<NavIcon name="present" />} disabled={!reportReady} onClick={() => setMeeting(true)}>会议模式</Button>
      {report && clientExportAllowed('report.export') && <Copyable data-toolbar-width={110} text={report.text} allowCopy={() => owner === identity() && clientExportAllowed('report.export')} label="复制报告" onCopied={ok => ok ? message.success('报告正文已复制，可直接粘贴到飞书 / 企业微信') : message.error('浏览器未允许复制，请改用下载')} />}
      {report && clientExportAllowed('report.export') && <Button data-toolbar-width={110} icon={<NavIcon name="download" />} onClick={() => owner === identity() && clientExportAllowed('report.export') && downloadText(`${report.title}.md`, report.text, 'text/markdown;charset=utf-8')}>下载</Button>}
    </FilterBar>
    <div className="pc-results-scroll pc-report-results" role="region" aria-label="报告明细" tabIndex={0}>
    <div className="pc-wr-grid">
      <div className="pc-wr-main">
      <Panel title={team ? '成员进展' : '我的进展'} note={focus ? '已筛选一位成员' : team ? '按跟进条数排序，未跟进的成员排在后面' : shownRows.length > 1 ? '含售前 / FDE 在我负责客户上的跟进' : ''}
        extra={<>{loading && progress?.total > 1 && <ProgressNote done={progress.done} total={progress.total} label="正在汇总客户跟进" />}{focus && <Button size="small" onClick={() => setFocus(null)}>看全部成员</Button>}
          <Button type="link" size="small" onClick={() => open.route(workspacePath('visit-records', range, scope))}>跟进记录 →</Button></>}>
        <LoadState loading={loading} error={readError} onRetry={followups.reload} rows={6}>
          {shownRows.some(row => row.count > 0) && <div className="pc-wr-members">{shownRows.filter(row => row.count > 0).map(row => <MemberCard key={row.id} row={row} onSelect={setSelected} onFocus={team ? setFocus : null} />)}</div>}
          {shownRows.some(row => !row.count) && <div className="pc-wr-idle-members" aria-label="本期未跟进成员">{shownRows.filter(row => !row.count).map(row => <MemberCard key={row.id} row={row} />)}</div>}
          {!shownRows.length && <Empty2 title="本期还没有跟进记录" />}
        </LoadState>
      </Panel>
      <Panel title="重点商机进展" note="本期有跟进记录的商机，按金额排序" bodyClassName="is-flush">
        <LoadState loading={loading || opportunities.loading} error={readError || opportunities.error || opportunities.value?.error} onRetry={opportunities.reload} rows={3}>{opportunityTable}</LoadState>
      </Panel>
      </div>
      <aside className="pc-wr-side">
        <Panel title="下一步与待协调" note={`${nextActions.length} 项`}>
          <LoadState loading={loading} error={readError} onRetry={followups.reload} rows={4}>
            {nextActions.length ? <ul className="pc-wr-list">{nextActions.slice(0, 12).map(v => <li key={v.id}><b>{v.owner} · {v.customerName}</b><span>{v.nextAction}</span></li>)}</ul> : <p className="pc-muted">跟进记录里还没有填写下一步</p>}
            {tasks.error && <LoadState error={tasks.error} onRetry={tasks.reload} />}
            {!tasks.error && tasks.value?.overdue?.length > 0 && <div className="pc-wr-overdue"><b>逾期任务 {tasks.value.counts.overdue} 项</b>{tasks.value.overdue.slice(0, 5).map(t => <button type="button" key={t.id} className="pc-link" onClick={() => open.task(t.id)}>{t.title}{t.owner ? ` · ${t.owner}` : ''}</button>)}</div>}
          </LoadState>
        </Panel>
        <Panel title="新增商机" note={created.value && !created.value.error ? `${created.value.items.length} 个` : ''}>
          <LoadState loading={created.loading} error={created.error || created.value?.error} onRetry={created.reload} rows={3}>
            {(created.value?.items || []).length ? <ul className="pc-wr-list">{created.value.items.map(o => <li key={o.id}><button type="button" className="pc-link" onClick={() => open.opportunity(o.customerId, o.id)}>{o.customerName} · {o.name}</button><span>{amountWan(o.amount)} 万元 · {o.stage} · {o.owner}</span></li>)}</ul> : <p className="pc-muted">本期没有新增商机</p>}
          </LoadState>
        </Panel>
        <Panel title="完成任务" note={completed.value && !completed.value.error ? `${completed.value.items.length} 项` : ''}>
          <LoadState loading={completed.loading} error={completed.error || completed.value?.error} onRetry={completed.reload} rows={3}>
            {(completed.value?.items || []).length ? <ul className="pc-wr-list">{completed.value.items.slice(0, 8).map(t => <li key={t.id}><button type="button" className="pc-link" onClick={() => open.task(t.id)}>{t.title}</button><span>{[t.owner, t.customerName].filter(Boolean).join(' · ')}</span></li>)}</ul> : <p className="pc-muted">本期还没有完成的任务</p>}
          </LoadState>
        </Panel>
        <Panel title="风险预警" note={!risks.error && risks.value ? `${risks.value.total} 项${risks.value.hasMore ? ' · 展示前50项' : ''}` : ''}>
          <LoadState loading={risks.loading} error={risks.error} onRetry={risks.reload} rows={2}>
            {(risks.value?.items || []).length ? <ul className="pc-wr-list">{risks.value.items.map(r => <li key={r.id}><button type="button" className="pc-link" onClick={() => open.risk(r.id)}>{r.title}</button><span>{[r.customerName, r.opportunityName].filter(Boolean).join(' · ')}</span></li>)}</ul> : <p className="pc-muted">没有待处理的风险</p>}
          </LoadState>
        </Panel>
      </aside>
    </div>
    <p className="pc-records-caliber">口径：跟进按跟进日期、任务按完成时间、商机按创建时间统计；报告正文只排列系统记录，不做 AI 推断。</p>
    </div>
    <MeetingMode open={meeting && reportReady} onClose={() => setMeeting(false)} slides={slides} />
    <FollowupDrawer visit={selected} onClose={() => setSelected(null)} />
  </div>;
}
