import FilterBar from './pc/FilterBar.jsx';
import {useLocalTablePagination} from './pc/list-pagination.js';
import React, {useMemo, useState} from 'react';
import {Avatar, Button, Segmented, Table, Tooltip} from 'antd';
import {ActivityFeed} from './Home.jsx';
import {NavIcon} from './pc/icons.jsx';
import {FollowupDrawer, FollowupStream, initials} from './pc/followups.jsx';
import {Kpi, LoadState, PageHead, Panel, PeriodSwitch, ProgressNote, ScopePicker, Tag2, useAsync, useSessionState} from './pc/ui.jsx';
import {amountWan, byMember, can, collectFollowups, defaultScope, identity, isFdeRole, loadActiveOpportunities, loadDirectory,
  open, periodRange, relativeDay, scopeKey, scopeLabel, scopeMembers, session, summarize, todayKey} from './pc/data.js';
import './pc/workspace.css';
import {workspacePath} from './pc/workspace-selection.js';
import {loadWorkspaceRisks, loadWorkspaceTasks} from './pc/workspace-data.js';
import WorkspaceItemsDrawer from './pc/WorkspaceItemsDrawer.jsx';
import {useLiveTaskRead} from './pc/useLiveTaskRead.js';

// PC 工作台（原「总览」）：销售与销售 leader 当天要关注的事一屏看完。
// 默认今日，可切本周、本月、本季度；有团队权限的人可切个人 / 团队 / 成员。
// 数字全部来自原接口的已授权数据：跟进记录、任务、风险、商机；原总览的业务动态保留在页面下方。
const QUICK = [
  {key: 'visit', label: '记录拜访', icon: 'mic', path: 'pages/visit-entry/index', capability: ['visit.create', 'visit.company_opportunity_follow_up', 'visit.company_customer_follow_up']},
  {key: 'opportunity', label: '新增商机', icon: 'opportunity', path: 'pages/opportunity-create/index', capability: 'opportunity.create'},
  {key: 'task', label: '创建任务', icon: 'task', path: 'pages/management-task-create/index', capability: 'task.create'},
  {key: 'claim', label: '客户认领', icon: 'customer', path: 'pages/customer-claim/index', capability: 'customer.claim'},
  {key: 'report', label: '今日日报', icon: 'report', path: 'pages/work-report/index?period=day'},
  {key: 'knowledge', label: '方案与定价 · 暂不能使用', icon: 'book', disabled: true},
];

function recordsPath(range, scope, extra = {}) {
  return workspacePath('visit-records', range, scope, extra);
}

function TaskList({tasks}) {
  const today = todayKey();
  const rows = tasks.items.slice(0, 7);
  if (!rows.length) return <p className="pc-fu-empty">没有待处理的任务</p>;
  return <ul className="pc-ws-tasks">
    {rows.map(task => {
      const overdue = task.overdue, todayDue = task.dueDay === today;
      return <li key={task.id}><button type="button" onClick={() => open.task(task.id)}>
        <span className={`pc-ws-task-dot${overdue ? ' is-overdue' : todayDue ? ' is-today' : ''}`} aria-hidden="true" />
        <span className="pc-ws-task-main"><b>{task.title}</b><small>{[task.customerName, task.owner && task.owner !== session()?.userName ? task.owner : ''].filter(Boolean).join(' · ') || '日常工作'}</small><small>{task.statusLabel}{task.showProgress?` · ${task.progressView.label} · ${task.progressView.countLabel}`:''}{task.responsibilityLabel?` · ${task.responsibilityLabel}`:''}</small></span>
        <span className={`pc-ws-task-due${overdue ? ' is-overdue' : ''}`}>{overdue ? '已逾期' : task.dueDay ? relativeDay(task.dueDay) : '未设截止'}</span>
      </button></li>;
    })}
  </ul>;
}

function RiskList({risks}) {
  if (!risks.length) return <p className="pc-fu-empty">没有待处理的风险</p>;
  return <ul className="pc-ws-risks">{risks.slice(0, 5).map(risk => <li key={risk.id}><button type="button" onClick={() => open.risk(risk.id)}>
    <NavIcon name="alert" /><span className="pc-ws-risk-main"><b>{risk.title}</b><small>{[risk.customerName, risk.opportunityName].filter(Boolean).join(' · ') || '风险预警'}</small></span>
  </button></li>)}</ul>;
}

