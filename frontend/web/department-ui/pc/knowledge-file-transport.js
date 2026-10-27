import {api} from './data.js';

// Shared authenticated transport owns credentials, refresh and identity generations.
// File stays local until the user submits; returned bytes contain no public URL.
export function uploadKnowledgeFile(file, {idempotencyKey} = {}) {
  const client = api();
  if (typeof client.uploadKnowledgeFile !== 'function') return Promise.reject(Error('文件上传组件未就绪，请刷新后重试'));
  return client.uploadKnowledgeFile(file, {idempotencyKey});
}
export function readKnowledgeFile(fileId, options = {}) {
  const client = api();
  if (typeof client.readKnowledgeFile !== 'function') return Promise.reject(Error('文件读取组件未就绪，请刷新后重试'));
  return client.readKnowledgeFile(fileId, options);
}
