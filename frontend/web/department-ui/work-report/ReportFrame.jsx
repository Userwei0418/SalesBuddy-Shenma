import React from 'react';
import {Alert, Button, Empty, Tag} from 'antd';
import {PageHead} from '../pc/ui.jsx';
import './work-report.css';

export const REPORT_TABS = [{key:'meeting',label:'本期概览'}, {key: 'compose', label: '生成报告'}, {key: 'documents', label: '我的报告'}, {key: 'schemes', label: '报告方案'}];

export function ReportFrame({tab, onTabChange, disabledTabs=[], actions, overview, filters, error, onRetry, children}) {
  return <div className="pc-page pc-list-page wr-workspace">
    <PageHead title="工作报告" extra={actions} />
    {overview}
    <nav className="wr-tabs" aria-label="工作报告栏目">
      {REPORT_TABS.map(item => <button key={item.key} type="button" disabled={disabledTabs.includes(item.key)} title={disabledTabs.includes(item.key)?'请先读取可生成范围的本期概览':undefined} aria-current={tab === item.key ? 'page' : undefined} className={tab === item.key ? 'is-active' : ''} onClick={() => onTabChange(item.key)}>{item.label}</button>)}
    </nav>
    {filters}
    {error && <Alert type="error" showIcon title={error} action={onRetry && <Button size="small" onClick={onRetry}>重试</Button>} />}
    <div className="wr-results">{children}</div>
  </div>;
}

export function ReportBlank({onPrepare, disabled}) {
  return <section className="wr-paper wr-paper-empty" aria-label="报告待生成">
    <div className="wr-document-icon" aria-hidden="true"><span /><span /><span /></div>
    <h2>把本期工作整理成报告</h2>
    <p>选择周期、范围和报告方案，查看材料后生成草稿。</p>
    <Button type="primary" onClick={onPrepare} disabled={disabled}>准备报告材料</Button>
    <div className="wr-steps"><span>准备材料</span><i>→</i><span>生成草稿</span><i>→</i><span>编辑确认</span><i>→</i><span>共享或导出</span></div>
  </section>;
}

export function EmptyReports({onCreate}) {
  return <div className="wr-empty-list"><Empty description="还没有保存的报告" />{onCreate && <Button type="primary" onClick={onCreate}>生成第一份报告</Button>}</div>;
}

export function CoverageNotice({state, message, children}) {
  if (state === 'complete') return children || null;
  return <Alert className="wr-coverage" type={state === 'failed' ? 'error' : 'warning'} showIcon title={message || '部分材料尚未取得，请核对后再生成报告'} />;
}

export function ReportStatus({status}) {
  const labels = {draft: '个人草稿', private: '个人草稿', shared: '已共享', queued: '等待生成', running: '正在生成', succeeded: '已生成', generated:'已生成', cancelled: '已取消', failed: '生成失败', insufficient_data: '材料不足', fallback: '基础汇总', basic_summary:'基础汇总'};
  return <Tag color={status === 'shared' ? 'blue' : status === 'failed' ? 'red' : status === 'fallback' ? 'orange' : undefined}>{labels[status] || status}</Tag>;
}
