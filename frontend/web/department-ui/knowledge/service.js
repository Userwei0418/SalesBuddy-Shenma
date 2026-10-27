import {api, can, readScopeFor} from '../pc/data.js';
import {uploadKnowledgeFile, readKnowledgeFile} from '../pc/knowledge-file-transport.js';
import {contributionLinks} from './model.js';
import {assertSourceFiles} from './source-archive.js';
import {assertIngestion, ingestionErrorText} from './ingestion.js';

export class KnowledgeUnavailable extends Error {
  constructor(message = '知识库服务尚未接入，请联系管理员完成配置') { super(message); this.code = 'KNOWLEDGE_UNCONFIGURED'; }
}

// The page accepts a service prop. The shared runtime can also supply this port;
// no vendor endpoints, cookies, provider keys or fake successful data live here.
export function runtimeKnowledgeService() {
  let client;
  try { client = api(); } catch { return unavailableService; }
  return client?.knowledge || (typeof client?.request === 'function' ? createKnowledgeService(client, {uploadKnowledgeFile, readKnowledgeFile}) : unavailableService);
}
const unavailable = async () => { throw new KnowledgeUnavailable(); };
export const unavailableService = Object.freeze({
  status: async () => ({retrieval: {status: 'unconfigured', message: '知识检索尚未配置'}, generation: {status: 'unconfigured', message: '材料生成尚未配置', formats: []}}),
  getCatalog: unavailable, listMaterials: unavailable, getMaterial: unavailable, createMaterial: unavailable, addVersion: unavailable,
  getIngestion: unavailable, startIngestion: unavailable, getSourceFiles: unavailable, archiveSourceFiles: unavailable,
  uploadFile: unavailable, updateAcl: unavailable, getStats: unavailable, searchCustomers: unavailable, searchOpportunities: unavailable,
  listAclCandidates: unavailable, listWorkShareCandidates: unavailable, getProviderBinding: unavailable, readFile: unavailable, ask: unavailable, listWorks: unavailable, generate: unavailable, getWork: unavailable, publishWork: unavailable,
});
export const errorText = error => ERRORS[error?.serverCode] || ERRORS[error?.code] || ERRORS[error?.message] || error?.message || '请求未完成，请重试';
export function canManageContributionCatalog() {
  try { return can('knowledge.catalog_manage') && readScopeFor('knowledge.catalog_manage').scopes.includes('workspace'); }
  catch { return false; }
}

const ERRORS = {
  KNOWLEDGE_TEMPORARILY_UNAVAILABLE: '知识库暂不能使用',
  KNOWLEDGE_FILE_TOO_LARGE: '单个附件不能超过 50 MiB，请压缩文件后重新上传。',
  KNOWLEDGE_FILE_SIZE_INVALID: '附件为空，请选择有效文件。',
  KNOWLEDGE_CONTRIBUTION_CATEGORY_DENIED: '当前只能贡献会议演讲或客户方案；其他类别需要目录维护权限。请刷新页面更新权限后再试。',
  KNOWLEDGE_CATALOG_UNCONFIGURED: '知识库目录尚未配置，请联系管理员维护产品和业务枚举',
  KNOWLEDGE_CATALOG_CHANGED: '目录已变化，请重新读取并核对字段',
  KNOWLEDGE_NAME_CONFLICT: '存在同名资料，请从资料目录明确选择已有材料新增版本，或修改人工补充后新建',
  KNOWLEDGE_ACCESS_DENIED: '当前没有该材料的访问权限',
  KNOWLEDGE_MATERIAL_NOT_FOUND: '材料不存在或当前不可访问',
  KNOWLEDGE_PREVIEW_UNSUPPORTED: '此格式暂不支持在线预览，请下载查看',
  KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED: '材料生成尚未配置真实供应商，暂不能提交生成；已有作品仍可查看和下载',
  KNOWLEDGE_DOCUMENT_SOURCE_LIMIT: '所选材料和用途超出单次生成上限，请减少参考材料或缩短用途后重新提交',
  KNOWLEDGE_DOCUMENT_INPUT_INVALID: '生成输入不符合要求，请检查参考材料和用途后重新提交；若仍失败，请联系管理员',
  KNOWLEDGE_WORK_NOT_FOUND: '作品不存在或当前不可访问，请刷新作品列表',
  KNOWLEDGE_WORK_REVISION_CONFLICT: '作品共享范围已变化，请重新读取后核对',
  KNOWLEDGE_WORK_CHANGED: '作品或共享范围已变化，请重新读取后核对',
};
export const isGenerationInputError = error => error?.statusCode === 422 &&
  ['KNOWLEDGE_DOCUMENT_SOURCE_LIMIT', 'KNOWLEDGE_DOCUMENT_INPUT_INVALID'].includes(error?.code);
