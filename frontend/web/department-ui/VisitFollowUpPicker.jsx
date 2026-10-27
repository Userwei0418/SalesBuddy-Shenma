import React from 'react';
import {Alert, Button, Segmented} from 'antd';
import {SbStatePanel} from '@shandiant/ui-react';
import {WorkflowDialog} from './WorkflowOverlays.jsx';
import {SbSearch} from './ImeInput.jsx';

export default function VisitFollowUpPicker({data: d, invoke}) {
  const busy = Boolean(d.isProcessing || d.isRecording || d.followUpChecking);
  const searching = Boolean(d.followUpLoading || d.followUpSearchPending);
  const customers = d.followUpPickerMode === 'customers';
  const selectedCustomer = Boolean(d.followUpCustomerId && d.followUpCustomerName);
  const directCustomer = selectedCustomer && d.followUpCanCompanyCustomer;
  const items = d.followUpItems || [];
  return <WorkflowDialog width={760} open={Boolean(d.followUpPickerOpen)}
    title={selectedCustomer ? '选择跟进方式' : customers ? '选择跟进客户' : '选择跟进商机'}
    onClose={busy ? undefined : () => invoke('closeFollowUpDiscovery')}
    footer={<>
      {d.followUpCanCustomerOnly && <Button className="ds-followup-full-entry" disabled={busy} type="link"
        title="可补充客户资料和参与同事；切换后需重新选择客户"
        onClick={() => invoke('returnToCustomerFollowUp')}>完整拜访记录</Button>}
      <Button disabled={busy} onClick={() => invoke('closeFollowUpDiscovery')}>关闭</Button>
    </>}>
    <div className="ds-followup-picker">
      <p className="ds-muted">{selectedCustomer
        ? directCustomer ? '可直接记录客户跟进，也可选择下方商机关联。' : '请选择本次要跟进的商机。'
        : '先选择客户，再决定是否关联商机。也可直接搜索商机。'}</p>
      {d.followUpCompanyPicker && <Segmented disabled={busy}
        value={selectedCustomer ? 'customers' : d.followUpPickerMode}
        options={[{label: '按客户选择', value: 'customers'}, ...(d.followUpCanCompanyOpportunity ? [{label: '直接选商机', value: 'opportunities'}] : [])]}
        onChange={mode => invoke('changeFollowUpPickerMode', {dataset: {mode}})} />}
      {selectedCustomer && <div className="ds-followup-customer">
        <div className="ds-followup-customer-main"><span className="ds-muted">已选客户</span><b>{d.followUpCustomerName}</b></div>
        <div className="ds-followup-customer-actions">
          <Button disabled={busy} onClick={() => invoke('changeFollowUpPickerMode', {dataset: {mode: 'customers'}})}>更换客户</Button>
          {directCustomer && <Button type="primary" loading={Boolean(d.followUpChecking)} disabled={busy}
            onClick={() => invoke('confirmFollowUpCustomer')}>直接跟进客户</Button>}
        </div>
      </div>}
      <SbSearch aria-label={customers ? '搜索跟进客户' : '搜索跟进商机'} value={d.followUpQuery || ''}
        placeholder={customers ? '输入客户名称，自动搜索' : selectedCustomer ? '搜索该客户下的商机' : '输入商机名称，自动搜索'}
        disabled={busy}
        onChange={value => invoke('inputFollowUpQuery', {detail: {value, autoSearch: true}})}
        onSearch={() => invoke('searchFollowUp')} />
      {d.followUpError && <Alert type="warning" showIcon message={d.followUpError}
        action={<Button disabled={busy} onClick={() => invoke('searchFollowUp')}>重新读取</Button>} />}
      <div className="ds-followup-results" aria-busy={searching}>
        <div className="ds-followup-results-heading"><b>{selectedCustomer ? directCustomer ? '关联商机（可选）' : '选择商机' : d.followUpQuery ? '搜索结果' : '最近跟进优先'}</b>
          <span className="ds-muted">{searching ? '搜索中…' : `${items.length}${d.followUpMore ? '+' : ''} 项`}</span></div>
        {customers ? items.map(row => <button type="button" className="ds-followup-row" key={row.id}
          disabled={busy} onClick={() => invoke('chooseFollowUpCustomer', {dataset: {id: row.id, showOpportunities: true}})}>
          <span className="ds-followup-row-name">{row.name}</span>
          <span className="ds-followup-row-summary">{d.followUpCanCompanyCustomer ? d.followUpCanCompanyOpportunity ? '选择后可直接跟进，或关联商机' : '直接记录客户跟进' : '选择该客户下的商机'}</span>
        </button>) : items.map(row => <button type="button" className="ds-followup-row" key={row.opportunity_id}
          disabled={busy} onClick={() => invoke('chooseFollowUp', {dataset: {id: row.opportunity_id}})}>
          <span className="ds-followup-row-name">{row.name}</span><span className="ds-followup-row-summary">{row.customer_reference.name}</span>
        </button>)}
        {searching && <p className="ds-followup-status ds-muted" role="status">正在搜索{customers ? '客户' : '商机'}…</p>}
        {!searching && !d.followUpError && !items.length && <SbStatePanel state="empty"
          title={selectedCustomer ? d.followUpQuery ? '没有匹配商机' : '该客户暂无商机' : customers ? '没有匹配客户' : '没有匹配商机'}
          description={directCustomer ? '可点击上方“直接跟进客户”，无需关联商机。' : '请调整关键词后重试。'} />}
        {d.followUpMore && <Button className="ds-followup-more" disabled={busy || searching} loading={d.followUpLoading}
          onClick={() => invoke('searchFollowUp', {dataset: {more: true}})}>加载更多</Button>}
      </div>
      {d.followUpChecking && <p role="status">正在核对所选目标的跟进权限…</p>}
      {d.followUpOnly && d.opportunityResolveError && d.opportunityResolveError !== '请确认本次跟进目标' && !d.followUpChecking && <Alert type="warning" showIcon message={d.opportunityResolveError}
        description="原文和草稿已保留，可重新选择有权跟进的客户或商机。" />}
    </div>
  </WorkflowDialog>;
}
