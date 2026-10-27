import React, {useEffect, useRef, useState} from 'react';
import {Alert, Button, Checkbox, Drawer, Segmented, Select, message} from 'antd';
import {identity} from '../pc/data.js';
import {Input} from '../ImeInput.jsx';
import {LoadState, Panel, Tag2} from '../pc/ui.jsx';
import {useKnowledgeRead} from './hooks.js';
import {CATEGORIES, SCOPES, aclGrants, contributionLinks, importedSource, importIssueText, materialStateLabel, newRequestKey, optionLabel, sourceStatusLabel} from './model.js';
import {assertDetail, authorizeMaterialLink, canManageContributionCatalog, errorText, openAuthorizedFile} from './service.js';
import FilePreview from './FilePreview.jsx';
import SourceArchive from './SourceArchive.jsx';
import {ingestionErrorText, ingestionPresentation, requestIngestion} from './ingestion.js';

function Sharing({service, material, onSaved}) {
  const actor = identity(), flight = useRef(false), request = useRef(null);
  const [grants, setGrants] = useState(material.acl?.grants || []), [kind, setKind] = useState('team'), [selected, setSelected] = useState([]), [actions, setActions] = useState(['read']);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [query, setQuery] = useState(''), [offset, setOffset] = useState(0), [labels, setLabels] = useState({});
  const [deniedIds, setDeniedIds] = useState(material.acl?.denied_user_ids || []);
  const directory = useKnowledgeRead(() => service.listAclCandidates(material.material_id, {subject_kind: kind, q: query, limit: 20, offset}), [service, material.material_id, kind, query, offset], kind !== 'company');
  useEffect(() => {
    if (directory.value) setLabels(previous => ({...previous, ...Object.fromEntries(directory.value.items.map(row => [`${directory.value.subject_kind}:${row.subject_id}`, row.display_name]))}));
  }, [directory.value]);
  const options = (directory.value?.items || []).map(row => ({value: row.subject_id, label: row.display_name}));
  const changeKind = value => {setKind(value); setSelected([]); setQuery(''); setOffset(0);};
  const add = () => {try {const added = aclGrants(kind, selected.map(row => row.value), actions); setGrants(previous => [...previous.filter(old => !added.some(row => row.subject_kind === old.subject_kind && row.subject_id === old.subject_id)), ...added]); setSelected([]); setError('');} catch (failure) {setError(errorText(failure));}};
  const save = async () => {
    if (flight.current || actor !== identity()) return;
    flight.current = true; setBusy(true); setError('');
    try {
      const body = {expected_acl_revision: material.acl_revision, grants, denied_user_ids: deniedIds};
      const fingerprint = JSON.stringify(body);
      if (request.current?.fingerprint !== fingerprint) request.current = {fingerprint, key: newRequestKey()};
      await service.updateAcl(material.material_id, body, request.current.key);
      if (actor === identity()) onSaved();
    } catch (failure) {if (actor === identity()) setError(errorText(failure));}
    finally {flight.current = false; if (actor === identity()) setBusy(false);}
  };
  return <section className="kb122-sharing"><h3>系统共享权限</h3><p className="pc-muted">内容范围仅描述业务受众；对象授权与账号当前功能权限同时满足时才可查看、编辑和管理。关联 CRM 不会扩大权限。</p>
    {error && <Alert type="error" showIcon title={error} />}
    <div className="kb122-share-grants">{!grants.length && <p>个人库 · 仅材料所有者</p>}{grants.map((grant, index) => <div className="kb122-file-row" key={index}><span>{grant.subject_kind === 'company' ? '企业全员' : `${grant.subject_kind === 'team' ? '团队' : '成员'}：${labels[`${grant.subject_kind}:${grant.subject_id}`] || grant.subject_id}`} · {grant.actions.map(action => ({read: '查看', edit: '编辑', manage: '管理'})[action]).join('、')}</span><Button size="small" disabled={busy} onClick={() => setGrants(rows => rows.filter((_, i) => i !== index))}>移除授权</Button></div>)}</div>
    {deniedIds.length > 0 && <div className="kb122-share-denied"><h4>明确禁止访问的成员</h4><p className="pc-muted">历史禁止项会保留，成员停用或未出现在当前候选页不会自动删除。</p>{deniedIds.map(id => <div key={id} className="kb122-file-row"><span>{labels[`user:${id}`] || id}</span><Button size="small" disabled={busy} onClick={() => setDeniedIds(rows => rows.filter(value => value !== id))}>移除禁止</Button></div>)}</div>}
    <Segmented value={kind} onChange={changeKind} options={[{value: 'team', label: '团队'}, {value: 'user', label: '指定成员'}, {value: 'company', label: '企业全员'}]} disabled={busy} />
    {kind !== 'company' && <>
      <Input.Search aria-label="搜索共享对象" maxLength={100} value={query} onChange={event => {setQuery(event.target.value); setOffset(0);}} allowClear placeholder={kind === 'team' ? '搜索有效团队' : '搜索有效成员'} disabled={busy} />
      <LoadState loading={directory.loading} error={directory.error} onRetry={directory.reload}>
        <Select mode="multiple" virtual={false} labelInValue aria-label="共享对象" value={selected} options={options} onChange={setSelected} placeholder="选择当前页的共享对象" disabled={busy} showSearch={false} notFoundContent="当前条件下没有可选对象" />
        {directory.value && <div className="kb122-pagination"><span>共 {directory.value.total} 个 · 当前页 {directory.value.items.length} 个</span><Button disabled={busy || offset === 0} onClick={() => setOffset(value => Math.max(0, value - 20))}>上一页候选</Button><Button disabled={busy || offset + directory.value.items.length >= directory.value.total || offset >= 100000} onClick={() => setOffset(value => Math.min(100000, value + 20))}>下一页候选</Button></div>}
      </LoadState>
      {selected.length > 0 && <p className="pc-muted">已选择 {selected.length} 个，搜索和翻页会保留选择。</p>}
    </>}
    <Checkbox.Group value={actions} onChange={setActions} disabled={busy} options={[{label: '可查看', value: 'read'}, {label: '可编辑', value: 'edit'}, {label: '可管理', value: 'manage'}]} />
    <Button onClick={add} disabled={busy || !actions.length || (kind !== 'company' && (!selected.length || directory.loading || Boolean(directory.error)))}>添加授权</Button>
    <div className="kb122-drawer-actions"><Button disabled={busy} onClick={() => setGrants([])}>改为个人库</Button><Button type="primary" loading={busy} onClick={save}>保存共享权限</Button></div>
  </section>;
}

