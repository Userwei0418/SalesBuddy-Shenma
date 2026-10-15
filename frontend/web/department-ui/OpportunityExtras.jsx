import React from 'react';
import {Alert, Button, Checkbox, Select} from 'antd';
import {SbField, SbSheet} from '@shandiant/ui-react';
import {Input, SbSearch} from './ImeInput.jsx';

// A presentation bridge only. The original shared component owns dirty fields,
// permission checks, directory identity guards and the single save transaction.
export default function OpportunityExtras({form, invokeOn, disabled, rosterControl}) {
  const f = form.data || {}, v = f.form || {};
  const on = (name, payload) => invokeOn(form, name, payload);
  const change = (field, value) => on('extraChange', {dataset: {key: field.key}, detail: {value}});
  return <div className="ds-oc-extras">
    {f.extraLoading && <p className="ds-muted">正在加载补充选项…</p>}
    {f.extraError && <Alert type="error" message={f.extraError} action={<Button onClick={() => on('loadExtraOptions')}>重试</Button>} />}
    {f.extraReady && <>
      <div className="ds-oc-grid">{(f.extraFields || []).map(field => <div key={field.key} className={field.type === 'multi_enum' || field.type === 'text' && field.maxLength > 500 ? 'ds-oc-extra-wide' : ''}><SbField label={field.label}>
        {field.type === 'multi_enum' ? <div className="ds-oc-extra-options">{field.options.map(option => <Checkbox key={option.value} disabled={disabled || field.disabled || option.legacy && !option.selected} checked={option.selected} onChange={() => on('extraToggleSource', {dataset: {key: field.key, value: option.value}})}>{option.label}</Checkbox>)}</div>
          : ['enum', 'boolean'].includes(field.type) ? <Select aria-label={field.label} disabled={disabled || field.disabled} value={field.value ?? '__empty__'} options={field.options.map(option => ({...option, value: option.value ?? '__empty__'}))} onChange={value => change(field, value === '__empty__' ? null : value)} style={{width: '100%'}} />
            : <Input.TextArea aria-label={field.label} value={field.value ?? ''} placeholder="选填" disabled={disabled || field.disabled} maxLength={field.maxLength} autoSize={{minRows: field.maxLength > 500 ? 3 : 2, maxRows: 8}} onChange={event => change(field, event.target.value)} />}
      </SbField></div>)}</div>
      <div className="ds-oc-extra-people"><h3>商机协作人员</h3>
        {rosterControl}
        <SbField label="主责 FDE" help="从长期协助名单中指定主责">
          <Select aria-label="主责 FDE" disabled={disabled || !f.extraCanManageFde} value={v.extra_values?.primary_fde_id || '__empty__'} options={(f.extraPrimaryOptions || []).map(option => ({...option, value: option.value ?? '__empty__'}))} onChange={value => on('extraPrimary', {detail: {value: value === '__empty__' ? null : value}})} style={{width: '100%'}} />
        </SbField>
        <SbField label="项目参与人员">
          {(f.extraParticipants || []).map(person => <div className="ds-oc-extra-person" key={person.user_id}><span>{person.label}{person.is_active === false ? '（已停用）' : ''} <small className="ds-muted">{person.roleLabel}</small></span>{f.extraCanEditParticipants && <Button size="small" type="text" disabled={disabled} onClick={() => on('removeExtraMember', {dataset: {id: person.user_id}})}>移除</Button>}</div>)}
          {!f.extraParticipants?.length && <span className="ds-muted">未设置参与人员</span>}
          {f.extraCanEditParticipants && <Button disabled={disabled} onClick={() => on('openExtraMembers')}>添加项目参与人员</Button>}
        </SbField>
      </div>
    </>}
    <SbSheet open={Boolean(f.extraMemberOpen)} title="添加项目参与人员" onClose={() => on('closeExtraMembers')} footer={<Button onClick={() => on('closeExtraMembers')}>完成</Button>}>
      <div className="ds-oc-picker">
        <SbField label="参与职责"><Select aria-label="参与职责" disabled={disabled} value={f.extraMemberRole ?? undefined} placeholder="先选择职责" options={f.extraParticipantRoles || []} onChange={value => on('extraMemberRole', {detail: {value}})} style={{width: '100%'}} /></SbField>
        <SbSearch value={f.extraMemberQuery || ''} disabled={disabled || !f.extraMemberRole} onChange={value => on('extraMemberSearch', {detail: {value}})} placeholder="搜索姓名或账号" />
        {!f.extraMemberRole && <p className="ds-muted">先选择本项目的参与职责，再选择成员。</p>}
        {(f.extraMemberRows || []).map(person => <div className="ds-oc-extra-person" key={person.id}><span><b>{person.name}</b> <small className="ds-muted">{person.account_code}</small></span><Button disabled={disabled || person.selected} size="small" onClick={() => on('chooseExtraMember', {dataset: {id: person.id}})}>{person.selected ? '已选择' : '选择'}</Button></div>)}
        {f.extraMemberLoading && <p className="ds-muted">正在读取成员…</p>}
        {f.extraMemberError && <Alert type="error" message={f.extraMemberError} action={<Button onClick={() => on('loadExtraMembers')}>重试</Button>} />}
        {!f.extraMemberLoading && !f.extraMemberError && f.extraMemberRole && !f.extraMemberRows?.length && <p className="ds-muted">没有找到可选成员</p>}
        {f.extraMemberMore && <Button disabled={f.extraMemberLoading} onClick={() => on('loadExtraMembers', {dataset: {more: true}})}>加载更多成员</Button>}
      </div>
    </SbSheet>
  </div>;
}