function TeamTable({rows, onMember, range}) {
  const pagination = useLocalTablePagination('workspace-members', rows.map(row => row.id).join(','));
  const columns = [
    {title: '成员', key: 'name', render: (_, row) => <span className="pc-ws-member"><Avatar size={22}>{initials(row.name)}</Avatar><span><b>{row.name}</b>{row.team && <small>{row.team}</small>}</span>{row.role && isFdeRole(row.role) && <Tag2 tone="teal">FDE</Tag2>}</span>},
    {title: '跟进', dataIndex: 'count', key: 'count', width: 72, align: 'right', sorter: (a, b) => a.count - b.count, defaultSortOrder: 'descend',
      render: value => value ? <b className="pc-num">{value}</b> : <Tag2 tone="warning">{range.kind === 'day' ? '今日未跟进' : '本期未跟进'}</Tag2>},
    {title: '客户', dataIndex: 'customerCount', key: 'customers', width: 64, align: 'right', render: value => <span className="pc-num">{value}</span>},
    {title: '商机', dataIndex: 'opportunityCount', key: 'opportunities', width: 64, align: 'right', render: value => <span className="pc-num">{value}</span>},
    {title: '最近跟进', dataIndex: 'lastDate', key: 'last', width: 100, render: value => value ? relativeDay(value) : <span className="pc-muted">—</span>},
  ];
  return <Table size="small" rowKey="id" columns={columns} dataSource={rows} pagination={pagination}
    onRow={row => ({onClick: () => !row.outside && onMember(row.id), style: {cursor: row.outside ? 'default' : 'pointer'}})} />;
}

function OpportunityTable({items, loading, error, reload}) {
  const pagination = useLocalTablePagination('workspace-opportunities', items.map(row => row.id).join(','));
  const columns = [
    {title: '商机 / 客户', key: 'name', render: (_, row) => <div className="pc-ws-opp"><button type="button" className="pc-link" onClick={() => open.opportunity(row.customerId, row.id)}>{row.name}</button><small>{row.customerName}</small></div>},
    {title: '阶段', key: 'stage', width: 132, render: (_, row) => <span>{row.stage}{Number.isFinite(Number(row.probability)) && row.probability !== null ? <small className="pc-muted"> · {row.probability}%</small> : null}</span>},
    {title: '金额（万元）', dataIndex: 'amount', key: 'amount', width: 116, align: 'right', render: value => <span className="pc-num">{amountWan(value)}</span>, sorter: (a, b) => a.amount - b.amount},
    {title: '预计关单', dataIndex: 'close', key: 'close', width: 112, render: value => value || <span className="pc-muted">未填写</span>},
    {title: '最近跟进', key: 'last', width: 130, render: (_, row) => row.lastVisit ? <Tooltip title={row.lastVisit.content}><span>{relativeDay(row.lastVisit.date)} · {row.lastVisit.recorderName}</span></Tooltip> : <span className="pc-muted">—</span>},
    // 销售在这里发起售前协同；售前 / FDE 自己看时同一入口叫「创建任务」
    ...(can('task.create') ? [{title: '', key: 'presales', width: 96, render: (_, row) => <Tooltip title={isFdeRole(session()?.role) ? '带着客户与商机创建任务' : '带着客户与商机创建任务，接收人可选 FDE'}><Button type="link" size="small" onClick={() => open.presales(row.customerId, row.id)}>{isFdeRole(session()?.role) ? '创建任务' : '发起售前'}</Button></Tooltip>}] : []),
  ];
  return <LoadState loading={loading} error={error} onRetry={reload} rows={3}>
    {items.length ? <Table size="small" rowKey="id" columns={columns} dataSource={[...items].sort((a, b) => b.amount - a.amount)} pagination={pagination} />
      : <p className="pc-fu-empty">本期还没有有跟进记录的商机</p>}
  </LoadState>;
}

