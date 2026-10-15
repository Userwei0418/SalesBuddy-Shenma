import React from 'react';
import {Alert, Button, Input, Select, Tag} from 'antd';
import FilterBar from '../pc/FilterBar.jsx';
import {Kpi} from '../pc/ui.jsx';
import {PERIOD_CHOICES, calendarHint} from './state.js';
import {CoverageNotice} from './ReportFrame.jsx';

// Display models only. Counts, coverage and allowed actions are supplied by the service.
export function ReportFilters({value, onChange, scopeControl, schemes, periods=PERIOD_CHOICES, disabled, prepareDisabled, onPrepare, preparing, actions}) {
  return <FilterBar label="报告条件" actions={actions ?? (onPrepare && <Button type="primary" loading={preparing} disabled={disabled || prepareDisabled} onClick={onPrepare}>准备材料</Button>)}>
    <Select aria-label="报告周期" value={value.period} options={periods} onChange={period=>onChange({...value,period})} disabled={disabled} data-toolbar-width={120} />
    <Input aria-label="周期内日期" type="date" value={value.anchor} onChange={event=>onChange({...value,anchor:event.target.value})} disabled={disabled} title={calendarHint(value.period,value.anchor)} data-toolbar-width={156} />
    {scopeControl && <div data-toolbar-width={280}>{scopeControl}</div>}
    <Select aria-label="使用报告方案" value={value.schemeId} options={schemes} onChange={schemeId=>onChange({...value,schemeId})} disabled={disabled} placeholder="选择报告方案" data-toolbar-width={220} />
  </FilterBar>;
}

export function ReportMetrics({items, loading}) {
  if (!items.length) return null;
  return <section className="pc-kpis wr-metrics" aria-label="报告材料统计">{items.map(item=><Kpi key={item.id} label={item.label} value={item.complete ? item.display : null} unit={item.unit} sub={item.note || (item.complete ? undefined : '统计尚未完整取得')} loading={loading} />)}</section>;
}

export function ReportSupplement({text, onChange, disabled, maxLength, error,sections,sectionId,onSectionChange}) {
  return <section className="wr-panel" aria-label="手工补充材料"><h2>补充说明</h2>
    {sections && <div className="wr-field"><label>补充内容用于</label><Select aria-label="补充内容用于" value={sectionId || ''} onChange={onSectionChange} disabled={disabled}
      options={[{value:'',label:'未指定（纳入工作总结）'},...sections.map(section=>({value:section.id,label:section.title}))]}/></div>}
    <Input.TextArea aria-label="补充说明" value={text} rows={6} maxLength={maxLength} disabled={disabled} onChange={event=>onChange(event.target.value)} placeholder="补充尚未登记的工作、需要协调的问题或下期计划。预计回款请写明客户/商机、计划日期、金额及币种，并注明是预计。" />
    <p className="wr-muted wr-field-note">补充内容单独标识，不计入系统业务统计。{sectionId==='next_week_collection'?'预计回款将标为用户补充、未核实；不会成为系统确认的回款计划。':''}</p>
    {error && <Alert type="error" title={error} />}
  </section>;
}

export function SalesWeekCoverage({value}) {
  if (!value) return null;
  if (!value.supported) return <Alert type="error" title={value.message}/>;
  return <section aria-label="销售周报章节材料" className="wr-report-outline">
    <h3>各章节材料情况</h3>
    <p className="wr-muted">{value.asOf}</p><p className="wr-muted">{value.nextPeriod}</p>
    {value.precisionNote && <Alert type="warning" title={value.precisionNote}/>}
    <div className="wr-material-sources">{value.sections.map(section=><article className="wr-material-source" key={section.id}>
      <div><b>{section.title}</b><p>{section.coverage}</p>{section.notes.map((note,index)=><p className="wr-muted" key={index}>{note}</p>)}</div>
      <Tag color={section.complete ? undefined : 'orange'}>{section.state}</Tag>
    </article>)}</div>
  </section>;
}

export default function MaterialReview({value, onSources, onGenerate, onRefresh, busy}) {
  return <section className="wr-paper" aria-label="报告材料预览">
    <header className="wr-paper-header"><div><strong>本次报告材料</strong><p className="wr-muted">{value.periodLabel} · {value.scopeLabel}</p></div>{onRefresh && <Button onClick={onRefresh} disabled={busy}>重新准备</Button>}</header>
    <div className="wr-editor-body">
      <CoverageNotice state={value.coverageState} message={value.coverageMessage} />
      <p className="wr-muted">{value.asOfLabel}</p>
      {value.cutoffLabel && <p className="wr-muted">{value.cutoffLabel}</p>}
      <SalesWeekCoverage value={value.weekly}/>
      <div className="wr-material-sources">{value.sources.map(source=><article key={source.id} className="wr-material-source">
        <div><b>{source.label}</b><p>{source.description}</p></div>
        <div className="wr-toolbar"><Tag color={source.complete ? undefined : 'orange'}>{source.countLabel}</Tag>{source.canRead && onSources && <Button size="small" onClick={()=>onSources(source.id)}>查看材料</Button>}</div>
      </article>)}</div>
      {value.notices?.length > 0 && <ul className="wr-material-notices">{value.notices.map((text,index)=><li key={index}>{text}</li>)}</ul>}
      <div className="wr-report-outline"><h3>报告结构</h3><ol>{value.sections.map(section=><li key={section.id}>{section.title}</li>)}</ol><p className="wr-muted">{value.styleLabel}</p></div>
    </div>
    <footer className="wr-paper-header"><span className="wr-muted">生成后先保存为个人草稿</span><Button type="primary" onClick={onGenerate} loading={busy} disabled={!value.canGenerate}>{value.generateLabel || '生成报告草稿'}</Button></footer>
  </section>;
}
