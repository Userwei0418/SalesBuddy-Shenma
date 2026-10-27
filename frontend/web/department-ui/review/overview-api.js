import {REPORT_READ_TIMEOUT} from '../work-report/read-policy.js';
import {api,identity} from '../pc/data.js';
import {matchingReview,matchingPage,normalizeReviewSelection} from './overview-model.js';

// Deliberately keep only stable error codes and safe public guidance for scope
// failures; backend detail may contain names or permission internals.
function safeScopeError(error) {
  const status=Number(error?.statusCode??error?.status);
  if(![403,409].includes(status))return error;
  const failure=Object.assign(Error(status===403?'当前账号无权查看所选范围，请重新选择。':'所选范围已变化，请重新读取概览后查看明细。'),{statusCode:status});
  for(const key of ['code','serverCode'])if(typeof error[key]==='string')failure[key]=error[key];
  return failure;
}
function pageQuery(query) {
  const allowed=new Set(['activity','filter','sort','metric_code','q','limit','offset']);
  if(!query||Object.keys(query).some(key=>!allowed.has(key)))throw Error('明细查询条件无效，请重新读取');
  return {...query};
}
export function createReviewOverviewApi({getClient=api,getIdentity=identity}={}) {
  const read=async(path,params,signal)=>{
    const owner=getIdentity();
    if(!owner)throw Error('请先登录');
    const query=new URLSearchParams();
    for(const [key,value] of Object.entries(params)) {
      if(value===undefined||value===null||value==='')continue;
      // Repeated keys are the API contract. String coercion would silently
      // turn multiple IDs into one comma-separated subject.
      for(const item of Array.isArray(value)?value:[value])query.append(key,String(item));
    }
    const cancelled=()=>Object.assign(Error('读取已取消'),{code:'REQUEST_CANCELLED',cancelled:true});
    if(signal?.aborted)throw cancelled();
    let task;
    const abort=()=>task?.abort?.();
    signal?.addEventListener('abort',abort,{once:true});
    try {
      const value=await getClient().request({path:`/review/${path}?${query}`,method:'GET',fresh:true,timeout:REPORT_READ_TIMEOUT,
        ...(signal?{onRequestTask:value=>{task=value;if(signal.aborted)abort();}}:{})});
      if(signal?.aborted)throw cancelled();
      if(owner!==getIdentity())throw Error('身份或权限已变化，请重新打开');
      return value;
    } catch(error) {
      if(owner!==getIdentity())throw Error('身份或权限已变化，请重新打开');
      throw safeScopeError(error);
    } finally {
      signal?.removeEventListener('abort',abort);
    }
  };
  const overview=async(selection,{signal}={})=>{const normalized=normalizeReviewSelection(selection);return matchingReview(await read('overview',normalized,signal),normalized);};
  const page=async(path,selection,query,{signal}={})=>{const normalized=normalizeReviewSelection(selection),filters=pageQuery(query);return matchingPage(await read(path,{...normalized,...filters},signal),normalized,filters);};
  return {overview,activities:(selection,query,options)=>page('activities',selection,query,options),opportunities:(selection,query,options)=>page('opportunities',selection,query,options)};
}
export const reviewOverviewApi=createReviewOverviewApi();
