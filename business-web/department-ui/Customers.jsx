import React, {useLayoutEffect, useMemo, useRef, useState} from 'react';
import {Alert, Button, Modal, Tooltip} from 'antd';
import {SbBattleMap, SbLabeledSelect, SbMetricStrip, SbSearch, SbStatusTag, SbTable} from '@shandiant/ui-react';
import './customers.css';

const tone = value => ({green: 'good', yellow: 'watch', red: 'bad', gray: 'pending', good: 'good', watch: 'watch', bad: 'bad'}[value] || 'pending');
// B-01：标签文字只用向好、需关注、转差、待评估，不用上游的「健康」「提醒」；依据一并显示
const TONE_LABEL = {good: '向好', watch: '需关注', bad: '转差', pending: '待评估'};
// 作战地图交给 SbBattleMap：页面里潜力、关系是 0～100 分，分界 70；组件收 1～10，分界 7
const QUADRANT_LABEL = {attack: '主攻区', asset: '客户资产', spot: '见单打单', resource: '客户资源'};
const QUADRANT_BY_LABEL = Object.fromEntries(Object.entries(QUADRANT_LABEL).map(([k, v]) => [v, k]));
const tenth = value => Number.isFinite(Number(value)) && value !== null && value !== '' ? Math.max(0, Math.min(10, Number(value) / 10)) : null;
// 金额档：按当前范围内客户的年度合同额分三档（前三分之一大、中间中、其余小），没有金额算小
function amountBands(rows) {
  const amounts = rows.map(r => Number(r.acv_amount ?? r.opportunity_amount ?? 0)).filter(v => v > 0).sort((a, b) => b - a);
  const big = amounts[Math.floor(amounts.length / 3)] ?? Infinity, mid = amounts[Math.floor(amounts.length * 2 / 3)] ?? Infinity;
  return row => { const v = Number(row.acv_amount ?? row.opportunity_amount ?? 0); return v > 0 && v >= big ? 'large' : v > 0 && v >= mid ? 'medium' : 'small'; };
}

function Filter({label, options = [], index = 0, onChange, disabled}) {
  // 上游选项第 0 项是「全部」，用下标当值；组件里没选就是全部
  return <SbLabeledSelect label={label} disabled={disabled} value={index > 0 ? index : undefined} options={options.map((item, i) => ({value: i, label: item.label})).filter(o => o.value > 0)} onChange={value => onChange(value ?? 0)}/>;
}

