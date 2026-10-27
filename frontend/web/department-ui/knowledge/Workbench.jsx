import React, {useState} from 'react';
import {Alert, Button, Pagination, Segmented, Select, Table} from 'antd';
import {Input} from '../ImeInput.jsx';
import {Empty2, Kpi, LoadState, Panel, Tag2} from '../pc/ui.jsx';
import {useKnowledgeRead} from './hooks.js';
import {CATEGORIES, SCOPES, countText, formatObservedAt, materialStateLabel, optionLabel, productFilterOptions, sourceStatusLabel, stateLabel} from './model.js';
import {DonutChart, TrendChart, ProductChart} from './KnowledgeCharts.jsx';

export const LIBRARIES = [{value: 'all', label: '全部可见'}, {value: 'personal', label: '我的个人库'}, {value: 'team', label: '团队库'}, {value: 'shared', label: '共享给我'}];
export function LibraryFilter({value, onChange, disabled}) {return <Segmented value={value} onChange={onChange} options={LIBRARIES.map(row => ({...row, disabled: disabled && row.value !== 'all'}))} />;}
export function KnowledgeDashboard({service, onDirectory}) {
  const [filters, setFilters] = useState({scope: 'all', period: 'all'});
  const update = patch => setFilters(current => ({...current, ...patch}));
  const stats = useKnowledgeRead(() => service.getStats(filters), [service, JSON.stringify(filters)]);
  const categories = stats.value?.categories || [], products = stats.value?.products || [];
  return <><div className="kb122-toolbar"><LibraryFilter disabled={service.features?.scope === false} value={filters.scope} onChange={scope => update({scope})} /><Button onClick={stats.reload}>刷新统计</Button></div><div className="kb122-filters"><Select aria-label="看板内容类别" allowClear placeholder="全部类别" options={CATEGORIES} value={filters.content_category} onChange={content_category => update({content_category})} /><Select aria-label="看板产品" allowClear placeholder="全部产品" options={productFilterOptions(products)} value={filters.product_code} onChange={product_code => update({product_code})} /><Select aria-label="看板时间" value={filters.period} onChange={period => update({period})} options={[{value: 'all', label: '全部收录'}, {value: '30d', label: '近 30 天新增'}]} /></div>
    <LoadState loading={stats.loading} error={stats.error} onRetry={stats.reload}>
      {stats.value && <>
        <div className="kb122-kpis kb122-summary-cards">{[['total', '可见材料', {}, `${products.length} 个产品`], ['external', '可对客或公开', {external: true}, '按材料使用范围统计'], ['contributors', '参与贡献', null, `我贡献了 ${countText(stats.value.metrics?.mine)} 份`]].map(([key, label, drill, hint]) => <Kpi key={key} label={label} value={countText(stats.value.metrics?.[key])} hint={hint} onClick={drill ? () => onDirectory({...filters, ...drill}) : undefined} />)}</div>
        <div className="kb122-two kb122-charts"><Panel title="材料类别占比" note="点击图例查看材料"><DonutChart title="类别统计" rows={categories.map(row=>({...row,label:row.label || optionLabel(CATEGORIES,row.code)}))} onSelect={row=>onDirectory({...filters,content_category:row.code})}/></Panel>
          <Panel title="材料累计收录趋势" note="近 30 天 · 北京时间"><TrendChart rows={stats.value.trend || []}/></Panel>
          <Panel title="产品资料分布" note="单位：份"><ProductChart rows={products} onSelect={row=>onDirectory({...filters,product_code:row.code})}/><p className="pc-muted">按产品目录顺序展示</p></Panel>
          <Panel title="成员贡献构成" note="按材料去重"><DonutChart title="成员贡献" rows={(stats.value.contributions || []).map(row=>({...row,code:row.code || row.contributor_user_id,label:row.label || row.contributor_name || '未填写'}))} onSelect={row=>onDirectory({...filters,contributor_code:row.code})}/><p className="pc-muted">附件和历史版本不重复计数；导入材料按原提供人归属。</p></Panel></div>
        <p className="pc-muted">统计于 {formatObservedAt(stats.value.observed_at)} · 仅包含当前有权查看的材料。可对客或公开表示材料用途，分享仍需通过发布与权限校验。</p>
      </>}
    </LoadState></>;
}
export function MaterialDirectory({service, refresh, initialFilters, onOpen, onContribute}) {
  const [filters, setFilters] = useState({scope: 'all', q: '', ...initialFilters}), [queryDraft, setQueryDraft] = useState(initialFilters.q || ''), [offset, setOffset] = useState(0);
  const update = patch => {setFilters(current => ({...current, ...patch})); setOffset(0);};
  const commitQuery = () => update({q: queryDraft.trim()});
  const data = useKnowledgeRead(() => service.listMaterials({...filters, offset, page_size: 20}), [service, JSON.stringify(filters), offset, refresh]);
  return <>
    <div className="kb122-toolbar"><LibraryFilter disabled={service.features?.scope === false} value={filters.scope} onChange={scope => update({scope})} /><Button type="primary" disabled={data.value?.capabilities?.create !== true} onClick={onContribute}>贡献资料</Button></div>
    {service.features?.filters === false && <p className="pc-muted">当前展示全部可见资料；多维筛选接入后开放。</p>}<div className="kb122-filters"><Input.Search disabled={service.features?.filters === false} aria-label="搜索资料" placeholder="搜索资料名称、产品或贡献人" maxLength={200} value={queryDraft} onChange={event => setQueryDraft(event.target.value)} onSearch={commitQuery} allowClear />
      <Select disabled={service.features?.filters === false} aria-label="筛选内容类别" placeholder="全部类别" allowClear value={filters.content_category} options={CATEGORIES} onChange={content_category => update({content_category})} />
      <Select disabled={service.features?.filters === false} aria-label="筛选产品" placeholder="全部产品" allowClear value={filters.product_code} options={productFilterOptions(data.value?.product_options)} onChange={product_code => update({product_code})} />
      <Select disabled={service.features?.filters === false} aria-label="筛选处理状态" placeholder="全部状态" allowClear value={filters.state} options={['saved', 'syncing', 'parsing', 'retrievable', 'failed'].map(value => ({value, label: stateLabel(value)}))} onChange={state => update({state})} />
      <Button onClick={data.reload}>刷新目录</Button></div>
    {(filters.external || filters.mine || filters.contributor_code) && <p className="pc-muted">{filters.external ? '可对客或公开材料' : filters.mine ? '我贡献的材料' : '指定贡献人的材料'} <Button type="link" onClick={() => update({external: undefined, mine: undefined, contributor_code: undefined})}>清除筛选</Button></p>}
    {filters.period === '30d'  && <p className="pc-muted">近 30 天新增 <Button type="link" onClick={() => update({period: undefined})}>清除时间筛选</Button></p>}
    {filters.retrievable === true && <p className="pc-muted">当前检索版本可用 <Button type="link" onClick={() => update({retrievable: undefined})}>清除检索筛选</Button></p>}<LoadState loading={data.loading} error={data.error} onRetry={data.reload}>
      {data.value && <Panel title="资料目录" note={data.value.scope_label || '仅展示当前可读资料'} bodyClassName="is-flush"><Table rowKey="material_id" pagination={false} dataSource={data.value.items || []} scroll={{x: 1050}}
        locale={{emptyText: <Empty2 title="当前条件下没有可见资料" description="可调整筛选，或贡献第一份资料。" />}}
        columns={[
          {title: '内容名称 / 版本', dataIndex: 'content_name', width: 300, render: (value, row) => <><button className="pc-link" onClick={() => onOpen(row.material_id)}>{row.source?.original_name || value}</button><br/><small className="pc-muted">{row.version_no ? `V${row.version_no}` : '版本待确认'}{row.source ? ' · 飞书弹药库导入' : ''}</small></>},
          {title: '产品 / 类别', render: (_, row) => <>{row.source?.product_label || row.product_label || row.product_code}<br/><small>{row.source?.category_label || optionLabel(CATEGORIES, row.content_category)}</small></>},
          {title: '使用范围', dataIndex: 'content_scope', render: value => optionLabel(SCOPES, value)},
          {title: '客户 / 行业', render: (_, row) => <>{row.customer_name || '—'}<br/><small>{row.industry_label || row.industry_code || '—'}</small></>},
          {title: '版本 / 状态', render: (_, row) => <>{row.source && <><small>原表：{sourceStatusLabel(row.source.source_status)}</small><br/></>}<Tag2 tone={row.state === 'failed' ? 'danger' : row.state === 'retrievable' ? 'success' : 'neutral'}>{row.source ? '系统：' : ''}{materialStateLabel(row.state, row.source)}</Tag2><br/><small>{row.version_no ? `V${row.version_no}` : '版本待确认'}{row.active_version_state === 'retrievable' && row.active_version_id !== row.version_id ? ' · 旧版仍可检索' : ''}{row.source && (row.source.validation_issues?.length ? ' · 待补全' : ' · 不可检索')}</small></>},
          {title: '共享范围', dataIndex: 'sharing_label', render: value => value || '当前授权'},
          {title: '提供人 / 更新', render: (_, row) => <>{row.contributor_label || (row.source ? row.source.provider_name || '原表未填写' : row.contributor_name || '—')}<br/><small>{formatObservedAt(row.updated_at)}</small></>},
          {title: '操作', render: (_, row) => <Button type="link" onClick={() => onOpen(row.material_id)}>浏览 / 下载</Button>},
        ]} /><div className="kb122-pagination">{Number.isInteger(data.value.total) ? <Pagination current={offset / 20 + 1} pageSize={20} total={data.value.total} showSizeChanger={false} onChange={page => setOffset((page - 1) * 20)} /> : <><Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</Button><Button disabled={!data.value.has_more} onClick={() => setOffset(data.value.next_offset ?? offset + 20)}>下一页</Button></>}</div></Panel>}
    </LoadState></>;
}
