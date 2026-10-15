import React, {useEffect, useState} from 'react';
import {Alert, Button, Checkbox, Modal, Tag} from 'antd';
import {Input} from './ImeInput.jsx';
import {can} from './pc/data.js';
import './customer-identity.css';

const LABELS = {customer: '客户', partner: '合作伙伴'};
export function BusinessIdentityTags({values}) {
  const roles = Array.isArray(values) ? values : ['customer'];
  return <span className="ds-business-tags">{Object.entries(LABELS).filter(([key]) => roles.includes(key)).map(([key, label]) => <Tag key={key} color={key === 'partner' ? 'purple' : 'blue'}>{label}</Tag>)}</span>;
}

// Shared Page owns identity changes, directory access, stable IDs and payloads.
export default function CustomerIdentityFields({data: d, invoke, disabled = false}) {
  const call = (name, payload = {}) => invoke(name, payload);
  const [query, setQuery] = useState('');
  const [identities, setIdentities] = useState(d.businessIdentities || ['customer']);
  const busy = disabled || Boolean(d.submitting || d.saving);
  const canChoose = can('partner.read');
  const ownLocked = Boolean(d.initialPartnerProfileId);
  useEffect(() => {if (d.partnerPickerVisible) setQuery(d.partnerPickerQuery || '');}, [d.partnerPickerVisible]);
  useEffect(() => {setIdentities(d.businessIdentities || ['customer']);}, [JSON.stringify(d.businessIdentities)]);
  const picker = mode => call('openPartnerPicker', {dataset: {mode}});
  const clear = mode => call('clearIdentityPartner', {dataset: {mode}});
  const search = () => call('searchIdentityPartners', {detail: {value: query}});
  return <section className="ds-identity-fields" aria-label="业务身份与伙伴关联">
    <div className="ds-identity-heading"><b>业务身份</b><span className="ds-muted">可同时选择</span></div>
    <Checkbox.Group aria-label="业务身份" value={identities} disabled={busy || !d.identityOptionsReady}
      options={d.identityOptions || []} onChange={value => {if (value.length) setIdentities(value); call('changeBusinessIdentities', {detail: {value}});}} />
    {!d.identityOptionsReady && <p role="status" className="ds-muted">业务身份选项暂不可用，请刷新页面后重试。已填写内容会保留。</p>}
    {d.isCustomerIdentity === false && <p className="ds-identity-note">纯伙伴不填写客户类型，不纳入客户经营评分与作战地图。</p>}
    {(d.isPartnerIdentity || d.partnerProfileId) && <div className="ds-identity-link" aria-label="本企业伙伴档案">
      <div><b>本企业伙伴档案{!d.isPartnerIdentity ? '（历史关联）' : ''}</b>
        <span>{d.partnerProfileName || (d.partnerProfileId ? '已关联伙伴档案' : '未选择已有档案')}</span>
        <small>{ownLocked ? '已绑定的档案关系会保留。' : '如已有伙伴档案，请选择关联；未选择时将在正式建档时新建。'}</small></div>
      {!ownLocked && <div className="ds-identity-buttons"><Button disabled={busy || !canChoose} onClick={() => picker('profile')}>选择已有伙伴档案</Button>
        {d.partnerProfileId && <Button disabled={busy} onClick={() => clear('profile')}>取消选择</Button>}</div>}
    </div>}
    <div className="ds-identity-link" aria-label="关联合作伙伴">
      <div><b>关联合作伙伴 <small>选填</small></b><span>{d.associatedPartnerName || '未关联'}</span>
        <small>{d.associatedPartnerName && !d.associatedPartnerId ? '当前保留的是历史名称，选择档案后建立编号关联。' : '与本企业合作的其他伙伴，与本企业业务身份分别维护。'}</small></div>
      <div className="ds-identity-buttons"><Button disabled={busy || !canChoose} onClick={() => picker('associated')}>选择关联伙伴</Button>
        {(d.associatedPartnerId || d.associatedPartnerName) && <Button disabled={busy} onClick={() => clear('associated')}>清除关联</Button>}</div>
    </div>
    {!canChoose && <p className="ds-muted">当前账号无伙伴目录读取权限，需要关联已有档案时请联系运营。</p>}
    <Modal title={d.partnerPickerMode === 'profile' ? '选择本企业已有伙伴档案' : '选择关联合作伙伴'} open={Boolean(d.partnerPickerVisible)} footer={null}
      width={620} onCancel={() => call('closePartnerPicker')} destroyOnHidden>
      <div className="department-ui ds-partner-picker">
        <div className="ds-partner-search"><Input aria-label="搜索伙伴名称" value={query} onChange={e => setQuery(e.target.value)} onPressEnter={search} placeholder="输入伙伴名称" maxLength={100} allowClear /><Button onClick={search} loading={d.partnerPickerLoading}>搜索</Button></div>
        {d.partnerPickerError && <Alert type="error" showIcon title={d.partnerPickerError} action={<Button onClick={search}>重试</Button>} />}
        <div className="ds-partner-results" aria-label="可选伙伴档案">
          {(d.partnerPickerItems || []).map(row => <div className="ds-partner-result" key={row.id}>
            <div><b>{row.name}</b><small>{row.description} · 编号 {row.id}</small></div>
            <Button disabled={busy || d.partnerPickerLoading} onClick={() => call('chooseIdentityPartner', {dataset: {id: row.id}})}>选择</Button>
          </div>)}
          {!d.partnerPickerLoading && !d.partnerPickerError && !d.partnerPickerItems?.length && <p className="ds-muted">没有可关联的伙伴档案，请调整关键词。</p>}
          {d.partnerPickerLoading && <p role="status" className="ds-muted">正在读取伙伴目录…</p>}
        </div>
        {d.partnerPickerHasMore && <Button block disabled={d.partnerPickerLoading} onClick={() => call('moreIdentityPartners')}>加载更多</Button>}
      </div>
    </Modal>
  </section>;
}
