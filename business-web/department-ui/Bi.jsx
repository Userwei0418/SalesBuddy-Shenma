import React, {useState, useEffect} from 'react';
import PresentationSwitch from './PresentationSwitch.jsx';
import {Button, Drawer} from 'antd';
import {SbBarChart, SbChartCard, SbKpiCard, SbLineChart, SbSegmented, SbSelect, SbStatePanel, SbTable} from '@shandiant/ui-react';
import './bi.css';

// 经营分析（原 pages/bi/index，非 FDE）：一行范围与季度，两排指标卡，四张图，公共排名。图全部交给组件库的图表件，主题来自 tokens 的 bridge-echarts。
export function supportsBi(page, data = {}) {
  return Boolean(page && page.route === 'pages/bi/index' && !data.isFde);
}

const CURRENCY = /^[¥￥]-?[\d,]+(?:\.\d+)?$/;
const yuan = value => CURRENCY.test(String(value || '')) ? Number(String(value).slice(1).replaceAll(',', '')) : null;
const wan = value => value == null ? null : Math.round(value / 10000 * 100) / 100;
const number = value => value == null || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
// 接口给的是格式化好的元，指标卡按万元显示；「未登记」「未填写」原样当缺失
function kpiProps(item) {
  const money = yuan(item.value);
  if (money != null) return {value: wan(money), unit: '万元', note: item.sub};
  if (['未登记', '未填写', '—'].includes(String(item.value))) return {value: null, missingText: String(item.value), note: item.sub};
  return {value: item.value, note: item.sub};
}

