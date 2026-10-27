import React, {useEffect, useRef, useState} from 'react';
import {Alert, Button, Drawer, Pagination, Select} from 'antd';
import {Input} from '../ImeInput.jsx';
import {identity} from '../pc/data.js';
import {Empty2, LoadState, Panel, Tag2} from '../pc/ui.jsx';
import {useKnowledgeRead} from './hooks.js';
import {SCOPES, newRequestKey} from './model.js';
import {errorText, isGenerationInputError, openAuthorizedFile} from './service.js';
import WorkShareDrawer from './WorkShareDrawer.jsx';
import {ProviderNotice, SourcePicker, generationFailureText} from './FlowControls.jsx';
import './flows.css';
export function KnowledgeWorks({service, onOpen, initialSources = []}) {
  const actor = identity(), flight = useRef(false), request = useRef(null), selection = useRef(0), mounted = useRef(true);
  const status = useKnowledgeRead(() => service.status(), [service]);
  const [offset, setOffset] = useState(0), [creating, setCreating] = useState(initialSources.length > 0);
  const works = useKnowledgeRead(() => service.listWorks({limit: 20, offset}), [service, offset]);
  const [purpose, setPurpose] = useState(''), [sources, setSources] = useState(initialSources), [format, setFormat] = useState('docx'), [audience, setAudience] = useState('internal');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [receipt, setReceipt] = useState(null);
  const [sharing, setSharing] = useState(null), [notice, setNotice] = useState(''), [parent, setParent] = useState(null), [readingParent, setReadingParent] = useState(false), [unconfirmed, setUnconfirmed] = useState(false), [failedGeneration, setFailedGeneration] = useState(false);
  const available = status.value?.generation?.status === 'available', formats = status.value?.generation?.formats || [];
  // Provider status declares configured formats, not a successful generation probe.
  const enabledFormats = Array.isArray(formats) ? formats.filter(value => ['docx', 'pptx'].includes(value)) : [];
  useEffect(() => {mounted.current = true; return () => {mounted.current = false; selection.current++;};}, []);
  const current = () => mounted.current && actor === identity();
  const resetGeneration = () => {selection.current++; setParent(null); setPurpose(''); setSources([]); setReceipt(null); setReadingParent(false); setFailedGeneration(false); request.current = null;};
  const prepareNewGeneration = () => {
    if (flight.current || readingParent || !available || !failedGeneration || !current()) return;
    // Explicit acknowledgement only. Keep inputs/parent revision for review;
    // the next submit revalidates sources and creates a new idempotency key.
    request.current = null; setFailedGeneration(false); setError(''); setReceipt(null);
  };
  const nextVersion = async workId => {
    if (busy || unconfirmed || failedGeneration || !available || !current()) return;
    const serial = ++selection.current;
    setReadingParent(true); setError(''); setNotice('');
    try {
      const work = await service.getWork(workId);
      if (!current() || serial !== selection.current) return;
      if (work?.work_id !== workId || work.can_manage !== true || !Number.isInteger(work.revision) || !Array.isArray(work.sources)) throw Error('当前无法为此作品生成新版本，请刷新作品后重试');
      setCreating(true); setParent(work); setPurpose(work.purpose || ''); setSources([...new Set(work.sources.map(source => source.material_id))]); setFormat(work.format || 'docx'); setAudience(work.audience || 'internal'); setReceipt(null); request.current = null;
    } catch (failure) {if (current() && serial === selection.current) setError(errorText(failure));}
    finally {if (current() && serial === selection.current) setReadingParent(false);}
  };
  const generate = async () => {
    if (flight.current || readingParent || failedGeneration || !available || !enabledFormats.includes(format) || !sources.length || !purpose.trim() || !current()) return;
    flight.current = true; setBusy(true); setError(''); setReceipt(null);
    try {
      const body = {purpose: purpose.trim(), material_ids: sources, format, audience, ...(parent ? {parent_work_id: parent.work_id, expected_parent_revision: parent.revision} : {})};
      const fingerprint = JSON.stringify(body); if (request.current?.fingerprint !== fingerprint) request.current = {fingerprint, key: newRequestKey()};
      const row = await service.generate(body, request.current.key);
      if (!current()) return;
      if (!row?.job_id) throw Object.assign(Error('生成请求未取得任务回执'), {code: 'KNOWLEDGE_RECEIPT_UNCONFIRMED'});
      setUnconfirmed(false); setReceipt(row); if (offset) setOffset(0); else works.reload();
    } catch (failure) {if (current()) {
      const terminal = generationFailureText(failure.code);
      const unknown = !terminal && (['NETWORK_ERROR', 'KNOWLEDGE_RECEIPT_UNCONFIRMED'].includes(failure.code) || failure.statusCode >= 500);
      setFailedGeneration(Boolean(terminal)); setUnconfirmed(unknown); setError(terminal || (unknown ? '生成响应尚未确认。先刷新作品查看；确认原请求将沿用同一次请求，不会重复创建。若持续返回相同错误，请联系管理员核对生成服务的执行结果。' : isGenerationInputError(failure) ? `本次未发起文档生成。${errorText(failure)}` : errorText(failure)));
    }}
    finally {flight.current = false; if (current()) setBusy(false);}
  };
  const download = async (work, file) => {if (!current()) return; setError(''); try {await openAuthorizedFile(service, {work_id: work.work_id, file_id: file.file_id}, false, current);} catch (failure) {if (current()) setError(errorText(failure));}};
  return <><LoadState loading={status.loading} error={status.error} onRetry={status.reload}><ProviderNotice value={status.value?.generation} label="材料生成" /></LoadState>
    {error && !creating && <Alert type={unconfirmed ? "warning" : "error"} title={error} />}
    {notice && <Alert type="success" showIcon title={notice} closable onClose={() => setNotice('')} />}
    <div className="kb-flow-toolbar"><div><h2>我的生成材料</h2><p className="pc-muted">生成材料默认仅自己可见，完成后可管理共享。</p></div><Button type="primary" onClick={() => setCreating(true)}>新建材料</Button></div><Drawer aria-label="生成材料表单" title={parent ? "生成作品新版本" : "新建材料"} open={creating} width={600} onClose={() => setCreating(false)}><Panel title={parent ? '生成作品新版本' : '生成材料'} note="生成后需真实归档才可下载"><div className="kb122-stack">
      {error && <Alert type={unconfirmed ? "warning" : "error"} title={error} />}
      {available && <p className="pc-muted">{status.value.generation.message || '生成能力已配置，提交时验证连接与实际结果。'}</p>}
      {parent && <Alert type="info" title={`基于：${parent.title || parent.purpose || '已有作品'} · V${parent.version_no || 1}`} description="新版本重新按当前材料权限生成，默认仅作者可见；原版本与原共享范围保留。" action={<Button disabled={busy || unconfirmed || failedGeneration} onClick={resetGeneration}>改为新建作品</Button>} />}
      <SourcePicker service={service} values={sources} onChange={setSources} enabled={available && !busy && !readingParent && !unconfirmed && !failedGeneration} />
      <Input.TextArea aria-label="材料用途" rows={4} placeholder="说明用途、交付要求和需要回答的问题" value={purpose} onChange={event => setPurpose(event.target.value)} disabled={!available || busy || readingParent || unconfirmed || failedGeneration} />
      <Select aria-label="材料受众" value={audience} options={SCOPES} onChange={setAudience} disabled={!available || busy || readingParent || unconfirmed || failedGeneration} />
      <Select aria-label="生成格式" value={format} options={[{value: 'docx', label: 'DOCX', disabled: !enabledFormats.includes('docx')}, {value: 'pptx', label: 'PPTX（独立验收后开放）', disabled: !enabledFormats.includes('pptx')}]} onChange={setFormat} disabled={!available || busy || readingParent || unconfirmed || failedGeneration} />
      <Button type="primary" loading={busy || readingParent} disabled={!available || readingParent || failedGeneration || !enabledFormats.includes(format) || !sources.length || !purpose.trim()} onClick={generate}>{unconfirmed ? '确认原生成请求' : parent ? '提交生成新版本' : '提交生成任务'}</Button>
      {failedGeneration && <Button disabled={!available || busy || readingParent} onClick={prepareNewGeneration}>准备新的生成请求</Button>}
      {receipt && <Alert type="info" title={`已收到任务：${receipt.job_id}`} description={receipt.status === 'succeeded' ? '生成已完成，请在作品中核对归档文件。' : '请在我的作品刷新查看实际处理及归档结果。'} />}
    </div></Panel></Drawer><Panel title="我的作品" note="本人生成及共享给我的作品" extra={<Button onClick={works.reload}>刷新作品</Button>}>
      <LoadState loading={works.loading} error={works.error} onRetry={works.reload}>{works.value?.items?.length ? <div className="kb122-stack">{works.value.items.map(work => <article className="kb122-work" key={work.work_id || work.job_id}>
        <h3>{work.title || work.purpose || '生成任务'}</h3><Tag2>{work.status === 'succeeded' && !work.files?.length ? '归档待确认' : ({queued: '排队中', running: '生成中', archiving: '文件归档中', succeeded: '已完成', failed: '生成失败'})[work.status] || '状态待确认'}</Tag2>
        {work.version_no && <span> · V{work.version_no}</span>}{work.error && <p className="pc-error">{work.error}</p>}
        {work.status === 'succeeded' && work.files?.length ? work.files.map(file => <Button key={file.file_id} onClick={() => download(work, file)}>下载 {file.filename || '作品'}</Button>) : work.status === 'succeeded' ? <p className="pc-error">产物归档信息不完整，暂不可下载。</p> : null}
        {work.can_manage === true ? <><Button onClick={() => {setNotice(''); setSharing(work.work_id);}}>管理作品共享</Button><Button disabled={!available || busy || readingParent || unconfirmed || failedGeneration || work.status !== 'succeeded'} onClick={() => nextVersion(work.work_id)}>生成新版本</Button></> : work.can_manage === false ? <Tag2>共享给我</Tag2> : null}
        {work.material_id && <Button onClick={() => onOpen(work.material_id)}>查看版本与共享</Button>}
        {(work.sources || []).map(source => <p key={`${source.material_id}:${source.version_id}`}>来源：<button className="pc-link" onClick={() => onOpen(source.material_id, source.version_id)}>{source.content_name || '来源资料'} · {source.version_no ? `V${source.version_no}` : '已锁定来源版本'}</button></p>)}
      </article>)}</div> : <Empty2 title="暂无作品" description="仅展示当前授权的真实任务与已归档文件。" />}</LoadState>
      {Number.isInteger(works.value?.total) && <Pagination current={offset / 20 + 1} pageSize={20} total={works.value.total} showSizeChanger={false} onChange={page => setOffset((page - 1) * 20)} />}
    </Panel>
    {sharing && <WorkShareDrawer key={`${actor}:${sharing}`} service={service} workId={sharing} onClose={() => setSharing(null)} onSaved={(_work, message) => {if (current()) {setSharing(null); setNotice(message); works.reload();}}} />}
  </>;
}