export default function MaterialDrawer({service, materialId, initialVersionId, onClose, onEdit, onChanged, onDistribution}) {
  const actor = identity();
  const fileSerial = useRef(0), fileFlight = useRef(false), downloadCleanups = useRef(new Set());
  const [preview, setPreview] = useState(null), [archiveNotice, setArchiveNotice] = useState(''), [fileProgress, setFileProgress] = useState('');
  const clearFile = () => {fileSerial.current++; fileFlight.current = false; setFileBusy(false); setPreview(null); for (const cleanup of downloadCleanups.current) cleanup(); downloadCleanups.current.clear();};
  const mounted = useRef(true), ingestionFlight = useRef(false), ingestionIntent = useRef(null);
  useEffect(() => {mounted.current = true; return () => {mounted.current = false; fileSerial.current++; for (const cleanup of downloadCleanups.current) cleanup(); downloadCleanups.current.clear();};}, []);
  const detail = useKnowledgeRead(async () => assertDetail(await service.getMaterial(materialId), materialId), [service, materialId]);
  const [versionId, setVersionId] = useState(initialVersionId || null), [fileBusy, setFileBusy] = useState(false), [error, setError] = useState('');
  const [ingestionBusy, setIngestionBusy] = useState(false), [ingestionError, setIngestionError] = useState('');
  const material = detail.value;
  const catalog = useKnowledgeRead(() => service.getCatalog(), [service]);
  const versions = [...(material?.versions || [])].sort((a, b) => b.version_no - a.version_no);
  const version = versionId ? versions.find(row => row.version_id === versionId) : versions[0];
  useEffect(() => {clearFile();}, [materialId, version?.version_id, material, actor]);
  useEffect(() => {setArchiveNotice('');}, [materialId, actor]);
  const refreshDetail = () => {clearFile(); setError(''); setIngestionError(''); detail.reload();};
  const binding = useKnowledgeRead(
    () => material?.capabilities?.manage === true && version?.version_id && typeof service.getProviderBinding === 'function'
      && !importedSource(version.contribution) ? service.getProviderBinding(materialId, version.version_id) : Promise.resolve(null),
    [service, materialId, material?.capabilities?.manage, version?.version_id, material],
    Boolean(material?.capabilities?.manage && version?.version_id),
  );
  const contribution = version?.contribution, selection = contribution?.selection || {};
  const source = importedSource(contribution), links = contributionLinks(contribution);
  const ingestion = useKnowledgeRead(() => service.getIngestion(materialId, version.version_id), [service, materialId, version?.version_id, material], Boolean(version && !source && typeof service.getIngestion === 'function'));
  const processing = ingestionPresentation(ingestion.value);
  const startIngestion = async () => {
    if (ingestionFlight.current || actor !== identity() || !processing.canRetry) return;
    ingestionFlight.current = true; setIngestionBusy(true); setIngestionError('');
    const fingerprint = JSON.stringify([actor, materialId, version.version_id, material.material_revision]);
    if (ingestionIntent.current?.fingerprint !== fingerprint) ingestionIntent.current = {fingerprint, key: newRequestKey()};
    const current = () => mounted.current && actor === identity();
    try {
      await requestIngestion(service, {material_id: materialId, version_id: version.version_id, expected_material_revision: material.material_revision}, ingestionIntent.current.key, current);
      if (current()) {ingestionIntent.current = null; detail.reload(); onChanged();}
    } catch (failure) {if (current()) {setIngestionError(ingestionErrorText(failure)); ingestion.reload();}}
    finally {ingestionFlight.current = false; if (current()) setIngestionBusy(false);}
  };
  const file = async (row, action) => {
    if (fileFlight.current || actor !== identity()) return;
    const showPreview = action === 'inline';
    clearFile(); const generation = fileSerial.current;
    const current = () => mounted.current && generation === fileSerial.current && actor === identity();
    fileFlight.current = true; setFileBusy(true); setFileProgress('正在读取原文件…'); setError('');
    if (showPreview) setPreview({name: row.filename || row.file_name || '资料', blob: null});
    try {
      const context = {material_id: materialId, version_id: version.version_id, file_id: row.file_id, isCancelled: () => !current(), onProgress: ({received, total}) => {if (current()) setFileProgress(`已读取 ${Math.floor(received * 100 / total)}% · ${(received / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`);}};
      if (showPreview) {
        const result = await service.readFile({...context, preview: true});
        if (!current()) return;
        if (!(result?.blob instanceof Blob) || !result.blob.size) throw Error('文件尚未真实归档或内容为空');
        setPreview({name: result.filename || row.filename || '资料', blob: result.blob});
      } else {
        // Both new-page browsing and downloads fetch fresh ACL-authorized bytes.
        // No storage URL or file token is exposed to the browser.
        const cleanup = await openAuthorizedFile(service, context, action === 'new-tab', current);
        if (current() && typeof cleanup === 'function') downloadCleanups.current.add(cleanup);
      }
    } catch (failure) {if (current()) {setPreview(null); setError(errorText(failure));}}
    finally {if (current()) {fileFlight.current = false; setFileBusy(false);}}
  };
  const openLink = async link => {
    if (fileBusy || actor !== identity()) return;
    // Reserve a tab while the click still has user activation; authorization
    // below is asynchronous and a later target=_blank click may be blocked.
    const popup = typeof window !== 'undefined' && typeof window.open === 'function' ? window.open('about:blank', '_blank') : null;
    if (popup) {
      try { popup.opener = null; } catch (failure) { /* browser may expose a read-only opener */ }
      try { popup.document.title = '正在校验原始来源…'; } catch (failure) { /* cross-window policy */ }
    }
    setFileBusy(true); setError('');
    try {
      const href = await authorizeMaterialLink(service, {material_id: materialId, version_id: version.version_id, link}, () => actor === identity());
      if (popup) { popup.location.href = href; }
      else {
        const anchor = document.createElement('a'); anchor.href = href; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
        document.body.appendChild(anchor); anchor.click(); anchor.remove();
      }
    } catch (failure) {
      if (popup) { try { popup.close(); } catch (closeError) { /* already closed */ } }
      if (actor === identity()) setError(errorText(failure));
    }
    finally {if (actor === identity()) setFileBusy(false);}
  };
  const copyLink = async link => {
    if (fileBusy || actor !== identity()) return;
    try {await navigator.clipboard.writeText(link); message.success('原材料链接已复制，可直接粘贴到浏览器打开');}
    catch {message.error('浏览器未允许复制，请点击“浏览原材料（新页）”');}
  };
  return <Drawer open width={760} title="材料详情" rootClassName="department-ui kb122-drawer" onClose={() => {clearFile(); onClose();}} extra={<Button disabled={ingestionBusy} onClick={refreshDetail}>重新读取</Button>}>
    <LoadState loading={detail.loading} error={detail.error} onRetry={refreshDetail}>
      {material && <>
        <h2>{source?.original_name || contribution?.content_name || material.content_name}</h2>
        {error && <Alert type="error" showIcon title={error} />}{archiveNotice && <Alert type="success" showIcon title={archiveNotice}/>}
        <div className="kb122-detail-actions">{onDistribution&&<Button onClick={onDistribution}>对客发布与分享</Button>}{material.capabilities?.edit === true && <Button onClick={() => onEdit(material)}>新增版本</Button>}<Tag2>{material.sharing_label || '以当前系统授权为准'}</Tag2></div>
        <Panel title="版本和处理状态">{versionId && !version && <Alert type="warning" title="引用版本已不可访问" description="请重新检索当前授权材料；此处不会自动替换为另一个版本。" />}
          <Select disabled={ingestionBusy} aria-label="材料版本" value={version?.version_id} options={versions.map(row => ({value: row.version_id, label: `V${row.version_no} · ${materialStateLabel(row.state, importedSource(row.contribution))}${material.active_version_state === 'retrievable' && material.active_version_id === row.version_id ? ' · 当前检索版本' : ''}`}))} onChange={value => {clearFile(); setVersionId(value); setIngestionError('');}} />
          <p>系统状态：{!source && ingestion.value ? processing.title : materialStateLabel(version?.state, source)}{version?.state !== 'retrievable' && ' · 保存成功不代表已可检索'}</p>
          {version && !source && <LoadState loading={ingestion.loading} error={ingestion.error} onRetry={ingestion.reload}>
            {ingestion.value && <Alert type={processing.type} showIcon title={processing.title} description={<><p>{processing.description}</p>{ingestion.value.total_files > 0 && <p>已处理附件 {ingestion.value.processed_files} / {ingestion.value.total_files}</p>}{ingestion.value.observed_at && <p>状态更新于 {ingestion.value.observed_at}</p>}{processing.canRetry && <Button loading={ingestionBusy} onClick={startIngestion}>{processing.button}</Button>}</>} />}
          </LoadState>}
          {ingestionError && <Alert type="warning" showIcon title={ingestionError} />}
          <p className="pc-muted">{source ? '导入日期' : '上传日期'}：{version?.uploaded_at || '未记录'} · {source ? '原提供人' : '内容贡献人'}：{source ? source.provider_name || '原表未填写' : version?.contributor_name || version?.contributor_user_id || '未记录'}</p>
          {material.capabilities?.manage === true && !source && <LoadState loading={binding.loading} error={binding.error} onRetry={binding.reload}>
            {binding.value ? <p className="pc-muted">外部检索映射：{binding.value.state === 'active' ? '已绑定' : '已停用'} · 数据集 {binding.value.provider_dataset_id} · 文档 {binding.value.provider_document_id} · 回读于 {binding.value.observed_at}</p>
              : !binding.loading && <p className="pc-muted">当前版本尚未绑定外部检索文档；绑定后才能进入知识助手检索。</p>}
          </LoadState>}
          {(version?.files || []).map(row => <div key={row.file_id} className="kb122-file-row"><span>{row.filename || row.file_name || row.file_id}</span><span><Button title={row.preview_supported === true ? '在线预览' : '该格式不支持网页预览，请下载后查看'} disabled={fileBusy || material.capabilities?.read !== true || service.features?.files === false || row.preview_supported !== true} onClick={() => file(row, 'inline')}>预览</Button><Button title={row.preview_supported === true ? '在新页面浏览' : '该格式不支持网页预览，请下载后查看'} disabled={fileBusy || material.capabilities?.read !== true || service.features?.files === false || row.preview_supported !== true} onClick={() => file(row, 'new-tab')}>新页浏览</Button><Button disabled={fileBusy || material.capabilities?.read !== true || service.features?.files === false} onClick={() => file(row, 'download')}>下载</Button></span></div>)}
          {fileBusy && <p className="pc-muted" role="status">{fileProgress} <Button size="small" onClick={clearFile}>取消</Button></p>}
          {preview && <FilePreview {...preview} onClose={clearFile}/>}
          {!source && links.map(link => <p key={link} className="kb122-source-link"><button className="pc-link" disabled={fileBusy} onClick={() => openLink(link)}>浏览原材料（新页）</button><Button size="small" disabled={fileBusy} onClick={() => copyLink(link)}>复制链接</Button> <span className="pc-muted">{link}</span></p>)}
          {!source && !version?.files?.length && !links.length && <p className="pc-muted">此版本未返回可访问的附件或链接。</p>}
        </Panel>
        {source && <Panel title="飞书弹药库来源">
          <p><Tag2 tone={source.source_status === '可用' ? 'success' : 'neutral'}>原表状态：{sourceStatusLabel(source.source_status)}</Tag2></p>
          <p className="pc-muted">原表状态表示资料登记情况；“可用”不代表文件已归档或已可检索。外部链接按飞书或来源站点自身权限打开。</p>
          <dl className="kb122-facts">{[['原名称', source.original_name], ['原提供人', source.provider_name], ['原产品', source.product_label], ['原类别', source.category_label], ['原使用范围', source.scope_label], ['原备注', source.remarks]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || '原表未填写'}</dd></div>)}</dl>
          {source.validation_issues?.length > 0 && <Alert type="warning" showIcon title="待补齐 / 复核" description={<ul>{source.validation_issues.map(code => <li key={code}>{importIssueText(code)}</li>)}</ul>} />}
          <h4>原材料链接</h4>{links.length ? links.map(link => <p key={link} className="kb122-source-link"><button className="pc-link" disabled={fileBusy} onClick={() => openLink(link)}>浏览原材料（新页）</button><Button size="small" disabled={fileBusy} onClick={() => copyLink(link)}>复制链接</Button> <span className="pc-muted">{link}</span></p>) : <p className="pc-muted">原表未提供链接。</p>}
          {source.original_content_text && <><h4>原材料说明</h4><p>{source.original_content_text}</p></>}
          <h4>原附件描述</h4>{source.attachments?.length ? <>{source.attachments.map((row, index) => { const archived = Number.isSafeInteger(row.size_bytes) && row.size_bytes >= 0 && version?.files?.some(file => file.filename === row.name && file.size_bytes === row.size_bytes); return <p key={index}>{row.name}{Number.isSafeInteger(row.size_bytes) && row.size_bytes >= 0 ? ` · ${row.size_bytes.toLocaleString()} 字节` : ''}{row.media_type ? ` · ${row.media_type}` : ''}{archived ? ' · 已归档，可在线预览或下载' : ' · 未归档，无法在线预览'}</p>; })}<p className="pc-muted">{version?.files?.length ? '已归档附件可在上方按权限预览或下载；未归档项仍只有原表描述。' : '这里只保留原表附件描述，尚无系统文件可供预览或下载。'}</p></> : <p className="pc-muted">原表未提供附件。</p>}
          {material.capabilities?.manage === true && canManageContributionCatalog() && typeof service.getSourceFiles === 'function' && <SourceArchive key={`${actor}:${materialId}:${version.version_id}:${material.material_revision}`} service={service} materialId={materialId} versionId={version.version_id} onArchived={receipt => {clearFile(); setVersionId(receipt.version_id); setArchiveNotice(receipt.changed ? `源附件已归档为 V${receipt.version_no}，原共享范围保持不变；尚未自动对客发布或解析。` : '这些源附件已归档，没有重复创建版本。'); detail.reload(); onChanged();}}/>}
        </Panel>}
        <Panel title="业务字段"><dl className="kb122-facts">{[
          ['内容类别', optionLabel(CATEGORIES, selection.content_category)], ['内容范围', optionLabel(SCOPES, selection.content_scope)], ['产品', source?.product_label || contribution?.product_label || optionLabel(catalog.value?.products, selection.product_code)],
          ['行业', contribution?.industry_label || optionLabel(catalog.value?.industries, selection.industry_code)], ['客户名称', contribution?.customer_name], ['汇报对象岗位', optionLabel(catalog.value?.reporting_positions, selection.reporting_position_code)],
          ['汇报对象职级', optionLabel(catalog.value?.reporting_levels, selection.reporting_level_code)], ['汇报对象决策角色', optionLabel(catalog.value?.decision_roles, selection.decision_role_code)], ['关联商机', contribution?.opportunity_name],
          ['人工补充', selection.supplementary_text], ['备注', source?.remarks || selection.remarks],
        ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || '—'}</dd></div>)}</dl></Panel>
        {material.capabilities?.manage === true ? <Sharing key={`${material.material_id}:${material.acl_revision}`} service={service} material={material} onSaved={() => {detail.reload(); onChanged();}} />
          : <Panel title="系统共享权限"><p>{material.sharing_label || '当前权限由服务端校验；无共享管理权限。'}</p></Panel>}
      </>}
    </LoadState>
  </Drawer>;
}
