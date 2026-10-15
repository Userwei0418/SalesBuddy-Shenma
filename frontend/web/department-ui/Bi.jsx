import FilterBar from './pc/FilterBar.jsx';
import React, {useEffect, useState} from 'react';
import {Button, Drawer, Table} from 'antd';
import {SbBarChart, SbChartCard, SbLineChart, SbSegmented, SbSelect, SbStatePanel} from '@shandiant/ui-react';
import {Kpi, PageHead} from './pc/ui.jsx';
import './bi.css';
import './pc/pc-ui.css';
import {RankingMembers} from './WorkflowCompletion.jsx';
import InsightsDetails from './InsightsDetails.jsx';
import {insightAmount, insightContext, insightDefinitions, insightRankingError, insightWan as wan} from './insights-presentation.js';

// 经营分析（原 pages/bi/index，非 FDE）：一行范围与季度，两排指标卡，四张图，公共排名。图全部交给组件库的图表件，主题来自 tokens 的 bridge-echarts。
export function supportsBi(page, data = {}) {
  return Boolean(page && page.route === 'pages/bi/index' && !data.isFde);
}

const number = value => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
// 接口给的是格式化好的元，指标卡按万元显示；「未登记」「未填写」原样当缺失
function kpiProps(item) {
  const money = insightAmount(item.value);
  if (money) return {...money, sub: item.sub};
  if (['未登记', '未填写', '—'].includes(String(item.value))) return {value: null, missingText: String(item.value), sub: item.sub};
  return {value: item.value, sub: item.sub};
}

