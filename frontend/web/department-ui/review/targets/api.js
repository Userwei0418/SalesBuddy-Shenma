import {api, identity, runtime} from '../../pc/data.js';

const flights = new Map();
const query = values => new URLSearchParams(Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== '')).toString();

// New Review routes use the shared auth transport and persistent intent receipts.
// apiClient's old mutation allowlist does not yet include /review, so supply the
// same explicit receipt header without changing the generated Mini source.
export async function reviewWrite(path, data, method = 'POST') {
  const client = api(), owner = identity(), auth = client.getAuth();
  if (!auth?.actor || !owner) throw Error('请先登录');
  const receipts = runtime().require('utils/requestIdentity');
  const options = {path, method, data}, scope = [client.getBaseUrl(), auth.actor.workspace_id, auth.actor.user_id];
  const fingerprint = `${owner}:${receipts.fingerprint(scope, options)}`;
  if (flights.has(fingerprint)) return flights.get(fingerprint);
  const receipt = receipts.begin(scope, options);
  const promise = client.request({...options, idempotencyKey: receipt.key}).then(result => {
    if (owner !== identity()) throw Object.assign(Error('账号或权限已变化，请在当前账号下重新读取'), {code: 'SESSION_CHANGED'});
    if (!result || typeof result !== 'object' || (path === '/review/targets' && !['effective', 'pending', 'unchanged'].includes(result.status))) throw Error('未收到完整保存回执，请先刷新核对');
    receipts.finish(receipt);
    return result;
  }).catch(error => {
    if ([400, 403, 404, 422].includes(error.statusCode) || (error.statusCode === 409 && !String(error.serverCode || '').startsWith('IDEMPOTENCY_'))) receipts.finish(receipt);
    throw error;
  }).finally(() => flights.delete(fingerprint));
  flights.set(fingerprint, promise);
  return promise;
}

export const reviewTargetsApi = {
  targets: params => api().request({path: `/review/targets?${query(params)}`, fresh: true}),
  options: () => api().request({path: '/review/targets/options', fresh: true}),
  metrics: () => api().request({path: '/review/metrics', fresh: true}),
  saveTarget: body => reviewWrite('/review/targets', body),
  createMetric: body => reviewWrite('/review/metrics', body),
  updateMetric: (code, body) => reviewWrite(`/review/metrics/${encodeURIComponent(code)}`, body, 'PATCH'),
  history: id => api().request({path: `/targets/${encodeURIComponent(id)}/history`, fresh: true}),
};
