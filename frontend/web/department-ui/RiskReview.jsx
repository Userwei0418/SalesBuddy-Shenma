import React, {useEffect} from 'react';
import {PAGE_SIZE_OPTIONS, useListPageSize} from './pc/list-pagination.js';
import {Button, Pagination} from 'antd';
import {SbBottomBar, SbField, SbSegmented, SbSelect, SbStatePanel, SbStatusTag, SbTable, SbTabs} from '@shandiant/ui-react';
import {SbTextarea} from './ImeInput.jsx';
import {Kpi, PageHead} from './pc/ui.jsx';
import FilterBar from './pc/FilterBar.jsx';
import {decisionLabel, evidenceText, riskClosed, riskCount, riskDate, riskEvent, riskStatus, riskSuggestion, riskVersioned} from './risk-presentation.js';
import './small-pages.css';
import './risk-review.css';

export function supportsRisks(page) { return page?.route === 'pages/risks/index'; }
export function Risks({page, data:d, invoke}) {
  const call = (name,payload={}) => invoke(name,payload);
  const rows = d.loadError ? [] : d.filteredRisks || [];
  const summary = !d.loading && !d.loadError ? d.summary || {} : {};
  const total = riskCount(d.total);
  const [pageSize, setPageSize] = useListPageSize('risks');
  const pending = (d.scopeMode === 'team' && !d.selectedTeamId) || (d.scopeMode === 'person' && !d.selectedMemberId);
  useEffect(() => {
    if (!page || d.loading || d.pageSize === pageSize || pending) return;
    page.setData({pageSize, offset: 0});
    call('loadRisks');
  }, [page, pageSize, d.pageSize, d.loading, pending]);
  const cols = [
    {title:'风险 / 客户',key:'title',width:300,render:(_,r)=><div className="ds-sp-two"><b>{r.title}</b><span className="ds-muted">{r.customerName}{r.team ? ` · ${r.team}` : ''}</span></div>},
    {title:'状态',key:'status',width:155,render:(_,r)=><SbStatusTag tone={riskClosed(r)?'good':'watch'} label={riskStatus(r)} />},
    {title:'等级',key:'severity',width:90,render:(_,r)=>`${r.severity || '未评级'}风险`},
    {title:'负责人',key:'owner',width:110,render:(_,r)=>r.owner || '待确认'},
    {title:'时间',key:'time',width:165,render:(_,r)=><span className="ds-muted">{riskClosed(r) ? `解除 ${r.resolvedLabel}` : `识别 ${r.openedLabel}`}</span>},
    {title:'关联商机',key:'opportunity',width:180,render:(_,r)=>r.opportunityId ? <Button type="link" size="small" onClick={e=>{e.stopPropagation();call('openOpportunity',{dataset:{customerId:r.customerId,opportunityId:r.opportunityId}});}}>{r.opportunityName || '查看商机'}</Button> : <span className="ds-muted">仅关联客户</span>},
  ];
  return <section className="pc-page pc-list-page pc-risk-list" aria-label={d.opportunityOnly?'商机风险':'风险预警'}>
    <PageHead title={d.opportunityOnly?'商机风险':'风险预警'} subtitle="客户与商机风险集中查看。建议由人确认；未关闭的风险持续保留。" />
    <div className="pc-kpis" aria-label="风险概览">{[['全部风险','total'],['未关闭','open_count'],['已解除','resolved_count'],['已逾期','overdue_count']].map(([label,key])=><Kpi key={key} label={label} value={riskCount(summary[key])} unit="项" missingText="未取得" loading={d.loading} sub={key==='overdue_count'?'超过处理期限且仍未关闭':'当前授权范围全部记录'} />)}</div>
    <FilterBar className="ds-panel pc-risk-filters" label="风险筛选" actions={<Button loading={d.loading} onClick={()=>call('loadRisks')}>刷新</Button>}>
      {(d.scopeOptions || []).length > 0 && <SbSegmented data-toolbar-width={300} options={d.scopeOptions} value={d.scopeMode} onChange={value=>call('changeScope',{detail:{value}})} />}
      {d.scopeMode==='team' && <SbSelect data-toolbar-width={220} width={220} aria-label="选择部门" placeholder="选择部门" value={d.selectedTeamId || undefined} options={(d.teamOptions||[]).map(t=>({value:t.id,label:t.name}))} loading={d.directoryLoading} disabled={Boolean(d.directoryError)} onChange={value=>call('selectRiskTeam',{detail:{value}})} />}
      {d.scopeMode==='person' && <SbSelect data-toolbar-width={240} width={240} aria-label="选择成员" placeholder="选择成员" showSearch optionFilterProp="label" value={d.selectedMemberId || undefined} options={(d.memberOptions||[]).map(m=>({value:m.id,label:[m.name,m.teamLabel,m.account_code].filter(Boolean).join(' · ')}))} loading={d.directoryLoading} disabled={Boolean(d.directoryError)} onChange={value=>call('selectRiskMember',{detail:{value}})} />}
    </FilterBar>
    <div className="ds-panel ds-sp-card pc-risk-results">
      <SbTabs activeKey={d.activeTab} onChange={key=>call('selectTab',{dataset:{key}})} items={(d.tabs || []).map(t=>{
        const count=riskCount(summary[t.key==='open'?'open_count':t.key==='resolved'?'resolved_count':'total']);
        return {key:t.key,label:<span>{t.key==='open'?'未关闭':t.key==='resolved'?'已解除':t.label}{count!=null && <small className="pc-risk-tab-count">{count}</small>}</span>};
      })} />
      {d.directoryError && <p className="ds-sp-error" role="alert">{d.directoryError} <Button type="link" onClick={()=>call('loadDirectory')}>重试目录</Button></p>}
      <div className="ds-sp-body ds-sp-body-pad"><SbTable rowKey="id" density="compact" columns={cols} rows={rows} state={d.loading?'loading':d.loadError?'error':rows.length?'normal':'empty'} scrollX={1000} emptyTitle={pending?'请先选择查看范围':d.loadError?'风险读取失败':'当前范围暂无对应风险'} emptyDescription={d.loadError || (pending?'选择具体部门或成员后读取':'切换状态可查看历史风险')} onRetry={()=>call('loadRisks')} onRowClick={r=>call('openRisk',{dataset:{id:r.id}})} /></div>
      {total != null && !d.loadError && !pending && <div className="pc-risk-pagination"><Pagination current={Math.floor((d.offset || 0)/(d.pageSize || 50))+1} total={total} pageSize={d.pageSize || 50} showSizeChanger pageSizeOptions={PAGE_SIZE_OPTIONS} showTotal={n=>`共 ${n} 项`} disabled={d.loading} onChange={(current,size)=>{if(size!==pageSize)setPageSize(size);else call('changePage',{detail:{current}});}} /></div>}
    </div>
  </section>;
}