export function createKnowledgeService(client, fileTransport = client) {
  const request = async options => {
    try {return await client.request({fresh: true, ...options});} catch (error) {
      const code = error.serverCode || (/^KNOWLEDGE_[A-Z_]+$/.test(error.message || '') ? error.message : error.code);
      throw Object.assign(new Error(ERRORS[code] || error.message || '知识库请求失败'), {code, statusCode: error.statusCode});
    }
  };
  const material = row => ({...row, capabilities: {read: true, edit: row.can_edit === true, manage: row.can_manage === true}});
  const service = {
    ...unavailableService,
    // G2 performs filters/counts on the backend's currently readable set.
    features: {filters: true, scope: true, upload: typeof client.uploadKnowledgeFile === 'function', files: typeof client.readKnowledgeFile === 'function'},
    status: () => request({path: '/knowledge/provider/status'}),
    ask: (body, idempotencyKey) => request({path: '/knowledge/ask', method: 'POST', data: body, idempotencyKey, timeout: 30000}),
    listWorks: ({limit = 20, offset = 0} = {}) => request({path: `/knowledge/works?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`}),
    generate: (body, idempotencyKey) => request({path: '/knowledge/works', method: 'POST', data: body, idempotencyKey, timeout: 90000}),
    getWork: workId => request({path: `/knowledge/works/${encodeURIComponent(workId)}`}),
    publishWork: (workId, body, idempotencyKey) => request({path: `/knowledge/works/${encodeURIComponent(workId)}/publish`, method: 'POST', data: body, idempotencyKey}),
    listWorkShareCandidates: async (workId, {subject_kind, q = '', limit = 20, offset = 0}) => {
      if (!['user', 'team'].includes(subject_kind) || typeof q !== 'string' || q.length > 100 || !Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0 || offset > 100000) throw Error('作品共享候选查询条件不完整');
      const params = new URLSearchParams({subject_kind, q, limit: String(limit), offset: String(offset)});
      const page = await request({path: `/knowledge/works/${encodeURIComponent(workId)}/share-candidates?${params}`});
      if (page?.work_id !== workId || page.subject_kind !== subject_kind || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0 || page.limit !== limit || page.offset !== offset || page.items.length > limit || page.items.length > page.total || page.items.some(row => !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(row.subject_id || '') || typeof row.display_name !== 'string' || !row.display_name.trim()) || new Set(page.items.map(row => row.subject_id)).size !== page.items.length) throw Error('共享候选响应与当前作品、类型或分页不匹配，请重新读取');
      return page;
    },
    getCatalog: () => request({path: '/knowledge/catalog'}),
    listMaterials: async ({page_size = 20, offset = 0, ...filters} = {}) => {
      const params = new URLSearchParams({limit: String(page_size), offset: String(offset)});
      for (const key of ['q', 'content_category', 'product_code', 'state', 'scope', 'period', 'retrievable', 'external', 'mine', 'contributor_code']) {
        if (filters[key] !== undefined && filters[key] !== null && filters[key] !== '') params.set(key, String(filters[key]));
      }
      const page = await request({path: `/knowledge/materials?${params}`});
      if (!Array.isArray(page?.items) || !Number.isInteger(page.total) || typeof page.capabilities?.create !== 'boolean') throw Error('资料目录未返回完整分页和权限信息');
      return {...page, items: page.items.map(material)};
    },
    getMaterial: async id => {
      const row = material(await request({path: `/knowledge/materials/${encodeURIComponent(id)}`}));
      assertDetail(row, id);
      if (row.capabilities.manage) row.acl = await request({path: `/knowledge/materials/${encodeURIComponent(id)}/acl`});
      return row;
    },
    createMaterial: (data, idempotencyKey) => request({path: '/knowledge/materials', method: 'POST', data, idempotencyKey}),
    addVersion: (id, data, idempotencyKey) => request({path: `/knowledge/materials/${encodeURIComponent(id)}/versions`, method: 'POST', data, idempotencyKey}),
    getIngestion: async (materialId, versionId) => {
      try {return assertIngestion(await request({path: `/knowledge/materials/${encodeURIComponent(materialId)}/versions/${encodeURIComponent(versionId)}/ingestion`}), materialId, versionId);}
      catch (error) {throw Object.assign(new Error(ingestionErrorText(error)), {code: error.code, statusCode: error.statusCode});}
    },
    startIngestion: async (materialId, versionId, data, idempotencyKey) => {
      if (!Number.isSafeInteger(data?.expected_material_revision) || data.expected_material_revision < 1 || !idempotencyKey) throw Error('解析请求缺少材料版本或请求标识');
      try {return assertIngestion(await request({path: `/knowledge/materials/${encodeURIComponent(materialId)}/versions/${encodeURIComponent(versionId)}/ingestion`, method: 'POST', data: {expected_material_revision: data.expected_material_revision}, idempotencyKey}), materialId, versionId);}
      catch (error) {throw Object.assign(new Error(ingestionErrorText(error)), {code: error.code, statusCode: error.statusCode});}
    },
    getSourceFiles: async materialId => assertSourceFiles(await request({path: `/knowledge/materials/${encodeURIComponent(materialId)}/source-files`}), materialId),
    archiveSourceFiles: async (materialId, body, idempotencyKey) => assertSourceFiles(await request({path: `/knowledge/materials/${encodeURIComponent(materialId)}/source-files`, method: 'POST', data: body, idempotencyKey}), materialId),
    getProviderBinding: (materialId, versionId) => request({path: `/knowledge/materials/${encodeURIComponent(materialId)}/versions/${encodeURIComponent(versionId)}/provider-binding`}),
    updateAcl: (id, data, idempotencyKey) => request({path: `/knowledge/materials/${encodeURIComponent(id)}/acl`, method: 'PUT', data, idempotencyKey}),
    uploadFile: async (file, idempotencyKey) => {
      if (typeof client.uploadKnowledgeFile !== 'function' || typeof fileTransport.uploadKnowledgeFile !== 'function') throw new KnowledgeUnavailable('附件上传正在接入，可先粘贴材料链接');
      if (!file.size || file.size > 50 * 1024 * 1024) throw Error('附件需大于 0 且不超过 50 MiB');
      return fileTransport.uploadKnowledgeFile(file, {idempotencyKey});
    },
    readFile: async context => {
      if (typeof client.readKnowledgeFile !== 'function' || typeof fileTransport.readKnowledgeFile !== 'function') throw new KnowledgeUnavailable('文件访问正在接入，请稍后重试');
      if (!context.file_id || (!context.work_id && !context.version_id)) throw Error('文件访问需要作品或材料版本标识');
      const options = context.work_id ? {workId: context.work_id, preview: false} : {versionId: context.version_id, preview: context.preview === true};
      if (typeof context.onProgress === 'function') options.onProgress = context.onProgress;
      if (typeof context.isCancelled === 'function') options.isCancelled = context.isCancelled;
      const result = await fileTransport.readKnowledgeFile(context.file_id, options);
      if (!(result?.data instanceof ArrayBuffer) || !result.data.byteLength) throw Error('文件响应不是完整的二进制内容');
      return {blob: new Blob([result.data], {type: result.contentType || 'application/octet-stream'}), filename: result.filename};
    },
    searchCustomers: q => client.listOpportunityCustomerReferences({q, pageSize: 50}),
    searchOpportunities: q => client.listOpportunities({q, pageSize: 50}),
    listAclCandidates: async (materialId, {subject_kind, q = '', limit = 20, offset = 0}) => {
      if (!['user', 'team'].includes(subject_kind) || typeof q !== 'string' || q.length > 100 || !Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0 || offset > 100000) throw Error('共享候选查询条件不完整');
      const params = new URLSearchParams({subject_kind, q, limit: String(limit), offset: String(offset)});
      const page = await request({path: `/knowledge/materials/${encodeURIComponent(materialId)}/acl-candidates?${params}`});
      if (page?.material_id !== materialId || page.subject_kind !== subject_kind || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0 || page.limit !== limit || page.offset !== offset || page.items.length > limit || page.items.length > page.total || page.items.some(row => !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(row.subject_id || '') || typeof row.display_name !== 'string' || !row.display_name.trim()) || new Set(page.items.map(row => row.subject_id)).size !== page.items.length) throw Error('共享候选响应与当前材料、类型或分页不匹配，请重新读取');
      return page;
    },
    getStats: async (filters = {}) => {
      const params = new URLSearchParams();
      for (const key of ['q', 'content_category', 'product_code', 'state', 'scope', 'period', 'retrievable', 'external', 'mine', 'contributor_code']) {
        if (filters[key] !== undefined && filters[key] !== null && filters[key] !== '') params.set(key, String(filters[key]));
      }
      const result = await request({path: `/knowledge/stats?${params}`});
      if (result?.source !== 'salesbuddy.internal_materials' || !result.observed_at || !result.metrics) throw Error('知识看板统计来源或观测时间不完整');
      return result;
    },
  };
  return service;
}

