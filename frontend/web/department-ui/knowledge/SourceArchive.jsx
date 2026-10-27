import React, {useEffect, useRef, useState} from 'react';
import {Alert, Button} from 'antd';
import {identity} from '../pc/data.js';
import {LoadState} from '../pc/ui.jsx';
import {useKnowledgeRead} from './hooks.js';
import {newRequestKey} from './model.js';
import {prepareUploadIntent, completeUploadIntent} from './upload-intent.js';
import {assertSourceFiles, assertSourceUpload, sameSourceRevision, sourceArchiveError, validateSourceFile} from './source-archive.js';

// Dedicated maintenance operation: source metadata/ACL stay intact; no ordinary
// contribution defaults, source downloads, publication or parsing are involved.
export default function SourceArchive({service, materialId, versionId, onArchived}) {
  const actor = identity(), mounted = useRef(true), flight = useRef(false), stored = useRef(new Map()), request = useRef(null), inputs = useRef(new Map());
  const [open, setOpen] = useState(false), [selected, setSelected] = useState({}), [busy, setBusy] = useState(false), [error, setError] = useState(''), [confirm, setConfirm] = useState(false);
  const current = () => mounted.current && actor === identity();
  useEffect(() => {mounted.current = true; return () => {mounted.current = false; stored.current.clear();};}, []);
  const read = useKnowledgeRead(() => service.getSourceFiles(materialId), [service, materialId, versionId], open);
  const source = read.value, eligible = source?.can_archive === true && source.version_id === versionId;
  const reload = () => {if (busy || confirm) return; stored.current.clear(); request.current = null; setSelected({}); setError(''); read.reload();};
  const choose = (row, file) => {
    if (busy || confirm || !current()) return;
    try {if (file) validateSourceFile(row, file); setSelected(previous => ({...previous, [row.source_token]: file || null})); stored.current.delete(row.source_token); setError('');}
    catch (failure) {setSelected(previous => ({...previous, [row.source_token]: null})); setError(failure.message);}
  };
  const save = async () => {
    if (flight.current || !current() || (!request.current && !eligible)) return;
    flight.current = true; setBusy(true); setError('');
    let phase = request.current ? 'archive' : 'preflight';
    try {
      if (!request.current) {
        const latest = assertSourceFiles(await service.getSourceFiles(materialId), materialId); if (!current()) return;
        if (!sameSourceRevision(source, latest)) throw Object.assign(Error('来源已变化'), {code: 'KNOWLEDGE_SOURCE_REVISION_CONFLICT', statusCode: 409});
        const attachments = [];
        for (const row of source.attachments) {
          const file = selected[row.source_token]; if (!file) continue;
          validateSourceFile(row, file); phase = 'upload';
          let receipt = stored.current.get(row.source_token);
          if (!receipt) {
            const intent = await prepareUploadIntent(file, actor); if (!current()) return;
            receipt = assertSourceUpload(await service.uploadFile(file, intent.key), row); if (!current()) return;
            completeUploadIntent(intent); stored.current.set(row.source_token, receipt);
          }
          attachments.push({source_token: row.source_token, name: row.name, size_bytes: row.size_bytes, file_id: receipt.file_id});
        }
        if (!attachments.length) return;
        request.current = {key: newRequestKey(), body: {expected_material_revision: source.material_revision, expected_acl_revision: source.acl_revision, expected_source_revision: source.source_revision, attachments}};
      }
      phase = 'archive';
      const receipt = assertSourceFiles(await service.archiveSourceFiles(materialId, request.current.body, request.current.key), materialId); if (!current()) return;
      // A successful HTTP status alone does not confirm the requested bytes.
      if ((receipt.changed ? receipt.material_revision !== source.material_revision + 1 || receipt.version_no !== source.version_no + 1 || receipt.version_id === source.version_id : receipt.material_revision !== source.material_revision || receipt.version_id !== source.version_id)
        || receipt.acl_revision !== request.current.body.expected_acl_revision || receipt.source_revision !== request.current.body.expected_source_revision
        || request.current.body.attachments.some(row => !receipt.files.some(file => file.source_token === row.source_token && file.name === row.name && file.size_bytes === row.size_bytes && file.sha256 === stored.current.get(row.source_token)?.sha256))) throw Error('归档结果尚未确认');
      request.current = null; setConfirm(false); setSelected({}); stored.current.clear(); onArchived(receipt);
    } catch (failure) {
      if (!current()) return;
      const unknown = !failure.statusCode || failure.statusCode >= 500;
      if (phase === 'archive' && unknown) setConfirm(true);
      else if (phase === 'archive') {request.current = null; setConfirm(false);}
      setError(sourceArchiveError(failure));
    } finally {flight.current = false; if (current()) setBusy(false);}
  };
  if (!open) return <Button onClick={() => setOpen(true)}>归档源附件</Button>;
  return <section className="kb122-source-archive" aria-label="归档源附件"><h4>归档源附件</h4><p className="pc-muted">选择已取得的原文件，名称和大小须与飞书登记一致。归档会保留原名称和共享范围，保存为本材料的新版本；不会自动对客发布或解析。</p>
    {error && <Alert type="warning" showIcon title={error}/>}
    <LoadState loading={read.loading} error={read.error ? sourceArchiveError({message: read.error}) : ''} onRetry={reload}>
      {source && <>
        <p>原表状态：{source.source_status} · 当前材料 V{source.version_no}</p>
        {!eligible && <Alert type="info" title={source.version_id !== versionId ? '当前查看的是历史版本，请切换到最新版本归档。' : '当前来源暂不可归档，请核对原表状态或材料版本。'}/>}
        {source.attachments.map(row => {
          const archived = source.files.some(file => file.source_token === row.source_token);
          const selectionDisabled = busy || confirm || !eligible || !(row.size_bytes > 0) || row.size_bytes > 50 * 1024 * 1024;
          return <div className="kb122-field" key={row.source_token}><label>{row.name} · {row.size_bytes === null ? '大小未登记' : `${row.size_bytes.toLocaleString()} 字节`}</label>
            {archived ? <p>已归档</p> : <><input type="file" hidden ref={node => {if (node) inputs.current.set(row.source_token, node); else inputs.current.delete(row.source_token);}} aria-label={`选择源附件 ${row.name}`} disabled={selectionDisabled} onChange={event => {choose(row, event.target.files?.[0]); event.target.value = '';}}/><div><Button aria-label={`选择原文件 ${row.name}`} disabled={selectionDisabled} onClick={() => inputs.current.get(row.source_token)?.click()}>选择原文件</Button></div>{selected[row.source_token] && <small>已选择：{selected[row.source_token].name}</small>}{!(row.size_bytes > 0) && <small>原表缺少准确文件大小，请先补齐来源信息。</small>}{row.size_bytes > 50 * 1024 * 1024 && <small>原文件超过 50 MiB，暂不能通过此入口归档。</small>}</>}
          </div>;
        })}
        {!source.attachments.length && <p>原表没有登记附件。</p>}
        <div className="kb122-drawer-actions"><Button disabled={busy || confirm} onClick={reload}>重新读取来源</Button><Button type="primary" aria-label={confirm ? '确认原归档' : '确认归档源附件'} loading={busy} disabled={!confirm && (!eligible || !Object.values(selected).some(Boolean))} onClick={save}>{confirm ? '确认原归档' : '确认归档源附件'}</Button></div>
      </>}
    </LoadState>
  </section>;
}
