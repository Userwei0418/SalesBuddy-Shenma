import React, {useEffect, useState} from 'react';
import {Button} from 'antd';
import {SbLabeledSelect, SbStatePanel, SbTable} from '@shandiant/ui-react';
import {SbSearch} from './ImeInput.jsx';
import {BusinessIdentityTags} from './CustomerIdentityFields.jsx';
import FilterBar from './pc/FilterBar.jsx';
import {PageHead, useAsync, useSessionState} from './pc/ui.jsx';
import {can, open} from './pc/data.js';
import {PAGE_SIZE_OPTIONS, useListPageSize} from './pc/list-pagination.js';
import {enterpriseDirectoryParams, IDENTITY_FILTERS, loadEnterpriseDirectory} from './pc/enterprise-directory.js';

export default function EnterpriseDirectory({page, viewControl}) {
  const allowed = can('customer.claim_directory');
  const [filters, setFilters] = useSessionState('enterprise-directory-v1', {q: '', businessIdentity: 'all', offset: 0}, page);
  const [draft, setDraft] = useState(filters.q);
  const [pageSize, setPageSize] = useListPageSize('enterprise-directory');
  const params = enterpriseDirectoryParams({...filters, pageSize});
  const result = useAsync(() => allowed ? loadEnterpriseDirectory(params) : null, [allowed, JSON.stringify(params)]);
  const response = result.value;
  const change = patch => setFilters(current => ({...current, ...patch, offset: 0}));
  useEffect(() => {
    if (draft === filters.q) return;
    const timer = setTimeout(() => change({q: draft}), 300);
    return () => clearTimeout(timer);
  }, [draft, filters.q]);
  useEffect(() => {
    if (response && filters.offset > 0 && filters.offset >= response.total) setFilters(current => ({...current, offset: Math.max(0, Math.ceil(response.total / pageSize) - 1) * pageSize}));
  }, [response, filters.offset, pageSize]);
  const clear = () => {setDraft(''); setFilters({q: '', businessIdentity: 'all', offset: 0});};
  const columns = [
    {title: '企业名称 / 业务身份', key: 'name', render: (_, row) => <div className="ds-sp-two"><b>{row.name}</b><BusinessIdentityTags values={row.business_identities} /></div>},
    {title: '所属团队', key: 'team', render: (_, row) => row.team_name || '未分配'},
    {title: '负责人', key: 'owner', render: (_, row) => row.owner_name || '未认领'},
    {title: '客户类型', key: 'type', render: (_, row) => row.business_identities.includes('customer') ? ({prospect: '潜在客户', opportunity: '商机客户', won: '已成单客户'}[row.customer_type_code] || row.customer_type_code || '未登记') : '不适用'},
  ];
  return <div className="pc-page pc-list-page pc-customers">
    <PageHead title="客户经营" extra={(can('customer.create_request') || can('customer.create')) && <Button type="primary" onClick={() => open.route('pages/customer-create/index')}>新建企业档案</Button>} />
    {viewControl}
    {!allowed ? <SbStatePanel state="empty" title="暂无企业目录查看权限" /> : <section className="pc-panel">
      <FilterBar label="企业档案筛选" search={<SbSearch value={draft} onChange={setDraft} placeholder="搜索企业名称" loading={result.loading} />} actions={<><Button onClick={clear}>重置</Button><Button onClick={result.reload}>刷新</Button></>}>
        <SbLabeledSelect label="业务身份" data-toolbar-width={230} width={230} value={filters.businessIdentity} options={IDENTITY_FILTERS} onChange={value => change({businessIdentity: value || 'all'})} />
      </FilterBar>
      <header className="pc-enterprise-heading"><h2>企业档案 <span className="ds-muted">{result.loading ? '读取中…' : response ? `${response.total} 家` : ''}</span></h2><span className="ds-muted">企业按档案去重，双身份只展示一行</span></header>
      <SbTable rowKey="id" columns={columns} rows={response?.items || []} scrollX={720}
        state={result.loading ? 'loading' : result.error ? 'error' : !response?.items.length ? 'empty' : 'normal'}
        emptyTitle={result.error ? '企业档案加载失败' : '没有匹配的企业档案'} emptyDescription={result.error} onRetry={result.reload} onClear={clear}
        actions={row => row.can_read_customer === true ? <Button type="link" onClick={() => open.customer(row.id)}>查看档案</Button> : <span className="ds-muted">仅目录可见</span>} actionsWidth={120}
        pagination={{current: Math.floor(filters.offset / pageSize) + 1, pageSize, total: response?.total || 0, pageSizeOptions: PAGE_SIZE_OPTIONS,
          onPageSizeChange: size => {setPageSize(size); change({});}, onChange: (number, size = pageSize) => {if (size !== pageSize) {setPageSize(size); change({});} else setFilters(current => ({...current, offset: (number - 1) * pageSize}));}}} />
    </section>}
  </div>;
}