export function assertDetail(value, materialId) {
  if (!value || value.material_id !== materialId || !Array.isArray(value.versions)) throw Error('材料回读与当前对象不匹配，请重新读取');
  return value;
}

export async function authorizeMaterialLink(service, {material_id, version_id, link}, stillCurrent = () => true) {
  const detail = assertDetail(await service.getMaterial(material_id), material_id);
  if (!stillCurrent()) throw Error('身份或权限已变化，请重新打开材料');
  const version = detail.versions.find(row => row.version_id === version_id && row.state !== 'deleted');
  if (!contributionLinks(version?.contribution).includes(link)) throw Error('该版本链接已不可访问，请重新读取材料');
  const url = new URL(link);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw Error('材料链接格式不受支持');
  return url.href;
}

// The adapter must fetch bytes through an authenticated current-ACL endpoint on
// EVERY click. A cached storage/provider URL is not an authorization receipt.
export async function openAuthorizedFile(service, context, preview, stillCurrent = () => true) {
  // Reserve the popup during the original click event. Opening a tab only
  // after the authenticated fetch resolves loses user activation in browsers.
  const popup = preview && typeof window !== 'undefined' && typeof window.open === 'function'
    ? window.open('about:blank', '_blank') : null;
  if (popup) {
    try { popup.opener = null; } catch (error) { /* browser may expose a read-only opener */ }
    try { popup.document.title = '正在读取知识库材料…'; } catch (error) { /* cross-window policy */ }
  }
  try {
    const result = await service.readFile({...context, preview, isCancelled: () => !stillCurrent()});
    if (!stillCurrent()) throw Error('身份或权限已变化，请重新打开材料');
    if (!(result?.blob instanceof Blob) || !result.blob.size) throw Error('文件尚未真实归档或内容为空');
    const safePreview = ['application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'text/plain'].includes(result.blob.type);
    const url = URL.createObjectURL(result.blob);
    if (popup && safePreview) {
      popup.location.href = url;
      const timer = setTimeout(() => URL.revokeObjectURL(url), 60000);
      return () => {clearTimeout(timer); URL.revokeObjectURL(url); try { popup.close(); } catch (error) { /* already closed */ }};
    }
    if (popup) { try { popup.close(); } catch (error) { /* already closed */ } }
    const anchor = document.createElement('a'); anchor.href = url;
    if (preview && safePreview) { anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; }
    else anchor.download = result.filename || '知识库材料';
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    const timer = setTimeout(() => URL.revokeObjectURL(url), 60000);
    return () => {clearTimeout(timer); URL.revokeObjectURL(url);};
  } catch (error) {
    if (popup) { try { popup.close(); } catch (closeError) { /* already closed */ } }
    throw error;
  }
}
