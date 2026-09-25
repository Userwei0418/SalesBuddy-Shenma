import React, { useEffect, useState } from 'react';
import { Button, Drawer } from 'antd';
import {
  SbMetricStrip, SbMetricTile, SbSearch, SbStatePanel, SbStatusTag, SbTabs,
} from '@shandiant/ui-react';
import './home.css';

// The candidate replaces presentation only. Specialized recording/ChatBI modes
// continue to use their original controls and lifecycle in the shared page.
export function supportsHome(page, data = {}) {
  if (!page || page.route !== 'pages/index/index') return false;
  return ![
    'homeChatBIEnabled', 'managementTaskMode', 'visitRecordingMode',
    'visitTranscriptVisible', 'showResult', 'showManagementResult',
    'isRecording', 'isStarting', 'isStopping', 'isProcessing', 'isThinking',
  ].some(key => data[key]);
}

const list = value => Array.isArray(value) ? value : [];
const text = value => value == null ? '' : String(value);
const isPresent = value => value !== undefined && value !== null && value !== '';

function actionData(action = {}) {
  return {
    action: action.code,
    opportunityId: action.opportunityId,
    customerId: action.customerId,
    taskId: action.taskId,
    visitId: action.visitId,
    riskId: action.riskId,
  };
}

function rowData(row) {
  return {
    taskId: row.taskId, riskId: row.riskId, visitId: row.visitId,
    customerId: row.customerId, reportDetailId: row.reportDetailId,
  };
}

function ActivityStatus({ activity }) {
  if (!activity?.status) return null;
  // Health tones exist only when the source explicitly supplies statusLabel.
  // Task/claim workflow states remain neutral, even when their card is orange.
  if (!activity.health) {
    return <span className="department-home-process-status" title={activity.statusHint}>{activity.status}</span>;
  }
  // 总览只对红灯、黄灯打标签；向好和已更新不打，颜色出现就有意义
  if (!activity.attention) return null;
  const tone = { red: 'bad', yellow: 'watch' }[activity.tone] || 'watch';
  return <SbStatusTag tone={tone} label={activity.status} reason={activity.statusHint} />;
}

function ReceiptRow({ row, invoke }) {
  const actionable = Boolean(row.taskId || row.riskId || row.visitId || row.reportDetailId);
  const content = <>
    <span className="department-home-receipt-copy">
      <strong>{row.title}</strong>
      {isPresent(row.meta) && <span>{row.meta}</span>}
    </span>
    {isPresent(row.tag) && <span className="department-home-process-status">{row.tag}</span>}
    {actionable && <span aria-hidden="true">›</span>}
  </>;
  return actionable
    ? <button type="button" className="department-home-receipt-row department-home-receipt-action"
      onClick={() => invoke('handleCardRow', { dataset: rowData(row) })}>{content}</button>
    : <div className="department-home-receipt-row">{content}</div>;
}

function ReceiptDetails({ message, invoke }) {
  const card = message.card || {};
  const activity = message.webActivity;
  if (message.kind === 'text') return <p className="department-home-detail-text">{message.text}</p>;
  if (message.kind === 'insight') return <>
    {message.badge && <span className="department-home-process-status">{message.badge}</span>}
    <p className="department-home-detail-text">{message.text}</p>
    <Button type="primary" onClick={() => invoke('openWorkbench')}>查看分析与建议</Button>
  </>;
  return <div className="department-home-detail-content">
    <div className="department-home-detail-meta">
      {card.eyebrow && <span>{card.eyebrow}</span>}
      <ActivityStatus activity={activity} />
    </div>
    {card.subtitle && <p className="department-home-detail-text">{card.subtitle}</p>}
    {list(card.metrics).length > 0 && <div className="department-home-detail-metrics">
      {list(card.metrics).map((metric, index) => {
        const content = <SbMetricTile label={metric.label} value={metric.value} />;
        return metric.action
          ? <button type="button" className="department-home-metric-link" key={`${metric.label}-${index}`}
            aria-label={`${metric.label} ${text(metric.value)}，查看详情`}
            onClick={() => invoke('handleMetricAction', { dataset: { action: metric.action } })}>{content}<span>查看 ›</span></button>
          : <div key={`${metric.label}-${index}`}>{content}</div>;
      })}
    </div>}
    {list(card.sections).length > 0 ? list(card.sections).map((section, index) => <section
      className="department-home-receipt-section" key={section.key || index}>
      <header>
        <div><h3>{section.title}</h3>{section.subtitle && <p>{section.subtitle}</p>}</div>
        <span>{section.report ? `${list(section.rows).length} 项` : <>
          {isPresent(section.taskCount) && `${section.taskCount} 待办`}
          {isPresent(section.riskCount) && ` · ${section.riskCount} 风险`}
        </>}</span>
      </header>
      {list(section.rows).length ? list(section.rows).map((row, rowIndex) => <ReceiptRow
        key={rowIndex} row={row} invoke={invoke} />) : <p className="department-home-detail-muted">{section.emptyText || '没有待处理的任务'}</p>}
    </section>) : list(card.rows).length > 0 ? <section className="department-home-receipt-section">
      {list(card.rows).map((row, index) => <ReceiptRow key={index} row={row} invoke={invoke} />)}
    </section> : card.emptyText ? <p className="department-home-detail-muted">{card.emptyText}</p> : null}
    {card.footer && <p className="department-home-detail-muted">{card.footer}</p>}
    {card.action && <div className="department-home-detail-footer"><Button type="primary"
      onClick={() => invoke('handleCardAction', { dataset: actionData(card.action) })}>{card.action.label} →</Button></div>}
  </div>;
}

