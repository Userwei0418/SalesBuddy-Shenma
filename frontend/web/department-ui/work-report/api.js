import {REPORT_READ_TIMEOUT} from './read-policy.js';
import {api, identity, runtime} from '../pc/data.js';

const prefix = '/work-reports';
const requiredId = value => {
  if (typeof value !== 'string' || !value.trim()) throw Error('报告记录标识缺失，请重新打开');
  return value;
};
const resourceId = value => encodeURIComponent(requiredId(value));
const queryString = values => {
  const params = new URLSearchParams();
  for (const [key,value] of Object.entries(values || {})) if (value !== undefined && value !== null && value !== '') params.set(key,String(value));
  return params.size ? `?${params}` : '';
};
const changedIdentity = () => Object.assign(Error('身份或权限已变化，请重新打开工作报告'),{code:'REPORT_IDENTITY_CHANGED'});

export function validatePage(page, requestedOffset = 0) {
  if (!page || !Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0 || !Number.isInteger(page.limit) || page.limit < 1 || page.limit > 100 || page.offset !== requestedOffset || page.items.length > page.limit || typeof page.has_more !== 'boolean') throw Error('报告分页响应不完整，请重新读取');
  const next = page.offset + page.items.length;
  if ((page.items.length > 0 && next > page.total) || page.has_more !== (next < page.total) || (page.has_more && (!page.items.length || page.next_offset !== next)) || (!page.has_more && page.next_offset != null)) throw Error('报告分页已变化，请重新读取');
  return page;
}

// Reuses the shared authenticated client and mutation receipts. No second token,
// retry, credential or idempotency implementation is kept in this feature.
export function createWorkReportApi({getClient = api, getIdentity = identity, supportsMutation = options=>runtime().require('utils/requestIdentity').supports(options)} = {}) {
  const send = async (path, method = 'GET', data, signal, timeout) => {
    const owner = getIdentity();
    if (!owner) throw Error('请先登录');
    const cancelled=()=>Object.assign(Error('读取已取消'),{code:'REQUEST_CANCELLED',cancelled:true});
    if(signal?.aborted)throw cancelled();
    let task;
    const abort=()=>task?.abort?.();
    const options = {path:`${prefix}${path}`,method,data,fresh:true,...(timeout?{timeout}:{}),
      ...(signal?{onRequestTask:value=>{task=value;if(signal.aborted)abort();}}:{})};
    if (method !== 'GET' && !supportsMutation(options)) throw Error('报告保存组件尚未配套，请刷新后重试');
    signal?.addEventListener('abort',abort,{once:true});
    try {
      const result = await getClient().request(options);
      if(signal?.aborted)throw cancelled();
      if (owner !== getIdentity()) throw changedIdentity();
      return result;
    } catch (error) {
      if (owner !== getIdentity()) throw changedIdentity();
      throw error;
    } finally {
      signal?.removeEventListener('abort',abort);
    }
  };
  const page = async (path, params = {}) => validatePage(await send(`${path}${queryString({limit:40,offset:0,...params})}`),params.offset ?? 0);
  return {
    options: () => send('/options'),
    overviewOptions: () => send('/overview/options'),
    overview: (params,{signal}={}) => send(`/overview${queryString(params)}`,'GET',undefined,signal,REPORT_READ_TIMEOUT),
    meetingOptions: () => send('/meeting/options'),
    prepareMeeting: data => send('/meeting/snapshots','POST',data),
    meetingSnapshot: id => send(`/meeting/snapshots/${resourceId(id)}`),
    saveMeeting: data => send('/meeting/documents','POST',data),
    schemes: params => page('/schemes',params),
    scheme: id => send(`/schemes/${resourceId(id)}`),
    createScheme: data => send('/schemes','POST',data),
    updateScheme: (id,data) => send(`/schemes/${resourceId(id)}`,'PUT',data),
    prepare: data => send('/snapshots','POST',data),
    snapshot: id => send(`/snapshots/${resourceId(id)}`),
    sources: (id,params) => page(`/snapshots/${resourceId(id)}/sources`,params),
    generate: data => send('/runs','POST',data),
    run: id => send(`/runs/${resourceId(id)}`),
    cancel: (id,data) => send(`/runs/${resourceId(id)}/cancel`,'POST',data),
    documents: params => page('/documents',params),
    document: id => send(`/documents/${resourceId(id)}`),
    feishu: id => send(`/documents/${resourceId(id)}/feishu`),
    publishFeishu: (id,data) => send(`/documents/${resourceId(id)}/feishu/publish`,'POST',data),
    revisions: (id,params) => page(`/documents/${resourceId(id)}/revisions`,params),
    revision: (id,revisionId) => send(`/documents/${resourceId(id)}/revisions/${resourceId(revisionId)}`),
    saveRevision: (id,data) => send(`/documents/${resourceId(id)}/revisions`,'POST',data),
    sharePreview: (id,data,{signal}={}) => send(`/documents/${resourceId(id)}/share-preview`,'POST',data,signal),
    share: (id,data) => send(`/documents/${resourceId(id)}/share`,'POST',data),
    unshare: (id,data) => send(`/documents/${resourceId(id)}/unshare`,'POST',data),
    export: (id,revisionId,format = 'markdown') => {
      if (!['markdown','text'].includes(format)) throw Error('不支持的报告导出格式');
      return send(`/documents/${resourceId(id)}/export${queryString({format,revision_id:requiredId(revisionId)})}`);
    },
  };
}

export const workReportApi = createWorkReportApi();
