import {api, identity, runtime} from '../pc/data.js';

const flights = new Map();
const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const invalid = () => Object.assign(Error('商机协作数据不完整，请重新读取'), {code: 'ACCESS_CONTRACT_INVALID'});
export function accessResponse(value, id) {
  const keys = ['can_transfer', 'can_manage_collaborators', 'can_follow_up', 'can_edit'];
  if (value?.contract_version !== 1 || value.opportunity_id !== id || !uuid(id) ||
      !Number.isInteger(value.version_no) || value.version_no < 1 ||
      !keys.every(key => typeof value.capabilities?.[key] === 'boolean') ||
      !value.owner || !uuid(value.customer_reference?.id) || typeof value.customer_reference.name !== 'string' ||
      !Array.isArray(value.collaborators) || value.collaborators.some(p => !uuid(p.user_id) || typeof p.display_name !== 'string')) throw invalid();
  return value;
}
export function collaboratorResult(value) {
  if (typeof value?.changed !== 'boolean' || !Number.isInteger(value.version_no) || value.version_no < 1 ||
      (value.changed ? !uuid(value.event_id) : value.event_id !== null) ||
      !Array.isArray(value.collaborators) || value.collaborators.some(p => !uuid(p.user_id) || typeof p.display_name !== 'string')) throw invalid();
  return value;
}
export function transferResult(value) {
  collaboratorResult(value);
  if (!uuid(value.owner?.user_id) || !uuid(value.owner?.team_id) || typeof value.owner.display_name!=='string' || typeof value.owner.team_name!=='string' || !['pending','disabled','not_configured','unchanged'].includes(value.sync_status)) throw invalid();
  return value;
}
export function createOpportunityAccessApi({getClient = api, getIdentity = identity, getReceipts = () => runtime().require('utils/requestIdentity')} = {}) {
  const context = () => {
    const client = getClient(), owner = getIdentity(), auth = client.getAuth();
    if (!owner || !auth?.actor) throw Error('请先登录');
    return {client, owner, auth};
  };
  const guard = owner => {if (owner !== getIdentity()) throw Object.assign(Error('账号或权限已变化，请重新打开商机'), {code: 'SESSION_CHANGED'});};
  const read = async path => {const {client, owner} = context();const result = await client.request({path, fresh: true});guard(owner);return result;};
  const pathFor = id => {if (!uuid(id)) throw invalid();return `/opportunities/${encodeURIComponent(id)}`;};
  return {
    async access(id) {return accessResponse(await read(`${pathFor(id)}/access`), id);},
    async candidates(id, q = '', offset = 0) {
      const value = await read(`${pathFor(id)}/access-candidates?${new URLSearchParams({purpose:'collaborate', q, limit:'20', offset:String(offset)})}`);
      if (!Array.isArray(value?.items) || !Number.isInteger(value.total) || value.total < 0 || value.items.some(p => !uuid(p.user_id) || typeof p.display_name !== 'string')) throw invalid();
      return value;
    },
    async transfer(id, data) {
      const {client, owner, auth} = context(), receipts = getReceipts();
      const options = {path:`${pathFor(id)}/transfer`, method:'POST', data};
      const scope = [client.getBaseUrl(), auth.actor.workspace_id, auth.actor.user_id];
      const signature = `${owner}:${receipts.fingerprint(scope, options)}`;
      if (flights.has(signature)) return flights.get(signature);
      const receipt = receipts.begin(scope, options);
      const promise = client.request({...options, idempotencyKey:receipt.key}).then(result => {
        guard(owner);transferResult(result);receipts.finish(receipt);return result;
      }).catch(error => {
        if ([400,403,404,422].includes(error.statusCode) || (error.statusCode === 409 && !String(error.serverCode || '').startsWith('IDEMPOTENCY_'))) receipts.finish(receipt);
        throw error;
      }).finally(() => flights.delete(signature));
      flights.set(signature, promise);return promise;
    },
    async transferCandidates(id,q='',offset=0) {
      const result=await read(`${pathFor(id)}/access-candidates?${new URLSearchParams({purpose:'transfer',q,limit:'20',offset:String(offset)})}`);
      if(!Array.isArray(result?.items)||!Number.isInteger(result.total)||result.total<0||result.items.length>20||result.items.some(p=>!uuid(p.user_id)||typeof p.display_name!=='string'||!Array.isArray(p.teams)||p.teams.some(t=>!uuid(t.team_id)||typeof t.name!=='string')))throw invalid();
      return result;
    },
    async save(id, data) {
      const {client, owner, auth} = context(), receipts = getReceipts();
      const options = {path:`${pathFor(id)}/collaborators`, method:'PUT', data};
      const scope = [client.getBaseUrl(), auth.actor.workspace_id, auth.actor.user_id];
      const signature = `${owner}:${receipts.fingerprint(scope, options)}`;
      if (flights.has(signature)) return flights.get(signature);
      const receipt = receipts.begin(scope, options);
      const promise = client.request({...options, idempotencyKey:receipt.key}).then(result => {
        guard(owner);collaboratorResult(result);receipts.finish(receipt);return result;
      }).catch(error => {
        if ([400,403,404,422].includes(error.statusCode) || (error.statusCode === 409 && !String(error.serverCode || '').startsWith('IDEMPOTENCY_'))) receipts.finish(receipt);
        throw error;
      }).finally(() => flights.delete(signature));
      flights.set(signature, promise);return promise;
    },
  };
}