export default function Home({ page, data, invoke }) {
  const [selectedId, setSelectedId] = useState(null);
  const messages = list(data.webHomeMessages);
  const selected = messages.find(message => String(message.id) === selectedId);
  // Account changes and filters must never leave a stale object in the drawer.
  useEffect(() => {
    setSelectedId(null);
  }, [page, data.initializedAccount]);
  useEffect(() => {
    if (selectedId !== null && !selected) setSelectedId(null);
  }, [selectedId, selected]);

  if (!supportsHome(page, data)) return null;
  if (data.accessBlocked) return <SbStatePanel state="forbidden" description={data.accessMessage} />;

  const attentionCount = (list(data.webActivityGroups).find(g => g.key === 'attention') || {}).count || 0;
  return <div className="department-home">

    <div className="department-home-summary">
      <div className="department-home-welcome">
        <strong>{data.greeting || '你好'}{data.userName ? `，${data.userName}` : ''}</strong>
      </div>
      <div className="department-home-metrics">
        <SbMetricStrip columns={(list(data.webOverviewMetrics).length || 3) + 1} items={[...list(data.webOverviewMetrics).map(metric => ({
          key: metric.key, label: metric.label, value: metric.value, ariaLabel: `${metric.label} ${text(metric.value)}，查看任务`,
          onClick: () => invoke('openOverviewTasks', { dataset: { key: metric.key } }),
        })), {
          // 需关注：已加载动态里的红灯与黄灯，点进去就是筛好的列表。总览只读客户页、商机页算好的结果
          key: 'attention', label: '需关注', value: <span className={`department-home-attention ${attentionCount ? 'is-on' : ''}`}>{attentionCount}</span>,
          ariaLabel: `需关注 ${attentionCount}，查看需关注的动态`, onClick: () => invoke('webFilterActivity', { dataset: { key: 'attention' } }),
        }]} />
      </div>
    </div>

    {data.isFdeLead && data.fdeTeamSummary && <Button className="department-home-team-entry"
      onClick={() => invoke('openFdeTeamTasks')}>
      团队待办：{data.fdeTeamSummary.overdue} 逾期 · {data.fdeTeamSummary.claim} 待领取 · {data.fdeTeamSummary.handover} 待交接 ›
    </Button>}

    <section className="department-home-activity" aria-label="业务动态">
      <header className="department-home-activity-heading">
        <div><h2>业务动态</h2></div>
        <span>已加载 {data.webActivityTotal ?? messages.length} 条</span>
      </header>
      <div className="department-home-toolbar">
        <SbTabs size="small" activeKey={data.webActivityFilter || 'all'}
          onChange={key => invoke('webFilterActivity', { dataset: { key } })}
          items={list(data.webActivityGroups).map(group => ({ key: group.key, label: group.label, count: group.count }))} />
        <div className="department-home-search"><SbSearch value={data.webActivityQuery || ''}
          onChange={value => invoke('webSearchActivity', { detail: { value } })}
          placeholder="搜索客户、任务或动态内容" /></div>
      </div>
      <div className="department-home-feed" data-web-page-scroll="true">
        {!messages.length ? <SbStatePanel state="empty"
          title={data.webActivityFiltered ? '没有匹配的动态' : '还没有动态'}
          onClear={data.webActivityFiltered ? () => invoke('webClearActivity') : undefined} />
          : messages.map((message, index) => {
            const activity = message.webActivity || {};
            const card = message.card || {};
            return <React.Fragment key={message.id ?? index}>
              {message.webActivityDay && <div className="department-home-date">{message.webActivityDay}</div>}
              <article className="department-home-event">
                <button type="button" className="department-home-event-open"
                  aria-label={`查看动态：${activity.title || card.title || message.title || message.text || '业务动态'}`}
                  onClick={() => setSelectedId(String(message.id))}>
                  <span className="department-home-event-icon" aria-hidden="true">{activity.icon || '动'}</span>
                  <span className="department-home-event-copy">
                    <strong>{activity.title || card.title || message.title || message.text || '业务动态'}</strong>
                    {activity.change && <span className="department-home-event-change">{activity.change}</span>}
                  </span>
                  <span className="department-home-event-state"><ActivityStatus activity={activity} />
                    <time title={activity.time?.full || message.time}>{activity.time?.clock || message.time}</time>
                  </span>
                </button>
                <div className="department-home-event-actions">
                  {card.action ? <Button type="link" size="small"
                    onClick={() => invoke('handleCardAction', { dataset: actionData(card.action) })}>{card.action.label} →</Button>
                    : message.kind === 'insight' ? <Button type="link" size="small" onClick={() => invoke('openWorkbench')}>查看分析与建议 →</Button>
                    : <Button type="link" size="small" onClick={() => setSelectedId(String(message.id))}>查看详情</Button>}
                </div>
              </article>
            </React.Fragment>;
          })}
      </div>
      {data.webActivityFiltered && <div className="department-home-filter-summary">
        <span>当前显示 {data.webActivityCount ?? messages.length} 条</span>
        <Button type="link" size="small" onClick={() => invoke('webClearActivity')}>清除筛选</Button>
      </div>}
    </section>

    <Drawer open={Boolean(selected)} onClose={() => setSelectedId(null)}
      title={selected?.card?.title || selected?.title || '动态详情'} size={640}
      rootClassName="department-ui department-home-drawer">
      {selected && <><div className="department-home-detail-time">{selected.time}</div>
        <ReceiptDetails message={selected} invoke={invoke} /></>}
    </Drawer>
  </div>;
}
