// Device history contains company/account labels only. Authentication uses the
// revocable server session in apiClient; passwords never enter this store.
const KEY = 'salesAccountHistoryV3';
const LEGACY_KEY = 'salesRememberedLoginV1';
const normalizeAccount = value => String(value || '').trim().toUpperCase();
const company = value => String(value || '').trim().toLowerCase();
function sanitize(value) {
  if (!value || typeof value.baseUrl !== 'string' || !value.baseUrl || typeof value.account !== 'string') return null;
  const account=normalizeAccount(value.account);
  if (!account || account.length>128) return null;
  const resolvedAccount=normalizeAccount(value.resolvedAccount);
  return {baseUrl:value.baseUrl,workspace:company(value.workspace),account,
    ...(['auto','phone','account'].includes(value.loginType)?{loginType:value.loginType}:{}),
    ...(resolvedAccount && resolvedAccount.length<=128?{resolvedAccount}:{})};
}
function records() {
  // Remove first, before writing migrated labels. A failed write must not retain a password.
  const legacy=wx.getStorageSync(LEGACY_KEY);
  if (legacy) wx.removeStorageSync(LEGACY_KEY);
  const current=wx.getStorageSync(KEY);
  const old=legacy?.version===1?[legacy]:legacy?.version===2&&Array.isArray(legacy.entries)?legacy.entries:[];
  const source=current?.version===3&&Array.isArray(current.entries)?current.entries:[];
  const entries=[];
  for (const item of [...old,...source]) {
    const v=sanitize(item);if(!v)continue;
    const index=entries.findIndex(e=>matches(e,v.baseUrl,v.account,v.workspace));
    if(index>=0)entries.splice(index,1);
    entries.push(v);
  }
  const clean=entries.slice(-20);
  // Re-encode labels, even if a malformed prior V3 record contains extra fields.
  if (legacy || current) wx.setStorageSync(KEY,{version:3,entries:clean});
  return clean;
}
function matches(value,baseUrl,account,workspace='') {
  return value.baseUrl===baseUrl && value.account===normalizeAccount(account) && value.workspace===company(workspace);
}
function cleanup() { records(); }
function read(baseUrl,account,workspace) {
  try {
    const entries=records().filter(v=>v.baseUrl===baseUrl && (workspace===undefined||v.workspace===company(workspace)) &&
      (account===undefined||v.account===normalizeAccount(account)));
    const v=entries[entries.length-1];return v?{account:v.account,workspace:v.workspace,
      ...(v.loginType?{loginType:v.loginType}:{}),...(v.resolvedAccount?{resolvedAccount:v.resolvedAccount}:{})}:null;
  } catch (_) {return null;}
}
function suggest(baseUrl,prefix,workspace) {
  const query=normalizeAccount(prefix);if(!query)return [];
  try {return [...new Set(records().slice().reverse().filter(v=>v.baseUrl===baseUrl&&(workspace===undefined||v.workspace===company(workspace))&&v.account!==query&&v.account.startsWith(query)).map(v=>v.account))].slice(0,5);}
  catch (_) {return [];}
}
function clear(baseUrl,account,workspace='') {
  const entries=records().filter(v=>!matches(v,baseUrl,account,workspace));
  if(entries.length)wx.setStorageSync(KEY,{version:3,entries});else wx.removeStorageSync(KEY);
}
function save(baseUrl,account,workspace='',metadata={}) {
  const value=sanitize({baseUrl,account,workspace,...metadata});if(!value)return;
  // A formerly company-less unique account now has a server-confirmed company.
  // Replace its ambiguous legacy label, retaining every explicit other company.
  const entries=records().filter(v=>!matches(v,baseUrl,account,workspace) &&
    !(workspace && matches(v,baseUrl,account,'')));
  wx.setStorageSync(KEY,{version:3,entries:[...entries,value].slice(-20)});
}
module.exports={read,clear,save,suggest,cleanup,normalizeAccount};
