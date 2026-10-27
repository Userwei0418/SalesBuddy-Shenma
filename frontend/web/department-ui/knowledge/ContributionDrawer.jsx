import React, {useMemo, useRef, useState} from 'react';
import {Alert, Button, Drawer, Select, Upload} from 'antd';
import {Input} from '../ImeInput.jsx';
import {identity} from '../pc/data.js';
import {LoadState} from '../pc/ui.jsx';
import {useKnowledgeRead} from './hooks.js';
import {CATEGORIES, SCOPES, activeOptions, changeCategory, contributionForm, contributionPayload, contributionCategoryAllowed, contributionCategoryOptions, importedSource, needsIndustry, newRequestKey, previewName, sourceStatusLabel, validateCatalog} from './model.js';
import {assertDetail, canManageContributionCatalog, errorText} from './service.js';
import {completeUploadIntent, prepareUploadIntent} from './upload-intent.js';

export function ReferencePicker({service, kind, value, onChange, disabled}) {
  const [query, setQuery] = useState('');
  const state = useKnowledgeRead(() => kind === 'customer' ? service.searchCustomers(query) : service.searchOpportunities(query), [service, kind, query], !disabled);
  const options = (state.value?.items || []).map(row => ({value: row.id, label: row.name, row}));
  if (value?.id && !options.some(option => option.value === value.id)) options.unshift({value: value.id, label: value.name, row: value});
  return <><Select showSearch allowClear filterOption={false} value={value?.id || undefined} onSearch={setQuery} disabled={disabled}
    aria-label={kind === 'customer' ? 'CRM 标准客户' : 'CRM 关联商机'} placeholder={kind === 'customer' ? '搜索并选择 CRM 标准客户' : '搜索并选择 CRM 商机（选填）'}
    options={options} loading={state.loading} notFoundContent={state.error || (state.loading ? '正在查询…' : '没有可选记录')}
    onChange={id => { if (!state.loading && !state.error) onChange(options.find(option => option.value === id)?.row || null); }} />
    {state.error && <span role="alert" className="pc-error">{state.error} <Button type="link" size="small" onClick={state.reload}>重新查询</Button></span>}</>;
}

function Field({label, required, error, wide, children}) {
  return <div className={`kb122-field${wide ? ' is-wide' : ''}`}><label>{label}{required && <span aria-label="必填"> *</span>}</label>{children}{error && <small role="alert" className="pc-error">{error}</small>}</div>;
}

