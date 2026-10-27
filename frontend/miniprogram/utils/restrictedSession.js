// Mini-only identity boundary. Display data is never persisted; backend remains authoritative.
const config = require('../config');
const KEY = 'salesReadonlySession';
const HOME = '/pages/demo-view/home/index';
const LOGIN = '/pages/login/index';
const ROUTES = ['home','list','detail','account'].map(name => `pages/demo-view/${name}/index`);
let state = null, generation = 0, redirecting = false;
const flights = new Set();
function baseUrl() { return String(config.LOCK_API_BASE_URL ? config.API_BASE_URL : wx.getStorageSync('salesApiBaseUrl') || config.API_BASE_URL).replace(/\/$/, ''); }
function changed() { return Object.assign(new Error('登录状态已变更，请重试'), {code:'SESSION_CHANGED'}); }
function valid(auth) {
  return !!auth && auth.session_kind === 'demo_readonly' && typeof auth.access_token === 'string' && !!auth.access_token &&
    typeof auth.identity_id === 'string' && typeof auth.session_id === 'string' &&
    auth.company && auth.company.code === config.READONLY_WORKSPACE_CODE && auth.company.id === config.READONLY_WORKSPACE_ID && !!auth.company.name &&
    Date.parse(auth.expires_at) > Date.now();
}
function active() { return !!state; }
function snapshot() { return state ? {generation,baseUrl:state.baseUrl,sessionId:state.auth.session_id} : null; }
function isCurrent(value) { return !!value && !!state && value.generation === generation && value.baseUrl === baseUrl() && value.sessionId === state.auth.session_id; }
function clear() {
  generation += 1;
  state = null;
  const pending = [...flights]; flights.clear();
  pending.forEach(task => { try { task.abort(); } catch (_) {} });
  wx.removeStorageSync(KEY);
}
function restore() {
  const saved = wx.getStorageSync(KEY);
  if (!saved) return null;
  if (saved.baseUrl !== baseUrl() || !valid(saved.auth)) { clear(); return null; }
  state = saved;
  return session();
}
function accept(auth, sourceBase) {
  if (sourceBase !== baseUrl() || !valid(auth)) throw changed();
  clear();
  state = {baseUrl:sourceBase,auth};
  wx.setStorageSync(KEY,state);
  return session();
}
function session() {
  if (!state) return null;
  const a=state.auth;
  return {sessionKind:'demo_readonly',workspaceId:a.company.id,userId:a.identity_id,sessionId:a.session_id,
    companyCode:a.company.code,companyName:a.company.name,account:a.account_code,apiBaseUrl:state.baseUrl,
    modules:a.modules || [],expiresAt:a.expires_at,dataVersion:a.data_version,remote:true,authMethod:'password',loginAt:a.session_id};
}
function go(url=HOME) {
  if (redirecting) return;
  redirecting=true;
  wx.reLaunch({url,complete:()=>{redirecting=false;}});
}
function expire() {
  clear();
  if (typeof getApp === 'function') {
    const app=getApp();
    if (app && app.globalData && app.globalData.session?.sessionKind === 'demo_readonly') app.globalData.session=null;
  }
  go(LOGIN);
}
function guard(route) {
  if (!active()) return true;
  const path=String(route || '').replace(/^\//,'').split('?')[0];
  if (path === 'pages/login/index' || ROUTES.includes(path)) return true;
  go(); return false;
}
function installPageGuard() {
  if (typeof Page !== 'function' || Page._readonlyGuard) return;
  const original=Page;
  const wrapped=function(definition) {
    const guarded={...definition};
    Object.keys(definition).forEach(key=>{
      if (typeof definition[key] !== 'function') return;
      const fn=definition[key];
      if(['onHide','onUnload'].includes(key)) {
        guarded[key]=function(...args){this._readonlyCleanup=true;try{return fn.apply(this,args);}finally{this._readonlyCleanup=false;}};
      } else guarded[key]=function(...args) { if (this._readonlyCleanup || guard(this.route)) return fn.apply(this,args); };
    });
    ['onLoad','onShow'].forEach(key=>{
      if (!guarded[key]) guarded[key]=function(){guard(this.route);};
    });
    return original(guarded);
  };
  wrapped._readonlyGuard=true;
  Page=wrapped;
}
const messages={401:'登录已失效，请重新登录',403:'当前账号无法访问此内容',404:'该记录暂不可用',429:'访问较频繁，请稍后重试'};
function request(path, data) {
  if (!/^\/(session|overview|customers(?:\/[a-f\d-]+)?|opportunities(?:\/[a-f\d-]+)?|follow-ups(?:\/[a-f\d-]+)?|tasks(?:\/[a-f\d-]+)?)$/.test(path)) return Promise.reject(new Error('当前账号无法访问此内容'));
  if (!state || !valid(state.auth)) { expire(); return Promise.reject(Object.assign(new Error(messages[401]),{statusCode:401})); }
  const current=snapshot(), auth=state.auth;
  return new Promise((resolve,reject)=>{
    let task;
    task=wx.request({url:current.baseUrl+'/demo-view'+path,method:'GET',data,timeout:30000,
      header:{Authorization:'Bearer '+auth.access_token,'Content-Type':'application/json'},
      success(response) {
        if(task)flights.delete(task);
        if (!isCurrent(current)) return reject(changed());
        if (response.statusCode >= 200 && response.statusCode < 300) return resolve(response.data);
        if ([401,403].includes(response.statusCode)) expire();
        reject(Object.assign(new Error(messages[response.statusCode] || '暂时无法加载，请稍后重试'),{statusCode:response.statusCode}));
      },fail() { reject(isCurrent(current) ? new Error('网络连接暂不可用，请重试') : changed()); },
      complete() { if(task)flights.delete(task); }
    });
    if(task)flights.add(task);
  });
}
async function verify() {
  const current=snapshot(), result=await request('/session');
  if(!isCurrent(current)) throw changed();
  if(result.session_kind !== 'demo_readonly' || result.identity_id !== state.auth.identity_id || result.session_id !== state.auth.session_id || result.company?.id !== state.auth.company.id) { expire(); throw changed(); }
  state.auth={...state.auth,...result};
  if(!valid(state.auth)){expire();throw changed();}
  wx.setStorageSync(KEY,state);
  const value=session();
  if(typeof getApp==='function')getApp().globalData.session=value;
  return value;
}
function logout() {
  const saved=state;
  clear();
  if(saved)wx.request({url:saved.baseUrl+'/demo-view/logout',method:'POST',timeout:15000,header:{Authorization:'Bearer '+saved.auth.access_token},fail(){}});
}
module.exports={HOME,active,restore,accept,session,clear,logout,request,verify,guard,go,installPageGuard,snapshot,isCurrent};