export default function Customers({page, data: d, invoke}) {
  const [{pageNumber, pageSize}, setPagination] = useState(() => ({pageNumber: page._departmentCustomerPage || 1, pageSize: page._departmentCustomerPageSize || 6}));
  const tableViewport = useRef(null);
  // 页面固定一屏：列表每页条数按表格区域剩余高度算，正好放满
  useLayoutEffect(() => {
    const viewport = tableViewport.current;
    if (!viewport) return;
    const measure = () => {
      const headingHeight = viewport.querySelector('thead')?.getBoundingClientRect().height || 40;
      const size = innerWidth <= 900 ? 6 : Math.max(3, Math.min(20, Math.floor((viewport.clientHeight - headingHeight - 1) / 56)));
      setPagination(previous => {
        if (previous.pageSize === size) return previous;
        const next = {pageSize: size, pageNumber: Math.floor((previous.pageNumber - 1) * previous.pageSize / size) + 1};
        page._departmentCustomerPage = next.pageNumber; page._departmentCustomerPageSize = size;
        return next;
      });
    };
    const observer = new ResizeObserver(measure); observer.observe(viewport); measure();
    return () => observer.disconnect();
  }, [page]);
  const [showPending, setShowPending] = useState(false);
  const customers = (showPending ? d.pendingCustomers : d.customers) || [], plots = d.plotCustomers || [];
  const [cluster, setCluster] = useState(null);
  // 客户详情走组件版页面，不再用原页面的侧滑详情
  const openDetail = id => globalThis.SalesRuntime.wx.navigateTo({url: '/pages/customer-detail/index?id=' + encodeURIComponent(id)});
  const points = useMemo(() => {
    const band = amountBands(plots);
    return plots.map(row => ({id: row.id, name: row.name, potential: tenth(row.potential), relationship: tenth(row.relationship), tone: tone(row.signal?.tone), amountBand: band(row),
      summary: `${row.team || ''}${row.owner ? ` · ${row.owner}` : ''} · 潜力 ${tenth(row.potential) === null ? '未登记' : tenth(row.potential).toFixed(1)} · 关系 ${tenth(row.relationship) === null ? '未登记' : tenth(row.relationship).toFixed(1)}/10`}));
  }, [plots]);
  const unrated = (d.pendingCustomers || []).length;
  const zoomQuadrant = d.quadrantIndex > 0 ? (QUADRANT_BY_LABEL[(d.quadrantOptions || [])[d.quadrantIndex]?.label] || null) : null;
  const count = customers.length;
  const currentPage = Math.min(pageNumber, Math.max(1, Math.ceil(count / pageSize)));
  const changePage = value => {page._departmentCustomerPage = value; setPagination(previous => ({...previous, pageNumber: value}));};
  const change = (method, options) => {changePage(1); return invoke(method, options);};
  const picker = method => value => change(method, {detail: {value}});
  const state = d.mapLoading ? 'loading' : d.mapError ? 'error' : !count ? 'empty' : 'normal';
  const chooseLevels = selected => {
    const previous = d.mapSelectedLevels || [];
    for (const value of new Set([...selected, ...previous])) if (selected.includes(value) !== previous.includes(value)) change('toggleMapLevel', {dataset: {value}});
  };
  const reset = () => {
    setShowPending(false);
    change('resetAllFilters'); change('search', {detail: {value: ''}});
  };
  const columns = [
    {title: '客户 / 负责人', key: 'customer', width: '39%', render: (_, row) => <div className="ds-customer-identity">
      <button className="ds-text-action ds-customer-name" title={row.name} onClick={() => openDetail(row.id)}>{row.name}</button>
      <div className="ds-muted" title={`${row.team} · ${row.owner}${row.level ? ` · ${row.level}` : ''}`}>{row.team} · {row.owner}{row.level && ` · ${row.level}`}</div>
    </div>},
    {title: '当前状态', key: 'status', width: '31%', render: (_, row) => <div className="ds-customer-health"><SbStatusTag tone={tone(row.signal?.tone)} label={TONE_LABEL[tone(row.signal?.tone)]} reason={row.signal?.reason || row.signal?.detail} showReason />{row.risk && !['暂无重大风险', '暂无判断依据', '没有需要关注的风险', '没有依据'].includes(row.risk) && row.risk !== (row.signal?.reason || row.signal?.detail) && <Tooltip title={row.risk}><p className="ds-muted">{row.risk}</p></Tooltip>}</div>},
    {title: '最近沟通', dataIndex: 'lastVisit', key: 'visit', width: 104, render: value => value || '未登记'},
  ];
  return <section className="ds-customers">
    <section className="ds-customer-assets" aria-label="客户资产汇总">
      <SbMetricStrip columns={4} variant="flat" loading={d.acvLoading && d.assetLoading}
        items={[
          {key: 'portfolio', label: '管理客户 · 家', value: d.scopeCustomerCount ?? '—', note: '当前权限范围全部客户'},
          {key: 'acv', label: '年度合同额 · 万元', value: d.acvLoading ? '…' : d.acvText, note: d.acvLoading ? '正在读取' : undefined},
          {key: 'recognized', label: '确收 · 万元', value: d.assetLoading ? '…' : d.recognizedText, note: d.assetLoading ? '正在读取' : undefined, onClick: () => invoke('openAssets', {dataset: {kind: 'recognized'}}), ariaLabel: '确收，查看明细'},
          {key: 'collection', label: '回款 · 万元', value: d.assetLoading ? '…' : d.collectionText, note: d.assetLoading ? '正在读取' : undefined, onClick: () => invoke('openAssets', {dataset: {kind: 'collection'}}), ariaLabel: '回款，查看明细'},
        ]}
        periods={[{label: '本年', value: 'year'}, {label: '历年', value: 'all'}]} period={d.assetPeriod} onPeriodChange={period => invoke('changeAssetPeriod', {dataset: {period}})}
        caliber={<span>年度合同额按当前范围客户汇总；确收、回款按财务登记口径，点卡片看明细。<Button type="link" size="small" onClick={() => invoke('assetHelp')}>完整口径</Button></span>}/>
    </section>
    {(d.assetError || d.acvError) && <Alert type="warning" showIcon title="部分资产数据暂不可用" description={d.assetError || d.acvError} action={<Button size="small" onClick={() => invoke('loadAssets')}>重试</Button>}/>}
    <section className="ds-panel ds-customer-workbench" aria-label="客户经营">
    <section className="ds-customer-tools" aria-label="客户筛选">
      <div className="ds-customer-search"><SbSearch value={d.keyword || ''} onChange={value => change('search', {detail: {value}})} placeholder="搜索客户或负责人" loading={d.mapLoading}/></div>
      <div className="ds-customer-filters">
        <Filter label="象限" options={d.quadrantOptions} index={d.quadrantIndex} onChange={picker('changeQuadrant')}/>
        <Filter label="计划" options={d.planOptions} index={d.planIndex} onChange={picker('changePlan')}/>
        <SbLabeledSelect label="优先级" mode="multiple" value={d.mapSelectedLevels || []} options={d.mapLevelOptions || []} onChange={values => chooseLevels(values || [])}/>
        <Filter label="金额" options={d.mapAmountOptions} index={d.mapAmountIndex} onChange={picker('changeMapAmount')}/>
        {d.canViewTeam && !d.isFde && <Filter label="团队" options={d.teamOptions} index={d.teamIndex} onChange={picker('changeTeam')} disabled={d.directoryLoading || !!d.directoryError}/>}
        {!d.isFde && d.canViewTeam && <Filter label="成员" options={d.memberOptions} index={d.memberIndex} onChange={picker('changeMember')} disabled={d.directoryLoading || !!d.directoryError}/>}
        {d.isFde && d.canViewTeam && <SbLabeledSelect label="协助成员" mode="multiple" value={d.fdeMapMemberIds || []} options={(d.fdeMapMembers || []).filter(p => p.id).map(p => ({value: p.id, label: p.name}))} disabled={d.directoryLoading || !!d.directoryError} onChange={ids => change('changeFdeMapMember', {detail: {ids: ids || []}})}/>}
      </div>
      {(d.keyword || d.mapFilterActive) && <Button className="ds-customer-reset" type="link" size="small" onClick={reset}>清除筛选</Button>}
      {d.directoryError && <Alert type="warning" showIcon title={d.directoryError} action={<Button size="small" onClick={() => invoke('loadData')}>重试</Button>}/>}
    </section>
    <div className="ds-customer-workspace-wrap"><div className="ds-customer-workspace">
      <aside className="ds-customer-map-panel" aria-label="作战地图">
        <header><h2>{d.quadrantIndex ? d.mapQuadrant : '作战地图'}</h2><span className="ds-muted ds-customer-map-note">点色是状态，点大小是年度合同额档位</span></header>
        <SbBattleMap points={points} thresholds={{potential: 7, relationship: 7}} unrated={unrated} loading={d.mapLoading}
          zoomQuadrant={zoomQuadrant} onZoomChange={q => change('changeQuadrant', {detail: {value: q ? Math.max(0, (d.quadrantOptions || []).findIndex(o => o.label === QUADRANT_LABEL[q])) : 0}})}
          onPointClick={point => openDetail(point.id)} onClusterClick={setCluster}
          onUnratedClick={() => {setShowPending(true);changePage(1);}}
          emptyText={d.mapLoading ? '正在加载客户…' : '这个范围还没有客户。先在客户列表登记关系和潜力。'} emptyAction={{label: '清除筛选', onClick: reset}} />
      </aside>
      <section className="ds-customer-list" aria-label="客户列表">
        <header><h2>活跃客户 <span className="ds-customer-count">{d.mapLoading ? '—' : count}</span></h2><Button size="small" onClick={() => {setShowPending(!showPending);changePage(1);}}>{showPending ? '返回已定位客户' : `待评估 ${unrated} 家`}</Button>{d.canClaimCustomer && <Button type="primary" size="small" onClick={() => invoke('openCustomerClaim')}>客户认领</Button>}</header>
        <p className="ds-muted ds-sync-note">近 6 个月有有效跟进 · {showPending ? '未取得完整评分，不绘制地图点' : '仅展示已定位客户'}{d.activitySince ? ` · 起始 ${d.activitySince}` : ''}</p>
        <div className="ds-customer-table-viewport" ref={tableViewport}>
          <SbTable rowKey="id" density="compact" columns={columns} rows={customers.slice((currentPage - 1) * pageSize, currentPage * pageSize)} state={state}
            emptyTitle={state === 'error' ? '加载失败' : '没有匹配的客户'} emptyDescription={state === 'error' ? d.mapError : undefined} onRetry={() => invoke('loadData')} onClear={reset}
            actions={row => <Button type="link" size="small" aria-label={`查看${row.name}`} onClick={() => openDetail(row.id)}>查看</Button>} actionsWidth={72} scrollX={540}
            pagination={{current: currentPage, pageSize, total: count, onChange: changePage}}
            expandable={{columnWidth: 28, expandedRowRender: row => <div className="ds-customer-expanded"><strong>{row.name}</strong><p>{row.team} · {row.owner}{row.level && ` · ${row.level}`}</p>{d.isFde && !!row.fde_members?.length && <p>协助：{row.fde_members.map(person => person.name).join('、')}</p>}<p>所属象限：{row.quadrant} · 客户潜力：{row.potential ?? '未填写'} · 关系深度：{row.relationship ?? '未填写'}</p>{row.risk && <p>{row.risk}</p>}</div>}}/>
        </div>
      </section>
    </div></div>
    </section>
    <Modal title="这个位置有几家客户" open={!!cluster?.length} footer={null} onCancel={() => setCluster(null)}>
      {(cluster || []).map(p => <Button className="ds-map-candidate" key={p.id} block onClick={() => { setCluster(null); openDetail(p.id); }}>{p.name}<span className="ds-muted"> · {p.summary}</span></Button>)}
    </Modal>
  </section>;
}