export default function ContributionDrawer({service, target, onClose, onSaved}) {
  const actor = identity(), mountedActor = useRef(actor), flight = useRef(false), request = useRef(null), uploadFlight = useRef(false);
  const latest = [...(target?.versions || [])].sort((a, b) => b.version_no - a.version_no)[0];
  const initial = contributionForm(latest);
  const [form, setForm] = useState(initial);
  const [customer, setCustomer] = useState(initial.customer_id ? {id: initial.customer_id, name: latest.contribution.customer_name} : null);
  const [opportunity, setOpportunity] = useState(initial.opportunity_id ? {id: initial.opportunity_id, name: latest.contribution.opportunity_name} : null);
  const [files, setFiles] = useState(latest?.files || []), [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false), [errors, setErrors] = useState({}), [error, setError] = useState(''), [conflicts, setConflicts] = useState([]), [versionTarget, setVersionTarget] = useState(target);
  const [savedId, setSavedId] = useState(null), [catalogConfirmed, setCatalogConfirmed] = useState(true);
  const [retryUpload, setRetryUpload] = useState(null), [categoryDeniedByServer, setCategoryDeniedByServer] = useState(false);
  const canManageCatalog = !categoryDeniedByServer && canManageContributionCatalog();
  const categoryAllowed = contributionCategoryAllowed(form.content_category, canManageCatalog);
  const categoryRestricted = Boolean(form.content_category) && !categoryAllowed;
  const imported = importedSource([...(versionTarget?.versions || [])].sort((a, b) => b.version_no - a.version_no)[0]?.contribution);
  const catalogRead = useKnowledgeRead(async () => validateCatalog(await service.getCatalog()), [service]);
  const catalog = catalogRead.value;
  const locked = busy || uploading || Boolean(savedId);
  const name = useMemo(() => previewName(form, catalog, customer), [form, catalog, customer]);
  const patch = values => {setForm(current => ({...current, ...values})); setErrors({}); setError(''); setConflicts([]);};
  const current = () => mountedActor.current === identity();
  const select = (field, group, label) => <Select aria-label={label} value={form[field] || undefined} options={activeOptions(catalog?.[group])} placeholder={`请选择${label}`} onChange={value => patch({[field]: value})} disabled={locked} />;

  const save = async () => {
    if (flight.current || !current() || !catalogConfirmed) return;
    if (!savedId && categoryRestricted) { setErrors({content_category: '请选择会议演讲或客户方案，或联系目录维护人处理原类别'}); return; }
    let payload;
    try { payload = contributionPayload(form, catalog); } catch (failure) { setErrors(failure.fields || {}); setError(errorText(failure)); return; }
    flight.current = true; setBusy(true); setError('');
    try {
      const fingerprint = JSON.stringify([versionTarget?.material_id, versionTarget?.material_revision, payload]);
      if (request.current?.fingerprint !== fingerprint) request.current = {fingerprint, key: newRequestKey()};
      const result = savedId ? {material_id: savedId} : versionTarget
        ? await service.addVersion(versionTarget.material_id, {expected_material_revision: versionTarget.material_revision, contribution: payload}, request.current.key)
        : await service.createMaterial(payload, request.current.key);
      if (!current()) return;
      if (!result?.material_id) throw Error('服务端未返回材料保存回执，请使用相同表单重试确认');
      setSavedId(result.material_id);
      const detail = assertDetail(await service.getMaterial(result.material_id), result.material_id);
      if (current()) onSaved(detail);
    } catch (failure) {
      if (!current()) return;
      setError(errorText(failure));
      if (failure.code === 'KNOWLEDGE_CONTRIBUTION_CATEGORY_DENIED') setCategoryDeniedByServer(true);
      if (failure.code === 'KNOWLEDGE_NAME_CONFLICT') setConflicts(failure.candidates || failure.details?.candidates || []);
      if (['KNOWLEDGE_CATALOG_CHANGED', 'KNOWLEDGE_CATALOG_REVISION_CONFLICT'].includes(failure.code)) {setCatalogConfirmed(false); catalogRead.reload();}
    } finally {flight.current = false; if (current()) setBusy(false);}
  };
  const upload = async file => {
    if (flight.current || uploadFlight.current || !current()) return false;
    uploadFlight.current = true;
    setUploading(true); setError('');
    try {
      const intent = await prepareUploadIntent(file, actor);
      if (!current()) return false;
      const stored = await service.uploadFile(file, intent.key);
      if (!current()) return false;
      if (!stored?.file_id) throw Error('附件尚未取得归档回执');
      completeUploadIntent(intent); setRetryUpload(null);
      setFiles(rows => [...rows, stored]);
      setForm(current => ({...current, content: {...current.content, file_ids: [...current.content.file_ids, stored.file_id]}}));
    } catch (failure) { if (current()) {setError(errorText(failure)); setRetryUpload(file);} }
    finally {uploadFlight.current = false; if (current()) setUploading(false); }
    return false;
  };
  const chooseVersion = async row => {
    setBusy(true); setError('');
    try { const detail = assertDetail(await service.getMaterial(row.material_id), row.material_id); if (!current()) return;
      if (detail.capabilities?.edit !== true) throw Error('当前没有该材料的编辑权限');
      setVersionTarget(detail); setConflicts([]); request.current = null;
    } catch (failure) { if (current()) setError(errorText(failure)); } finally { if (current()) setBusy(false); }
  };
  return <Drawer open width={760} title={versionTarget ? '为已有材料新增版本' : '贡献资料'} rootClassName="department-ui kb122-drawer" onClose={() => {if (!busy && !uploading) onClose();}}
    footer={<div className="kb122-drawer-actions"><Button disabled={busy || uploading} onClick={onClose}>取消</Button><Button type="primary" loading={busy} disabled={!catalog || uploading || !catalogConfirmed || (!savedId && categoryRestricted)} onClick={save}>{savedId ? '重新读取保存结果' : versionTarget ? '确认新增版本并保存' : '保存材料'}</Button></div>}>
    <LoadState loading={catalogRead.loading} error={catalogRead.error} onRetry={catalogRead.reload}>
      {catalog && <>
        {error && <Alert showIcon type="error" title={error} />}
        {retryUpload && <Alert type="warning" title={`附件上传结果未确认：${retryUpload.name}`} description={<><p>重试将保留相同文件和上传标识，避免重复归档。</p><Button disabled={busy || uploading} onClick={() => upload(retryUpload)}>重试该附件</Button></>} />}
        {savedId && <Alert type="info" showIcon title="已取得保存回执，正在确认回读" description="回读成功后将显示材料版本；请勿重复新建。" />}
        {!catalogConfirmed && <Alert type="warning" title="目录已变化，请核对所选字段" description={<Button onClick={() => setCatalogConfirmed(true)}>我已核对当前目录，继续编辑</Button>} />}
        {versionTarget && <Alert type="info" title={`更新目标：${versionTarget.content_name || versionTarget.material_id}`} description="新版本保存后，原当前版本仍保留到新版本通过检索验证。" />}
        {imported && <Alert type="warning" showIcon title="补齐字段后保存为新版本" description={<><p>原表名称：{imported.original_name} · 原表状态：{sourceStatusLabel(imported.source_status)}</p><p>原产品：{imported.product_label}。请重新选择系统产品，并补齐当前类别的必填信息；原导入版本和来源信息会保留，不会被本表单覆盖。</p>{imported.attachments?.length > 0 && <p>原表附件尚未归档：{imported.attachments.map(row => row.name).join('、')}。如需使用附件，请取得文件后上传；附件描述不能直接用于下载或检索。</p>}</>} />}
        {conflicts.length > 0 && <section className="kb122-conflicts"><h3>存在同名资料</h3><p>请选择有编辑权限的资料明确新增版本，或修改人工补充后再新建。</p>{conflicts.map(row => <div key={row.material_id}><span>{row.content_name}</span><Button disabled={busy || row.capabilities?.edit !== true} onClick={() => chooseVersion(row)}>选择此资料新增版本</Button></div>)}</section>}
        <fieldset disabled={busy || uploading || Boolean(savedId)} className="kb122-form">
          <Field label="内容名称" wide><output className="kb122-name">{name || '填写后自动预览'}</output><small>名称由服务端按当前目录和 CRM 标准名称生成，本预览仅供核对。</small></Field>
          <Field label="内容类别" required error={errors.content_category}><Select aria-label="内容类别" value={form.content_category || undefined} options={contributionCategoryOptions(canManageCatalog, form.content_category)} placeholder="请选择内容类别" disabled={locked} onChange={value => {setForm(current => changeCategory(current, value)); if (value !== 'customer_solution') setCustomer(null); setErrors({}); setError(''); setConflicts([]);}} />
            {!canManageCatalog && <small>可贡献会议演讲、客户方案；其他类别由目录维护人管理。</small>}
            {categoryRestricted && <small role="alert" className="pc-error">{versionTarget ? '原类别为' : '当前类别为'}{CATEGORIES.find(row => row.value === form.content_category)?.label || '未识别类别'}，当前不能以此类别{versionTarget ? '新增版本' : '提交资料'}。请选择允许的类别或联系目录维护人{versionTarget ? '；历史版本保持不变。' : '。'}</small>}
          </Field>
          <Field label="内容范围" required error={errors.content_scope}><Select aria-label="内容范围" value={form.content_scope || undefined} options={SCOPES} placeholder="请选择内容范围" onChange={value => patch({content_scope: value})} disabled={locked} /><small>业务使用受众，不决定系统共享权限。</small></Field>
          <Field label="产品" required error={errors.product_code}>{select('product_code', 'products', '产品')}</Field>
          {needsIndustry(form.content_category) && <Field label="行业" required error={errors.industry_code}>{select('industry_code', 'industries', '行业')}</Field>}
          {form.content_category === 'customer_solution' && <>
            <Field label="客户名称" required wide error={errors.customer_id}><ReferencePicker service={service} kind="customer" value={customer} onChange={row => {setCustomer(row); patch({customer_id: row?.id || null});}} disabled={locked} /></Field>
            <Field label="汇报对象岗位" required error={errors.reporting_position_code}>{select('reporting_position_code', 'reporting_positions', '汇报对象岗位')}</Field>
            <Field label="汇报对象职级" required error={errors.reporting_level_code}>{select('reporting_level_code', 'reporting_levels', '汇报对象职级')}</Field>
            <Field label="汇报对象决策角色" required error={errors.decision_role_code}>{select('decision_role_code', 'decision_roles', '汇报对象决策角色')}</Field>
          </>}
          <Field label="关联商机" wide error={errors.opportunity_id}><ReferencePicker service={service} kind="opportunity" value={opportunity} onChange={row => {setOpportunity(row); patch({opportunity_id: row?.id || null});}} disabled={locked} /></Field>
          <Field label="人工补充" wide error={errors.supplementary_text}><Input aria-label="人工补充" value={form.supplementary_text} maxLength={300} onChange={event => patch({supplementary_text: event.target.value})} placeholder="补充用途或阶段，区分不同资料" /></Field>
          <Field label="内容材料" required wide error={errors.content}>
            <Upload beforeUpload={upload} showUploadList={false} disabled={locked || service.features?.upload === false}><Button loading={uploading} disabled={locked || service.features?.upload === false}>上传附件</Button></Upload><small>单个附件不超过 50 MiB。</small>{service.features?.upload === false && <small>附件上传正在接入，可先使用链接贡献资料。</small>}
            {files.map(file => <div className="kb122-file-row" key={file.file_id}><span>{file.filename || file.file_name || file.file_id} · 已归档</span><Button type="link" disabled={busy} onClick={() => {setFiles(rows => rows.filter(row => row.file_id !== file.file_id)); patch({content: {...form.content, file_ids: form.content.file_ids.filter(id => id !== file.file_id)}});}}>移除</Button></div>)}
            <Input.TextArea aria-label="材料链接" rows={3} value={form.content.links.join('\n')} onChange={event => patch({content: {...form.content, links: event.target.value.split('\n')}})} onBlur={() => patch({content: {...form.content, links: form.content.links.map(link => link.trim()).filter(Boolean)}})} placeholder="或粘贴 HTTP(S) 链接，每行一条；附件或链接至少一种" />
            <small>附件保存后按服务配置进入解析；仅保存链接不会自动抓取或变成可检索资料。处理状态与重试入口可在材料详情查看。</small>
          </Field>
          <Field label="上传日期 / 内容贡献人" wide><p className="pc-muted">保存时由系统记录服务端时间及当前操作人。</p></Field>
          <Field label="备注" wide error={errors.remarks}><Input.TextArea aria-label="备注" value={form.remarks} maxLength={2000} rows={3} onChange={event => patch({remarks: event.target.value})} /></Field>
        </fieldset>
        <Alert type="info" title="新材料默认本人可见" description="保存并回读后可独立设置团队或指定成员共享；内容范围、客户及商机关联不会自动授予访问权。" />
      </>}
    </LoadState>
  </Drawer>;
}