export default function Bi({page, data: d, invoke}) {
  const call = (name, payload = {}) => invoke(name, payload);
  const manager = d.canViewTeam, canScope = d.canViewTeam;
  const [memberTeam, setMemberTeam] = useState(null);
  useEffect(() => setMemberTeam(null), [d.rankingCards, d.viewMode, d.presentation]);
  const scopeLabel = `${d.viewMode === 'personal' ? d.memberLabel : manager ? d.teamLabel : '本团队'} · ${d.selectedQuarter?.label || ''}`;
  const kpis = d.kpis || [], primary = kpis.slice(0, 4), secondary = kpis.slice(4);
  const funnel = d.funnel || [], visitDays = d.visitDays || [], timeline = d.timeline || [];
  const chartState = d.loading ? 'loading' : d.loadError ? 'error' : 'normal';
  const rankState = d.rankingLoading ? 'loading' : d.rankingMessage ? 'error' : 'normal';
  const caliber = '经营数据按所选成员或团队汇总；确收、回款按发生季度统计，预测按已填季度计划乘阶段概率计算，包含 10% 阶段。实际为 0 与未登记分别展示。商机榜和区域榜按所选季度预计关单的在推商机统计。';
  return <section className="ds-bi" aria-label="经营分析">
    <PresentationSwitch data={d} invoke={invoke}/>
    <div className="ds-panel ds-bi-bar">
      {canScope && <SbSegmented value={d.viewMode} options={[{value: 'team', label: '团队'}, {value: 'personal', label: '个人'}]} onChange={mode => call('changeView', {dataset: {mode}})} />}
      {canScope && d.viewMode === 'personal' && <SbSelect width={220} showSearch optionFilterProp="label" value={d.memberSelected?.[0]} loading={d.optionsLoading} disabled={d.optionsLoading} options={(d.memberOptions || []).map(m => ({value: m.id, label: m.name}))} onChange={id => call('selectMember', {detail: {ids: [id]}})} />}
      {manager && d.viewMode === 'team' && <SbSelect width={220} value={d.teamPickerSelected?.[0]} loading={d.optionsLoading} disabled={d.optionsLoading} options={(d.teamOptions || []).map(t => ({value: t.id, label: t.name}))} onChange={id => call('selectTeams', {detail: {ids: [id]}})} />}
      {d.optionsError && <Button type="link" size="small" onClick={() => call('loadData')}>{d.optionsError} · 重试</Button>}
      <div className="ds-bi-quarter">
        <SbSelect width={110} value={d.quarterYearIndex} options={(d.quarterYears || []).map((y, i) => ({value: i, label: `${y}年`}))} onChange={i => call('changeQuarterYear', {detail: {value: i}})} />
        <div className="ds-bi-quarters" role="group" aria-label="统计季度，可多选">
          {(d.quarterChoices || []).map(q => <Button key={q.key} size="small" type={q.selected ? 'primary' : 'default'} aria-pressed={q.selected} onClick={() => call('toggleQuarter', {dataset: {key: q.key}})}>Q{q.quarter}</Button>)}
        </div>
        {d.quarterFilterDirty && <Button type="link" size="small" onClick={() => call('resetQuarters')}>重置</Button>}
      </div>
      <span className="ds-muted ds-bi-meta">{scopeLabel} · {d.dataModeLabel} · 更新至 {d.sourceDate}</span>
    </div>
    {d.loadError && !d.loading ? <div className="ds-panel"><SbStatePanel state="error" title={d.loadError} description="筛选条件已保留" onRetry={() => call('loadFacts')} /></div> : <>
      <div className="ds-bi-kpis">
        {(d.loading && !kpis.length ? Array.from({length: 4}, (_, i) => ({label: '正在读取', value: null})) : primary).map((item, i) => item.kind === 'ring'
          ? <SbKpiCard key={`${item.label}-${i}`} label={item.label} value={item.value} note={`已登记 ${item.actual} · 目标 ${item.target}${item.estimated ? ' · 规划口径' : ''}`} loading={d.loading} />
          : <SbKpiCard key={`${item.label}-${i}`} label={item.label} loading={d.loading} {...kpiProps(item)} />)}
      </div>
      {secondary.length > 0 && <div className="ds-bi-kpis ds-bi-kpis-secondary">
        {secondary.map((item, i) => <SbKpiCard key={`${item.label}-${i}`} label={item.label} loading={d.loading} {...kpiProps(item)} />)}
      </div>}
      <div className="ds-bi-countdown ds-muted">{d.countdown?.sentence} · 季度总商机 {d.totalAcv} · {d.activeOpportunityCount === null ? '—' : d.activeOpportunityCount} 个活跃商机</div>
      <div className="ds-bi-charts">
        {!d.quarterEmpty && <SbChartCard title="各阶段商机 ACV 有多少" scope={scopeLabel} caliber={caliber} state={chartState} onRetry={() => call('loadFacts')} emptyTitle="这个季度还没有在推商机"
          summary={`各阶段商机 ACV：${funnel.map(r => `${r.name} ${wan(number(r.value)) ?? '未登记'} 万元`).join('，')}`}
          data={{columns: ['阶段', 'ACV（万元）', '商机数'], rows: funnel.map(r => [`${r.name} · ${r.probability}%`, wan(number(r.value)), r.count])}}>
          <SbBarChart orientation="horizontal" categories={funnel.map(r => `${r.name} · ${r.probability}%`)} series={[{name: 'ACV', data: funnel.map(r => wan(number(r.value)))}]} unit="万元" height={Math.max(200, funnel.length * 40 + 60)} />
        </SbChartCard>}
        <SbChartCard title="近 7 天跟进节奏够不够" scope={`已确认和已归档的拜访 · 近 7 天 ${d.weeklyVisitCount} 条`} state={chartState} onRetry={() => call('loadFacts')} emptyTitle="近 7 天没有跟进记录"
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
          const money = card.key !== 'followup', rows = card.rows || [];
          const perCapita = !money && d.viewMode !== 'personal';
          const unit = money ? '万元' : perCapita ? '次/人' : '次';
          const value = r => money ? wan(r.value) : perCapita && r.member_count === 0 ? null : r.value;
          return <SbChartCard key={card.key} title={card.title} scope={`${card.subtitle} · ${card.period}`} state={rankState} onRetry={() => call('reloadRankings')} emptyTitle="当前范围暂无排名数据"
            summary={`${card.title}：${rows.slice(0, 3).map((r, i) => `${r.rank == null ? '未排名' : `第 ${r.rank}`} ${r.name} ${value(r) ?? '未统计'} ${unit}`).join('，')}`}
            data={{columns: ['名次', '名称', money ? 'ACV（万元）' : perCapita ? '人均跟进（次/人）' : '跟进次数', '说明'], rows: rows.map(r => [r.rank ?? '—', r.name, value(r), r.meta])}}>
            {rows.length ? <SbBarChart orientation="horizontal" categories={rows.map(r => r.name)} series={[{name: money ? 'ACV' : '跟进', data: rows.map(value)}]} unit={unit} maxItems={10} height={Math.max(160, Math.min(rows.length, 10) * 32 + 60)} />
              : <SbStatePanel state="empty" title="当前范围暂无排名数据" />}
          {perCapita && <div className="ds-sync-members">{rows.map(r => <Button key={r.code || r.id || r.name} size="small" onClick={() => setMemberTeam(r)}>{r.name} · 成员明细</Button>)}</div>}
          </SbChartCard>;
        })}
      </div>
    </>}
    <Drawer title={`${memberTeam?.name || ''} · 跟进构成`} open={!!memberTeam} onClose={() => setMemberTeam(null)} width={680}>
      <p>跟进总次数 ÷ 有效在岗人数，包含零跟进成员；无有效成员不参与排名。</p>
      <SbTable rowKey="user_id" rows={memberTeam?.members || []} columns={[{title:'成员',dataIndex:'name'},{title:'跟进次数',dataIndex:'followup_count'},{title:'覆盖客户',dataIndex:'customer_count'}]} emptyTitle="当前团队没有有效成员"/>
    </Drawer>
  </section>;
}
