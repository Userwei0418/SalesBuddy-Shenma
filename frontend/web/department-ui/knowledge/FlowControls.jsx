import React, {useEffect, useState} from 'react';
import {Alert, Select} from 'antd';
import {useKnowledgeRead} from './hooks.js';
export function ProviderNotice({value, label}) {
  if (value?.status === 'available') return null;
  return <Alert type="info" showIcon title={value?.message || `${label}尚未配置`} description="管理员完成连接与能力验证后开放；已保存资料和已归档作品仍可按当前权限查看、下载和管理。" />;
}
// These exact workflow errors are sealed failure receipts: no work was archived,
// and replaying their key cannot start another run. There is no retryable flag.
// Unknown workflow/transport outcomes stay locked; never infer finality from 503.
export function generationFailureText(code) {
  if (code === 'KNOWLEDGE_DOCUMENT_AUTH_UNAVAILABLE') return '文档生成服务授权失败，未生成可下载作品。请联系管理员修复服务授权后，再准备新的生成请求。';
  if (['KNOWLEDGE_DOCUMENT_RUN_FAILED', 'KNOWLEDGE_DOCUMENT_RUN_STOPPED'].includes(code)) return '文档生成流程已失败或停止，未生成可下载作品。请确认原因并处理后，再准备新的生成请求。';
  if (code === 'KNOWLEDGE_DOCUMENT_DOWNLOAD_UNAVAILABLE' || [
    'MEDIA_TYPE', 'REDIRECT', 'CONTENT_ENCODING', 'CONTENT_LENGTH', 'TOO_LARGE',
    'METADATA', 'CONFIGURATION', 'URL_NOT_ALLOWED', 'TIMEOUT', 'NETWORK',
  ].some(value => code === `KNOWLEDGE_ARTIFACT_DOWNLOAD_${value}`)) return '文档生成流程已返回，但文件未能取得，未生成可下载作品。请联系管理员检查文件下载权限与服务配置；处理后再准备新的生成请求。';
  if (['KNOWLEDGE_DOCUMENT_FILES_INVALID', 'KNOWLEDGE_DOCUMENT_FILES_LIMIT'].includes(code) || [
    'UNSAFE_PACKAGE', 'UNSAFE_RELATIONSHIP', 'INVALID_PACKAGE', 'LIMIT_EXCEEDED',
    'UNSAFE_CONTENT', 'INVALID_STRUCTURE', 'INVALID_XML', 'INVALID_RELATIONSHIP',
    'NOT_EDITABLE', 'FORMAT_UNSUPPORTED', 'FORMAT_MISMATCH',
  ].some(value => code === `KNOWLEDGE_ARTIFACT_${value}`)) return '生成文件未通过格式、大小或安全检查，未生成可下载作品。请联系管理员核对文档生成服务；处理后再准备新的生成请求。';
  return '';
}
export function SourcePicker({service, values, onChange, enabled}) {
  const [queryDraft, setQueryDraft] = useState(''), [query, setQuery] = useState('');
  useEffect(() => { const timer = setTimeout(() => setQuery(queryDraft.trim()), 250); return () => clearTimeout(timer); }, [queryDraft]);
  const read = useKnowledgeRead(() => service.listMaterials({q: query, retrievable: true, page_size: 50, offset: 0}), [service, query], enabled);
  return <><Select mode="multiple" showSearch filterOption={false} aria-label="选择参考材料" placeholder="选择当前授权、可检索的参考材料" value={values} onChange={onChange} onSearch={setQueryDraft}
    options={(read.value?.items || []).filter(row => row.active_version_id && row.active_version_state === 'retrievable').map(row => ({value: row.material_id, label: `${row.content_name} · ${row.active_version_id && row.active_version_id !== row.version_id ? '当前检索版本' : `V${row.version_no}`}`}))} disabled={!enabled} loading={read.loading} />
    {read.error && <p className="pc-error" role="alert">{read.error}</p>}</>;
}
