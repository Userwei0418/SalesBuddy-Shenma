import React from 'react';
import {Button,Tag} from 'antd';
import {money,exactMoney} from './review/overview-model.js';
import {DASHBOARD_ACTUAL_METRICS,dashboardMetricReason} from './dashboard-monitor.js';

const sourceLabels={entries:'历史逐笔采用值',historical:'历史期间采用值',operations:'实绩登记'};
const statusLabels={missing:'未登记',single_source:'已登记',confirmed:'已确认',needs_confirmation:'来源待确认',stale_confirmation:'原确认待复核',partial:'部分登记',withdrawn:'已撤回'};
export function actualCoverageText(metric) {
  const reasons=new Set(metric.missing_reasons || []),parts=[];
  const append=(key,reason,label)=>{
    if (Number.isInteger(metric[key]) && metric[key]>0) { parts.push(`${metric[key]} ${label}`);if(reason)reasons.delete(reason); }
  };
  // These describe overlapping quality dimensions; never add them into a
  // fictitious missing-record total.
  append('incomplete_period_count','actual_incomplete','项季度记录尚未完整登记');
  append('missing_material_count',null,'项物料待补齐');
  append('tax_unknown_period_count','actual_tax_basis_unresolved','项季度记录的不含税口径待确认');
  append('conflict_period_count','actual_source_conflict','项季度记录来源待核对');
  append('attribution_unknown_period_count','actual_attribution_unresolved','项历史季度记录归属待核对，暂不计入个人或部门业绩');
  return [...new Set([...parts,...[...reasons].map(dashboardMetricReason)])].join('；');
}

export function actualCardView(metric) {
  const amount=metric.coverage==='complete'?metric.value:metric.coverage==='partial'?metric.known_subtotal:null;
  const reasons=metric.missing_reasons || [];
  if (amount==null && reasons.includes('actual_attribution_unresolved')) return {amount:null,label:'已采用金额',state:'归属待核对',color:'orange'};
  if (metric.coverage==='partial') return {amount,label:'已登记小计',state:'部分登记',color:'orange'};
  if (amount!=null) return {amount,label:'已采用金额',state:'已登记',color:'blue'};
  const state=reasons.includes('source_scope_not_fully_authorized')?'当前范围数据未齐':reasons.includes('actual_withdrawn')?'已撤回':reasons.includes('actual_attribution_unresolved')?'归属待核对':reasons.includes('actual_not_registered')?'未登记':'金额待核对';
  return {amount:null,label:'已采用金额',state,color:'default'};
}

// Actual amounts intentionally use a separate, concise layout. Forecast target,
// expected attainment, time progress and daily comparison do not belong here.
export function DashboardActualCards({value,onDrill}) {
  return <section className="ds-dashboard-actuals" aria-label="季度实际">
    <header className="ds-dashboard-actuals-heading"><h3>季度实际</h3><span>{value.quarter.year} Q{value.quarter.quarter} · 人民币</span></header>
    <div className="ds-dashboard-actual-grid">{DASHBOARD_ACTUAL_METRICS.map(definition=>{
      const metric=value.metrics.find(row=>row.metric_code===definition.code),view=actualCardView(metric),note=actualCoverageText(metric);
      return <section className="ds-dashboard-actual-card" key={definition.code} aria-label={definition.label}>
        <header><h4>{definition.label}</h4><Tag color={view.color}>{view.state}</Tag></header>
        <div className="ds-dashboard-actual-value"><div><small>{view.label}</small><div title={exactMoney(view.amount)}><strong>{money(view.amount)}</strong><span>万元</span></div></div><Button type="link" onClick={()=>onDrill?.(definition.code)}>查看明细 →</Button></div>
        <p className="ds-dashboard-actual-source">{metric.registered_fact_count>0 && <>已登记 {metric.registered_fact_count} 条来源记录 · </>}{definition.code==='actual_recognized_exclusive'?'来自已采用的不含税确收记录':'来自已采用的回款记录'}；未登记金额不计为零。</p>
        {note && <p className="ds-dashboard-actual-warning" role="status">{note}</p>}
      </section>;
    })}</div>
  </section>;
}

export function dashboardActualColumns(metric) {
  return [
    {title:'客户 / 商机',key:'object',width:250,render:(_,row)=><div><b>{row.customer_name || '客户名称未记录'}</b><small>{row.opportunity_id?row.opportunity_name || '商机名称未记录':'客户级记录 · 未关联商机'}</small>{row.material_line_id && <small>物料记录</small>}</div>},
    {title:'归属人员',dataIndex:'owner_name',width:140,render:(name,row)=>row.attribution_status==='unresolved'?'历史归属待核对':name || '—'},
    {title:'确认季度',key:'period',width:150,render:(_,row)=><div>{row.year} Q{row.quarter}<small>{row.period_precision==='day' && row.occurred_on?row.occurred_on:'季度汇总记录'}</small></div>},
    {title:'采用来源',dataIndex:'source',width:150,render:source=>sourceLabels[source] || '来源未记录'},
    {title:'登记状态',key:'status',width:170,render:(_,row)=><div>{statusLabels[row.status] || '状态待核对'}{row.attribution_status==='unresolved' && <small>归属待核对，未计入个人或部门业绩</small>}{row.tax_unresolved && <small>不含税口径待确认</small>}{row.missing_material_count>0 && <small>{row.missing_material_count} 项物料待补齐</small>}</div>},
    {title:metric==='actual_recognized_exclusive'?'采用不含税金额（万元）':'采用回款（万元）',key:'amount',width:190,render:(_,row)=><div><span title={exactMoney(row.amount)}>{money(row.amount)}</span>{!row.complete && row.amount!=null && <small>已登记小计</small>}</div>},
  ];
}
