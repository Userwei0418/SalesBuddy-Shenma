import {SALES_WEEK_FACTS} from './sales-week.js';

// Only server-issued saved references can reach /runs. general_scheme is an
// editor configuration and must never be fabricated into a runnable reference.
export function composeSchemes(saved=[],options={}) {
  const ids=new Set(saved.map(row=>row.id));
  const builtins=(options.builtin_schemes || []).map(row=>({id:row.scheme_id,name:row.name,version_no:row.version_no,config:row.config,config_hash:row.config_hash,builtin:true}));
  for(const row of builtins) {
    if(!row.id || !Number.isSafeInteger(row.version_no) || row.version_no<1 || !row.config || ids.has(row.id))throw Error('默认报告方案响应不完整，请重新读取');
    ids.add(row.id);
  }
  return [...saved,...builtins];
}
export function compatibleScheme(scheme,period) {
  return Boolean(scheme && (scheme.config.facts_contract!==SALES_WEEK_FACTS || period==='week') &&
    (!scheme.builtin || scheme.config.default_period===period));
}
export function chooseComposeScheme(schemes,{schemeId,period,factsContract=null}) {
  if(schemeId)return schemes.find(row=>row.id===schemeId);
  // Existing weekly schemes keep their order and content. First-time users
  // can use the server-owned weekly default for the selected material profile.
  if(period==='week')return schemes.find(row=>!row.builtin && compatibleScheme(row,period)) ||
    schemes.find(row=>row.builtin && compatibleScheme(row,period) && (row.config.facts_contract ?? null)===factsContract);
  return schemes.find(row=>!row.builtin && row.config.default_period===period && compatibleScheme(row,period)) ||
    schemes.find(row=>row.builtin && compatibleScheme(row,period)) ||
    schemes.find(row=>!row.builtin && compatibleScheme(row,period));
}
