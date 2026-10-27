// Customer distribution uses the existing authenticated transport. It never
// counts the internal import catalogue or carries an internal token into H5.
const config = require('../config');
const SOURCE = 'salesbuddy.customer_distribution';
const ROOT = '/knowledge/distribution';
const uuidPattern = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
function requestKey() { return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.floor(Math.random()*16);return(c==='x'?r:(r&3)|8).toString(16);}); }
function safeOrigin(address, originOnly) {
  if (typeof address !== 'string' || /[\s\\]/.test(address)) return null;
  const match = address.match(/^(https?):\/\/([a-z0-9.-]+)(?::([0-9]{1,5}))?(\/[^?#]*)?$/i);
  if (!match || match[2].split('.').some(label => !/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(label))
      || (match[3] && (Number(match[3]) < 1 || Number(match[3]) > 65535))
      || (originOnly && match[4] && match[4] !== '/')) return null;
  return match[1].toLowerCase() + '://' + match[2].toLowerCase() + (match[3] ? ':' + match[3] : '');
}
function customerShareUrl(receipt, baseUrl, webOrigin = config.KNOWLEDGE_CUSTOMER_WEB_ORIGIN) {
  if (typeof receipt?.share_path !== 'string' || /[\s\\]/.test(receipt.share_path) || !/^\/web\/knowledge-share\.html#token=[A-Za-z0-9_-]{32,128}$/.test(receipt.share_path)) throw new Error('分享地址不完整，请重新获取');
  const configured = webOrigin !== undefined && webOrigin !== '';
  const origin = safeOrigin(configured ? webOrigin : baseUrl, configured);
  if (!origin) throw Object.assign(new Error(configured ? '客户 Web 入口配置无效，请联系管理员' : '服务地址不可用，请重新登录'), {code:'KNOWLEDGE_SHARE_WEB_CONFIG_INVALID'});
  return origin + receipt.share_path;
}
function errorText(error) {
  const code=error && (error.serverCode||error.code||error.message);
  return ({KNOWLEDGE_TEMPORARILY_UNAVAILABLE:'知识库暂不能使用',KNOWLEDGE_SHARE_WEB_CONFIG_INVALID:'客户 Web 入口配置无效，请联系管理员',KNOWLEDGE_DISTRIBUTION_DENIED:'当前没有资料分发权限',KNOWLEDGE_SHARE_UNAVAILABLE:'资料已下架或分享不可用',KNOWLEDGE_PUBLICATION_CONFLICT:'发布版本已变化，请刷新后重新分享'})[code] || '暂时无法读取或确认操作，请稍后重试';
}
function createKnowledgeDistribution(client) {
  const request=options=>client.request({...options,fresh:true});
  return {
    list:async(params={})=>{
      const values={limit:20,offset:0,...params};
      const query=['q','content_category','product_code','date_from','date_to','limit','offset'].filter(key=>values[key]!==undefined&&values[key]!=='').map(key=>`${key}=${encodeURIComponent(values[key])}`).join('&');
      const row=await request({path:ROOT+'/materials?'+query});
      if(row?.source!==SOURCE||!Array.isArray(row.items)||!Number.isSafeInteger(row.total)||row.total<0||!Number.isSafeInteger(row.offset)||!Number.isSafeInteger(row.limit))throw new Error('资料目录回执不完整');
      return row;
    },
    get:async id=>{
      const row=await request({path:ROOT+`/materials/${encodeURIComponent(id)}/publication`});
      if(row?.material_id!==id||typeof row.can_share!=='boolean'||!Array.isArray(row.files))throw new Error('资料发布回执不完整');
      return row;
    },
    share:async(id,publicationId,key)=>{
      const row=await request({path:ROOT+`/materials/${encodeURIComponent(id)}/shares`,method:'POST',data:{publication_id:publicationId},idempotencyKey:key});
      if(row?.material_id!==id||row.publication_id!==publicationId||!uuidPattern.test(row.share_id)||!uuidPattern.test(row.sales_user_id)||!uuidPattern.test(row.version_id))throw new Error('分享回执与资料版本不匹配');
      return {...row,url:customerShareUrl(row,client.getBaseUrl())};
    },
  };
}
module.exports={SOURCE,createKnowledgeDistribution,customerShareUrl,requestKey,errorText};
