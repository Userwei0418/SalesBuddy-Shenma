import {api} from '../pc/data.js';
const ROOT = '/knowledge/distribution';
const uuid = value => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value || '');
import {DISTRIBUTION_SOURCE, distributionError} from './distribution-model.js';
export {DISTRIBUTION_SOURCE, QUESTION_FIELDS, distributionError} from './distribution-model.js';
export function assertPublication(row, materialId) {
  if (!row || row.material_id !== materialId || !Number.isSafeInteger(row.revision) || row.revision < 0 || typeof row.can_publish !== 'boolean' || typeof row.can_share !== 'boolean' || !Array.isArray(row.files) || !row.questionnaire || !Array.isArray(row.questionnaire.fields)) throw Error('资料发布信息不完整，请重新读取');
  return row;
}
function page(row) {
  if (!Array.isArray(row?.items) || !Number.isSafeInteger(row.total) || row.total < 0 || !Number.isSafeInteger(row.offset) || !Number.isSafeInteger(row.limit)) throw Error('资料分发分页不完整，请重新读取');
  return row;
}
function query(values, keys) {
  const params=new URLSearchParams();
  for(const key of keys) if(values[key] !== undefined && values[key] !== null && values[key] !== '') params.set(key,String(values[key]));
  return params.toString();
}
export function shareUrl(row, origin = globalThis.location?.origin) {
  if (!row?.share_path || !/^\/web\/knowledge-share\.html#token=[A-Za-z0-9_-]{32,128}$/.test(row.share_path)) throw Error('分享地址不完整，请重新获取');
  const url=new URL(row.share_path,origin);
  if(url.origin !== new URL(origin).origin) throw Error('分享地址来源不符');
  return url.href;
}
export function createDistributionService(client) {
  const request=async options=>{try{return await client.request({fresh:true,...options});}catch(error){throw distributionError(error);}};
  const write=(path,data,key,method='POST')=>{if(!uuid(key))throw Error('操作缺少请求标识');return request({path:ROOT+path,method,data,idempotencyKey:key});};
  return {
    list:async(values={})=>{const result=page(await request({path:ROOT+'/materials?'+query({limit:20,offset:0,...values},['q','content_category','product_code','date_from','date_to','limit','offset'])}));if(result.source!==DISTRIBUTION_SOURCE)throw Error('目录来源不完整，请重新读取');return result;},
    getPublication:async id=>assertPublication(await request({path:ROOT+`/materials/${encodeURIComponent(id)}/publication`}),id),
    publish:async(id,body,key)=>assertPublication(await write(`/materials/${encodeURIComponent(id)}/publication`,body,key,'PUT'),id),
    share:async(id,publicationId,key)=>{
      const row=await write(`/materials/${encodeURIComponent(id)}/shares`,{publication_id:publicationId},key);
      if(row?.material_id!==id||row.publication_id!==publicationId||!uuid(row.share_id)||!uuid(row.version_id)||!uuid(row.sales_user_id))throw Error('分享回执与当前资料版本不匹配，请重新读取');
      return row;
    },
    shares:async(values={})=>page(await request({path:ROOT+'/shares?'+query({scope:'self',limit:20,offset:0,...values},['scope','material_id','version_id','sales_user_id','date_from','date_to','limit','offset'])})),
    revoke:(id,key)=>write(`/shares/${encodeURIComponent(id)}/revoke`,{expected_revoked:false},key),
    statistics:async(values={})=>{
      const row=await request({path:ROOT+'/statistics?'+query({scope:'self',...values},['scope','material_id','version_id','sales_user_id','date_from','date_to'])});
      if(row?.source!==DISTRIBUTION_SOURCE||typeof row.can_view_all!=='boolean'||['visits','unique_visitors_estimate','questionnaire_submissions','previews','download_requests'].some(key=>!Number.isSafeInteger(row[key])||row[key]<0))throw Error('分发统计来源或数值不完整，请重新读取');
      return row;
    },
    responses:async(values={})=>page(await request({path:ROOT+'/responses?'+query({scope:'self',limit:20,offset:0,...values},['scope','material_id','version_id','sales_user_id','date_from','date_to','limit','offset'])})),
  };
}
export function runtimeDistributionService() {
  try {return createDistributionService(api());} catch {return createDistributionService({request:()=>Promise.reject(Error('服务尚未就绪'))});}
}