export default function Workspace({page, data, invoke}) {
  const s = session();
  const [period, setPeriod] = useSessionState('workspace-period', 'day');
  const [savedScope, setScope] = useSessionState('workspace-scope', null);
  const [filter, setFilter] = useState('all');
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useSessionState('workspace-detail', null);
  const [progress, setProgress] = useState(null);
  const today = todayKey();
  const range = useMemo(() => periodRange(period), [period, today]);
  const directory = useAsync(() => loadDirectory(), [identity()]);
  const scope = savedScope || defaultScope(s, directory.value);
  const key = scopeKey(scope);
  const followups = useAsync(async signal => {
    if (directory.error) throw Error('人员目录加载失败：' + directory.error);
    if (!directory.value) return null;
    setProgress(null);
    return collectFollowups({range, scope, directory: directory.value, signal, onProgress: (done, total) => setProgress({done, total})});
  }, [directory.value, directory.error, range.start, range.end, key]);
  const tasks = useLiveTaskRead(() => loadWorkspaceTasks(scope), [key, today, identity()]);
  const risks = useAsync(() => loadWorkspaceRisks(scope), [key, identity()]);
  const visits = followups.value?.items || [];
  const opportunities = useAsync(() => followups.value ? loadActiveOpportunities(range, scope, visits) : null, [followups.value]);
  const members = directory.value ? scopeMembers(scope, directory.value, s) : [];
  const summary = summarize(visits, scope.kind === 'team' ? members : members.filter(m => m.id === (scope.kind === 'member' ? String(scope.memberId) : String(s?.userId))));
  const teamRows = scope.kind === 'team' && directory.value ? byMember(visits, members) : [];
  const shown = filter === 'all' ? visits : visits.filter(v => filter === 'sales' ? v.source === 'sales' : v.source !== 'sales');
  const loadingFollowups = directory.loading || followups.loading || (!followups.value && !followups.error && !directory.error);
  const team = scope.kind === 'team';
  const label = scopeLabel(scope, directory.value, s);
  const taskCounts = !tasks.error && tasks.value?.counts;
  const quick = QUICK.filter(item => !item.capability || can(item.capability));
  const days = new Set(visits.map(v => v.date)).size;
  const followupError = followups.error && <div className="pc-error" role="alert"><span>{followups.error}</span>
    <span>{scope.kind !== 'self' && <Button size="small" onClick={() => setScope({kind: 'self'})}>切回个人视角</Button>} <Button size="small" onClick={followups.reload}>重试</Button></span></div>;

  if (data.accessBlocked) return <Panel title="工作台"><p>{data.accessMessage}</p></Panel>;
  return <div className="pc-page pc-list-page pc-workspace">
    <PageHead title={`${data.greeting || '你好'}${s?.userName ? `，${s.userName}` : ''}`}
      subtitle={`${range.title} · ${label}${s?.roleName ? ' · ' + s.roleName : ''}`}
      />

    <section className="pc-kpis" aria-label="关键指标">
      <Kpi label={`${range.name}跟进记录`} value={loadingFollowups || followups.error || directory.error ? null : summary.visits} unit="条" loading={loadingFollowups}
        sub={directory.error || followups.error ? '暂未取得统计' : loadingFollowups ? '读取中' : team ? `人均 ${summary.perCapita ?? '—'} 条 · 含售前 ${summary.presales} 条` : `下一步已写 ${summary.withNext} 条`} onClick={() => open.route(recordsPath(range, scope))} tone="primary" />
      <Kpi label="活跃客户" value={loadingFollowups || followups.error || directory.error ? null : summary.customers} unit="家" loading={loadingFollowups} sub="本期有跟进的客户" onClick={() => open.route(recordsPath(range, scope, {view: 'customer', linked: '1'}))} />
      <Kpi label="活跃商机" value={loadingFollowups || followups.error || directory.error ? null : summary.opportunities} unit="个" loading={loadingFollowups} sub="本期跟进关联的商机" onClick={() => open.route(recordsPath(range, scope, {view: 'opportunity', linked: '1'}))} />
      {team ? <Kpi label="活跃人员" value={loadingFollowups || followups.error || directory.error ? null : `${summary.activeMembers}/${summary.members}`} unit="人" loading={loadingFollowups} sub="本期有跟进记录的成员" tone={summary.members && summary.activeMembers < summary.members ? 'warning' : undefined} onClick={() => open.route(recordsPath(range, scope, {view: 'person'}))} />
        : <Kpi label="跟进天数" value={loadingFollowups || followups.error || directory.error ? null : days} unit="天" loading={loadingFollowups} sub={`本期已过 ${range.elapsed} 天`} onClick={() => open.route(recordsPath(range, scope))} />}
      <Kpi label="待办任务" value={tasks.error ? null : tasks.value?.total} unit="项" loading={tasks.loading}
        sub={tasks.error ? '暂未取得统计' : taskCounts ? `今日到期 ${taskCounts.todayPending} · 已逾期 ${taskCounts.overdue}` : '读取中'} tone={taskCounts?.overdue ? 'danger' : undefined} onClick={() => setDetail('all_pending')} />
      <Kpi label="风险预警" value={risks.error ? null : risks.value?.total} unit="项" loading={risks.loading} sub={risks.error ? '暂未取得统计' : '当前未关闭 · 按负责人范围'} tone={risks.value?.total ? 'warning' : undefined} onClick={() => setDetail('risks')} />
    </section>
    {directory.error && <div className="pc-error" role="alert"><span>人员目录加载失败：{directory.error}</span><Button size="small" onClick={directory.reload}>重新加载人员目录</Button></div>}
    <FilterBar className="pc-page-filters" label="时间与查看范围" moreLabel="更多条件">
      <PeriodSwitch data-toolbar-width={260} value={range.kind} onChange={value => setPeriod(value)} />
      {directory.value && <ScopePicker data-toolbar-width={scope.kind === 'member' ? 530 : scope.kind === 'team' ? 340 : 176} scope={scope} directory={directory.value} onChange={next => setScope(next)} />}
    </FilterBar>

    <div className="pc-ws-grid pc-results-scroll" role="region" aria-label="工作台明细" tabIndex={0}>
      <div className="pc-ws-main">
        <Panel title="跟进动态" note={range.title}
          extra={<>
            {loadingFollowups && progress?.total > 1 && <ProgressNote done={progress.done} total={progress.total} label="正在汇总客户跟进" />}
            <Segmented size="small" value={filter} onChange={setFilter} options={[{value: 'all', label: '全部'}, {value: 'sales', label: '销售'}, {value: 'presales', label: '售前 / FDE'}]} />
            <Button type="link" size="small" onClick={() => open.route(recordsPath(range, scope))}>全部记录 →</Button>
          </>}>
          {followupError || <LoadState loading={loadingFollowups} rows={5}>
            <FollowupStream visits={shown} limit={8} onSelect={setSelected} showPerson={scope.kind !== 'self' || shown.some(v => v.recorderId !== String(s?.userId))}
              emptyText={`${range.name}还没有跟进记录。拜访后在小程序或这里「记录拜访」即可。`} />
            {shown.length > 8 && <p className="pc-ws-more">还有 {shown.length - 8} 条，<button type="button" className="pc-link" onClick={() => open.route(recordsPath(range, scope))}>在跟进记录里查看</button></p>}
          </LoadState>}
        </Panel>

        {team && <Panel title="团队跟进" note={followups.value ? `人均 ${summary.perCapita ?? '—'} 条 · 活跃 ${summary.activeMembers}/${summary.members} 人` : ''}
          extra={<Button type="link" size="small" onClick={() => open.route(workspacePath('work-report', range, scope))}>看工作报告 →</Button>}>
          <LoadState loading={loadingFollowups} error={directory.error || followups.error} onRetry={followups.reload} rows={4}>
            <TeamTable rows={teamRows} range={range} onMember={memberId => setScope({kind: 'member', memberId})} />
            <p className="pc-ws-foot">按记录人统计；人均 = 团队成员的跟进条数 ÷ 团队人数，不按商机数排名。点成员看个人工作台。</p>
          </LoadState>
        </Panel>}

        <Panel title="活跃商机" note="有跟进记录的商机，按金额排序"
          extra={<Button type="link" size="small" onClick={() => open.route(recordsPath(range, scope, {view: 'opportunity', linked: '1'}))}>全部商机 →</Button>}>
          <LoadState loading={loadingFollowups} error={directory.error || followups.error} onRetry={followups.reload} rows={3}>
            <OpportunityTable items={opportunities.value?.items || []} loading={opportunities.loading} error={opportunities.value?.error || opportunities.error} reload={opportunities.reload} />
          </LoadState>
        </Panel>

        <ActivityFeed page={page} data={data} invoke={invoke} title="历史业务动态" note="当前账号的历史消息 · 不随上方周期和人员切换" embedded />
      </div>

      <aside className="pc-ws-side">
        <Panel title="当前待办任务" note={`${scope.kind === 'self' ? '我负责的' : `${label}负责的`} · 不限创建时间`} extra={<Button type="link" size="small" onClick={() => setDetail('all_pending')}>全部 →</Button>}>
          {!tasks.loading && taskCounts && <div className="pc-ws-taskstats" role="group" aria-label="任务概览">
            {[{key: 'today_pending', label: '今日待办', value: taskCounts.todayPending}, {key: 'all_pending', label: '全部待办', value: taskCounts.pending}, {key: 'today_completed', label: '今日已完成', value: taskCounts.todayCompleted}].map(metric => <button type="button" key={metric.key} aria-label={`${metric.label} ${metric.value}，查看任务`}
              onClick={() => setDetail(metric.key)}><b className="pc-num">{metric.value}</b><span>{metric.label}</span></button>)}
          </div>}
          <p className={tasks.error?'pc-error':'pc-muted'} role="status">{tasks.error?`同步未完成，保留最近数据：${tasks.error}`:tasks.lastSyncedAt?`自动同步 · ${new Date(tasks.lastSyncedAt).toLocaleTimeString('zh-CN')}`:'正在同步任务'}</p><LoadState loading={tasks.loading} error={!tasks.value ? tasks.error : ''} onRetry={tasks.reload} rows={4}>{tasks.value && <TaskList tasks={tasks.value} />}</LoadState>
        </Panel>
        <Panel title="当前未关闭风险" note={label} extra={<Button type="link" size="small" onClick={() => setDetail('risks')}>全部 →</Button>}>
          <LoadState loading={risks.loading} error={risks.value?.error || risks.error} onRetry={risks.reload} rows={3}>{risks.value && <RiskList risks={risks.value.items} />}</LoadState>
        </Panel>
        <Panel title="快捷操作">
          <div className="pc-ws-quick">{quick.map(item => <button type="button" key={item.key} disabled={item.disabled} onClick={() => !item.disabled && open.route(item.path)}><NavIcon name={item.icon} /><span>{item.label}</span></button>)}</div>
        </Panel>
        {data.isFdeLead && data.fdeTeamSummary && <Panel title={`${data.fdeTeamSummaryScope || '当前授权范围'}待办`} note="不随上方部门 / 成员筛选">
          <button type="button" className="pc-ws-fde" onClick={() => invoke('openFdeTeamTasks')}>{data.fdeTeamSummary.overdue} 逾期 · {data.fdeTeamSummary.claim} 待领取 · {data.fdeTeamSummary.handover} 待交接 →</button>
        </Panel>}
        <p className="pc-ws-caliber">口径：跟进按跟进日期统计{followups.value?.coverage ? `；来源：${followups.value.coverage.label}${followups.value.coverage.customers ? `（${followups.value.coverage.customers} 家客户）` : ''}` : ''}{followups.value?.coverage?.truncated ? '；客户较多，已读取最近活跃的前 400 家' : ''}。售前 / FDE 跟进与 ProjectBuddy 同步的售前记录一并计入。</p>
      </aside>
    </div>
    <FollowupDrawer visit={selected} onClose={() => setSelected(null)} />
    {detail && <WorkspaceItemsDrawer key={`${detail}:${key}`} selection={detail} scope={scope} label={label} onClose={() => setDetail(null)} />}
  </div>;
}