export default function Bi({page, data: d, invoke}) {
  const [expanded, setExpanded] = useState({});
  const [selection, setSelection] = useState(null);
  const context = insightContext(d);
  useEffect(() => {
    if (selection && (selection.context !== context || d.loading || d.loadError)) setSelection(null);
  }, [context, d.loading, d.loadError, selection]);
  const selected = !d.loading && !d.loadError && selection?.context === context ? selection.key : null;
  const call = (name, payload = {}) => invoke(name, payload);
  const canScope = Boolean(d.canViewTeam), manager = canScope && (d.teamOptions || []).length > 1;
  const scopeLabel = `${d.viewMode === 'personal' ? d.memberLabel : manager ? d.teamLabel : '本团队'} · ${d.selectedQuarter?.label || ''}`;
  const kpis = d.kpis || [], primary = kpis.slice(0, 4), secondary = kpis.slice(4);
  const funnel = d.funnel || [], visitDays = d.visitDays || [], timeline = d.timeline || [];
  const chartState = d.loading ? 'loading' : d.loadError ? 'error' : 'normal';
  const rankState = d.rankingLoading ? 'loading' : d.rankingMessage ? 'error' : 'normal';
  const rankError = rankState === 'error' ? insightRankingError(d.rankingMessage) : '';
  const caliber = '经营数据按所选成员或团队、季度汇总。预测确收与回款取销售最新填写金额，历史变化保留；实际取当前采用的有效实绩，预测更新不改变实际。实际为 0 与未登记分别展示。加权预测 ACV 是独立的商机指标，按商机金额乘阶段概率计算，不用于计算季度预测或实际。商机榜和区域榜按所选季度预计关单的在推商机统计。';
  const metricClick = item => !d.loading && item.key && d.metricDetails?.[item.key] ? () => setSelection({key: item.key, context}) : undefined;
  return <div className="pc-page pc-bi" aria-label="经营分析">
    <PageHead title="经营分析" subtitle={`${scopeLabel} · ${d.dataModeLabel || '经营数据与跟进节奏'}${d.sourceDate ? ' · 更新至 ' + d.sourceDate : ''}`} />
    <section className="ds-bi" aria-label="经营分析内容">
    {(!d.loadError || d.loading) && <div className="pc-kpis" aria-label="经营概览">
        {(d.loading && !kpis.length ? Array.from({length: 4}, (_, i) => ({label: '正在读取', value: null})) : primary).map((item, i) => item.kind === 'ring'
          ? <Kpi key={`${item.label}-${i}`} label={item.label} value={item.value} sub={`已登记 ${item.actual} · 目标 ${item.target}${item.estimated ? ' · 规划口径' : ''}`} loading={d.loading} />
          : <Kpi key={`${item.label}-${i}`} label={item.label} loading={d.loading} {...kpiProps(item)} onClick={metricClick(item)} />)}
      </div>}
    <FilterBar className="ds-panel ds-bi-bar" label="经营分析筛选" actions={<Button disabled={d.loading || Boolean(d.loadError)} onClick={() => setSelection({key: 'definitions', context})}>指标口径</Button>}>
      {canScope && <SbSegmented value={d.viewMode} options={[{value: 'team', label: '团队'}, {value: 'personal', label: '个人'}]} onChange={mode => call('changeView', {dataset: {mode}})} />}
      {canScope && d.viewMode === 'personal' && <SbSelect data-toolbar-width={220} width={220} showSearch optionFilterProp="label" value={d.memberSelected?.[0]} loading={d.optionsLoading} disabled={d.optionsLoading || Boolean(d.optionsError)} options={(d.memberOptions || []).map(m => ({value: m.id, label: m.name}))} onChange={id => call('selectMember', {detail: {ids: [id]}})} />}
      {canScope && d.viewMode === 'team' && <SbSelect data-toolbar-width={220} width={220} value={d.teamPickerSelected?.[0]} loading={d.optionsLoading} disabled={d.optionsLoading || Boolean(d.optionsError)} options={(d.teamOptions || []).map(t => ({value: t.id, label: t.name}))} onChange={id => call('selectTeams', {detail: {ids: [id]}})} />}
      <div className="ds-bi-quarter" data-toolbar-width={405}>
        <SbSelect width={110} value={d.quarterYearIndex} options={(d.quarterYears || []).map((y, i) => ({value: i, label: `${y}年`}))} onChange={i => call('changeQuarterYear', {detail: {value: i}})} />
        <div className="ds-bi-quarters" role="group" aria-label="统计季度，可多选">
          <Button size="small" type={d.selectedQuarter?.isFullYear ? 'primary' : 'default'} aria-pressed={Boolean(d.selectedQuarter?.isFullYear)} onClick={() => call('selectFullYear')}>全年</Button>
          {(d.quarterChoices || []).map(q => <Button key={q.key} size="small" type={q.selected ? 'primary' : 'default'} aria-pressed={q.selected} onClick={() => call('toggleQuarter', {dataset: {key: q.key}})}>Q{q.quarter}</Button>)}
        </div>
        {d.quarterFilterDirty && <Button type="link" size="small" onClick={() => call('resetQuarters')}>重置</Button>}
      </div>
    </FilterBar>
    {d.optionsError && <p role="alert">{d.optionsError} <Button type="link" size="small" onClick={() => call('loadData')}>重试</Button></p>}
    {d.loadError && !d.loading ? <div className="ds-panel"><SbStatePanel state="error" title={d.loadError} description="筛选条件已保留" onRetry={() => call('loadFacts')} /></div> : <>

      {secondary.length > 0 && <div className="pc-kpis ds-bi-kpis-secondary">
        {secondary.map((item, i) => <Kpi key={`${item.label}-${i}`} label={item.label} loading={d.loading} {...kpiProps(item)} onClick={metricClick(item)} />)}
      </div>}
      <div className="ds-bi-countdown ds-muted">{d.countdown?.sentence ? d.countdown.sentence + ' · ' : ''}{d.selectedQuarter?.label}在推商机 {d.totalAcv}{` · 本期活跃商机 ${d.activeOpportunityCount == null ? '未统计' : d.activeOpportunityCount + ' 个'}`}</div>
      {d.activeOpportunityNote && <p className="ds-muted ds-insight-period-note">{d.activeOpportunityNote}</p>}
      <div className="ds-bi-charts">
        {!d.quarterEmpty && <SbChartCard title="各阶段商机 ACV 有多少" scope={scopeLabel} caliber={caliber} state={chartState} onRetry={() => call('loadFacts')} emptyTitle="这个季度还没有在推商机"
          summary={`各阶段商机 ACV：${funnel.map(r => `${r.name} ${wan(number(r.value)) ?? '未登记'} 万元`).join('，')}`}
          data={{columns: ['阶段', 'ACV（万元）', '商机数'], rows: funnel.map(r => [`${r.name} · ${r.probability}%`, wan(number(r.value)), r.count])}}>
          <SbBarChart orientation="horizontal" categories={funnel.map(r => `${r.name} · ${r.probability}%`)} series={[{name: 'ACV', data: funnel.map(r => wan(number(r.value)))}]} unit="万元" height={Math.max(200, funnel.length * 40 + 60)} />
        </SbChartCard>}
        <SbChartCard title="近 7 天跟进节奏" scope={`${d.viewMode === 'personal' ? d.memberLabel : d.teamLabel} · 已确认和已归档 · ${d.followupPeriodLabel || '近 7 天（含今天）'} · 不随季度筛选变化`} state={chartState} onRetry={() => call('loadFacts')} emptyTitle="近 7 天没有跟进记录"
          summary={`近 7 天跟进：${visitDays.map(r => `${r.weekday} ${r.count} 条`).join('，')}`}
          data={{columns: ['日期', '跟进数'], rows: visitDays.map(r => [r.weekday, number(r.count)])}}>
          <SbLineChart categories={visitDays.map(r => r.weekday)} series={[{name: '跟进数', data: visitDays.map(r => number(r.count))}]} unit="条" height={240} />
        </SbChartCard>
        {!d.quarterEmpty && <SbChartCard title="商机预计在哪个月关单" scope="按预计成交月份分布" state={chartState} onRetry={() => call('loadFacts')} emptyTitle="这个季度没有预计关单的商机"
          summary={`按月分布：${timeline.map(r => `${r.month} ${wan(number(r.value)) ?? '未登记'} 万元`).join('，')}`}
          data={{columns: ['月份', 'ACV（万元）', '商机数', '节奏'], rows: timeline.map(r => [r.month, wan(number(r.value)), r.count, r.status])}}>
          <SbBarChart orientation="vertical" categories={timeline.map(r => r.month)} series={[{name: 'ACV', data: timeline.map(r => wan(number(r.value)))}]} unit="万元" height={240} />
        </SbChartCard>}
      </div>
      <h2 className="ds-bi-h">公共排名 <small className="ds-muted">{d.rankingSubjectLabel}</small></h2>
      <div className="ds-bi-charts">
        {(d.rankingCards || []).map(card => {
          const money = card.key !== 'followup', all = card.rows || [], full = !!expanded[card.key];
          const rows = full ? all : all.filter(row => (card.summaryIds || []).includes(row.id));
          const unit = money ? '万元' : d.viewMode === 'personal' ? '次' : '次/人';
          const columns = [{title:'名次',dataIndex:'rank',width:64},{title:'名称',dataIndex:'name'},
            {title:money?'ACV（万元）':`跟进（${unit}）`,render:(_,r)=>money?wan(r.value):r.displayValue},
            {title:'统计说明',dataIndex:'meta'}];
          return <SbChartCard key={card.key} title={card.title} scope={`${card.subtitle} · ${card.period}`} state={rankError ? 'normal' : rankState} emptyTitle="当前范围暂无排名数据"
            summary={`${card.title}：${rankError || (rankState === 'loading' ? '正在加载' : rows.map(r => `第 ${r.rank} ${r.name} ${r.displayValue}`).join('，'))}`}
            data={rankState === 'normal' ? {columns:['名次','名称',unit,'说明'],rows:all.map(r=>[r.rank,r.name,money?wan(r.value):r.displayValue,r.meta])} : undefined}>
            {rankError ? <SbStatePanel state="error" title="排名加载失败" description={rankError} onRetry={() => call('reloadRankings')} /> : <>
            <div className="ds-rank-toolbar"><span className="ds-muted">{full?'完整榜单':'所选对象摘要'} · {card.subtitle}</span><Button type="link" size="small" aria-expanded={full} onClick={()=>setExpanded(v=>({...v,[card.key]:!v[card.key]}))}>{full?'收起榜单':'展开完整榜单'}</Button></div>
            {rows.length ? <Table rowKey="id" size="small" pagination={full?{pageSize:10,showSizeChanger:false}:false} columns={columns} dataSource={rows} scroll={{x:480}}
              expandable={card.key==='followup' && d.viewMode!=='personal'?{rowExpandable:r=>Array.isArray(r.members),expandIcon:({expanded,onExpand,record})=>Array.isArray(record.members)?<Button type="link" size="small" aria-expanded={expanded} aria-label={`查看 ${record.name} 成员次数`} onClick={event=>onExpand(record,event)}>{expanded?'收起':'成员'}</Button>:null,expandedRowRender:r=><RankingMembers row={r}/>}:undefined} />
              : <SbStatePanel state="empty" title={all.length?card.emptySummary:'当前范围暂无排名数据'} />}
            </>}
          </SbChartCard>;
        })}
      </div>
    </>}
    </section>
    {selected === 'definitions' && <Drawer open title="经营分析指标口径" onClose={() => setSelection(null)} size={760} classNames={{root: 'department-ui ds-insight-drawer'}}>
      <p className="ds-insight-definition">{scopeLabel}。未登记、已登记为 0 和部分登记分别展示；实际与预测独立统计。</p>
      <dl className="ds-insight-definitions">{insightDefinitions(d).map(item => <div key={item.key}><dt>{item.title}<small>{item.periodLabel}</small></dt><dd>{item.definition}</dd></div>)}</dl>
      <p className="ds-insight-summary">跟进节奏与跟进排名使用近 7 天（含今天），不随季度筛选变化。商机及区域 ACV 排名使用所选季度。公开排名的汇总查看范围与客户、商机明细权限分别校验。</p>
    </Drawer>}
    {selected && selected !== 'definitions' && d.metricDetails?.[selected] && <InsightsDetails key={`${context}:${selected}`} metricKey={selected} detail={d.metricDetails[selected]} onClose={() => setSelection(null)}
      onOpenRecord={typeof page.openMetricRecord === 'function' ? (metricKey, rowId) => call('openMetricRecord', {dataset: {metricKey, rowId}}) : undefined} />}
  </div>;
}