export function supportsRiskDetail(page) { return page?.route === 'pages/risk-detail/index'; }
export function RiskDetail({data:d, invoke}) {
  const call=(name,payload={})=>invoke(name,payload), r=d.risk;
  if (d.loading || !r) return <SbStatePanel state={d.loading?'loading':d.loadError?'error':'empty'} title={d.loading?'正在读取风险':d.loadError || '风险不存在或已不可见'} onRetry={()=>call('refreshRisk')} />;
  const closed=riskClosed(r), valid=riskVersioned(r), busy=Boolean(d.submitting || d.decisionSubmitting);
  const canResolve=!closed && valid && d.canResolveRisk===true && r.canResolve===true;
  const suggestion=riskSuggestion(r), canDecide=!closed && valid && r.canDecide===true && Boolean(suggestion);
  const decision=r.current_decision, events=r.events || [];
  const decisionStale=Boolean(decision && (r.decisionStale === true || decision.is_current !== true || decision.risk_version !== r.version_no));
  const disabled=busy || Boolean(d.refreshRequired);
  return <section className="ds-sp ds-sp-two-col pc-risk-detail" aria-label="风险详情">
    <aside className="ds-panel ds-sp-side"><div className="ds-sp-tags"><SbStatusTag tone={closed?'good':'watch'} label={riskStatus(r)} /><span className="ds-sp-chip">{r.severity || '未评级'}风险</span></div>
      <p className="ds-muted">{r.customerName}</p><h1>{r.title}</h1><p className="ds-muted">{[r.owner,r.team].filter(Boolean).join(' · ')}</p>
      <dl className="ds-sp-info"><div><dt>识别时间</dt><dd>{r.openedLabel}</dd></div>{closed && <div><dt>解除时间</dt><dd>{r.resolvedLabel}</dd></div>}</dl>
      <p className="ds-muted">采纳建议后风险仍会保留，由具备解除权限的 leader 确认风险已消除。</p>
    </aside>
    <section className="ds-panel ds-sp-main">
      <section className="ds-sp-block"><h2>风险说明</h2><p className="ds-sp-pre">{r.description || '暂无说明'}</p></section>
      <section className="ds-sp-block"><h2>判断依据</h2><ol className="ds-sp-evidence">{(r.evidence?.length?r.evidence:['未登记风险依据']).map((e,i)=><li key={i}>{evidenceText(e) || '系统记录了相关事实，但暂未整理成可读依据'}</li>)}</ol></section>
      <section className="ds-sp-block ds-sp-next"><h2>建议动作</h2><p>{suggestion || '当前未提供建议动作'}</p>
        {decision && <div className="pc-risk-decision"><b>{decisionLabel(decision.decision)}{decisionStale?' · 历史版本决定':''}</b><span className="ds-muted">{decision.actor_name || '处理人未登记'} · {riskDate(decision.occurred_at)}</span><p className="ds-sp-pre">{decision.note}</p>{decisionStale && <p className="ds-muted">此后风险已有更新，请结合当前依据重新判断。</p>}</div>}
        {canDecide && <><SbSegmented aria-label="建议处理方式" value={d.decisionChoice || ''} disabled={disabled} options={[{label:'采纳建议',value:'adopt'},{label:'暂不采纳',value:'defer'}]} onChange={value=>call('chooseDecision',{detail:{value}})} /><SbField label="确认说明" required help="记录你的判断和后续安排"><SbTextarea value={d.decisionNote || ''} disabled={disabled} maxLength={2000} autoSize={{minRows:3,maxRows:7}} placeholder="说明采纳后的安排，或暂不采纳的原因" onChange={value=>call('inputDecisionNote',{detail:{value}})} /></SbField><div className="pc-risk-confirm"><span className="ds-muted">本次只记录处理决定，不会自动生成任务或关闭风险。</span><Button type="primary" loading={d.decisionSubmitting} disabled={disabled || !['adopt','defer'].includes(d.decisionChoice) || !String(d.decisionNote||'').trim()} onClick={()=>call('confirmDecision')}>确认处理建议</Button></div></>}
        {!closed && suggestion && !canDecide && <p className="ds-muted">由具备处理权限的负责人确认建议。</p>}
      </section>
      {(d.decisionError || d.resolveError) && <p role="alert" className="ds-sp-error">{d.decisionError || d.resolveError}</p>}
      {d.refreshRequired && <p role="alert">风险已有更新，请先读取最新记录。<Button disabled={busy} onClick={()=>call('refreshRisk')}>读取最新风险</Button></p>}
      {events.length > 0 && <details className="ds-sp-block pc-risk-history"><summary>处理记录 · {events.length} 条</summary><ol>{events.map((event,i)=>{const item=riskEvent(event);return <li key={event.id || i}><b>{item.title}</b><span className="ds-muted">{item.actor} · {item.date}</span>{item.note && <p className="ds-sp-pre">{item.note}</p>}{item.suggestion && <p className="ds-muted ds-sp-pre">当时建议：{item.suggestion}</p>}</li>;})}</ol></details>}
      {closed ? <section className="ds-sp-block ds-sp-block-good"><h2>{r.resolvedBy} 已解除风险 <small className="ds-muted">{r.resolvedLabel}</small></h2><p className="ds-sp-pre">{r.resolutionNote}</p></section> : canResolve ? <section className="ds-sp-block"><h2>解除风险</h2><p className="ds-muted">请确认风险已消除，并填写事实依据。</p><SbField label="解除依据" required help="至少 5 个字"><SbTextarea value={d.resolutionNote || ''} disabled={disabled} maxLength={500} autoSize={{minRows:4,maxRows:8}} onChange={value=>call('inputResolutionNote',{detail:{value}})} /></SbField></section> : <p className="ds-muted">解除风险由有权限且管理该范围的 leader 操作。</p>}
      {canResolve && <div className="ds-sp-bar"><SbBottomBar reason="解除记录保留处理人、时间和依据" primary={{label:'确认解除风险',loading:d.submitting,disabled:disabled || String(d.resolutionNote||'').trim().length<5,onClick:()=>call('confirmResolve')}} /></div>}
    </section>
  </section>;
}
