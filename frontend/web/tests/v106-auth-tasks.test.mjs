/** Source page + Web storage contracts. Every identity/password below is synthetic. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const read = file => fs.readFileSync(new URL('../' + file, import.meta.url), 'utf8');
const KEY = 'salesAccountHistoryV3';
function util(name,wx={}) {const module={exports:{}};vm.runInNewContext(read('source/miniprogram/utils/'+name+'.js'),{module,wx,require:n=>util(n.split('/').pop(),wx),Date,Map,Set});return module.exports;}

function storage() {
  const values = {};
  return new Proxy(values, {get(target, key) {
    if (key === 'getItem') return name => target[name] ?? null;
    if (key === 'setItem') return (name, value) => {target[name] = String(value);};
    if (key === 'removeItem') return name => {delete target[name];};
    return target[key];
  }});
}
function fixture({local = storage(), session = storage(), mode = 'live', origin = 'http://localhost:5186', apiBase = '/api/v1', login = async () => ({role: 'sales'})} = {}) {
  const window = {SALES_MODE: mode, location: {href: origin + '/', origin}}, wx = {}, notices = [], calls = [];
  vm.runInNewContext(read('browser-platform.js'), {window, localStorage: local, sessionStorage: session, URL, Blob, navigator: {}, screen: {}, requestAnimationFrame: fn => fn()});
  window.SalesPlatform.install(wx);
  Object.assign(wx, {showToast: value => notices.push(value.title), switchTab() {}});
  const app = {globalData: {}, async loginWithApi(...args) {calls.push(args); const result = await login(...args); app.globalData.session = result; wx.setStorageSync('salesApiAuth', {access_token: 'synthetic-session-token'}); wx.setStorageSync('salesSession', result); return result;}, logout() {app.globalData.session = null; wx.removeStorageSync('salesApiAuth'); wx.removeStorageSync('salesSession');}};
  const module = {exports: {}}; vm.runInNewContext(read('source/miniprogram/utils/rememberedLogin.js'), {module, wx});
  let definition; vm.runInNewContext(read('source/miniprogram/pages/login/index.js'), {Page: value => {definition = value;}, wx, getApp: () => app, require: name => name.includes('rememberedLogin') ? module.exports : name.includes('encryptedCredentials') ? util('encryptedCredentials', wx) : name.includes('companyEntry')?util('companyEntry'): {getBaseUrl: () => apiBase, changePassword: async () => {},getCompanyEntry:async code=>({company_code:code,company_name:'合成公司'}),cancelPendingLogin(){}}});
  const page = {...definition, data: {...definition.data}, setData(value) {Object.assign(this.data, value);}};
  return {page, app, wx, local, session, notices, calls, remembered: module.exports};
}
async function submit(x, account = 'SYNTHETIC_A', password = 'Synthetic-Only-A', remember = true) {
  x.page.inputAccount({detail: {value: account}}); x.page.inputPassword({detail: {value: password}});
  if (x.page.data.rememberAccount !== remember) x.page.toggleRememberAccount();
  x.page.setData({agreed: true}); await x.page.submitLogin();
}
test('remember company/account labels only; tokens remain tab scoped and passwords never persist',async()=>{
 const x=fixture();await submit(x,'synthetic_a','Synthetic-Only-A',false);assert.equal(Object.keys(x.local).length,0);
 x.app.logout();await submit(x);assert.equal(Object.keys(x.local).length,1);assert.doesNotMatch(JSON.stringify(x.local),/Synthetic-Only-A|password|access_token/);
 x.app.logout();const fresh=fixture({local:x.local});fresh.page.onLoad();assert.equal(fresh.page.data.account,'SYNTHETIC_A');assert.equal(fresh.page.data.password,'');assert.equal(fresh.page.data.rememberAccount,true);assert.equal(fresh.wx.getStorageSync('salesApiAuth'),'');
});
test('account suggestions preserve names, clear password, and removing a label preserves other accounts',async()=>{
 const x=fixture();await submit(x);x.page.switchAccount();await submit(x,'SYNTHETIC_B','Synthetic-Only-B');x.page.switchAccount();x.page.inputAccount({detail:{value:'synthetic_'}});
 assert.deepEqual(Array.from(x.page.data.accountSuggestions),['SYNTHETIC_B','SYNTHETIC_A']);x.page.selectAccountSuggestion({currentTarget:{dataset:{account:'SYNTHETIC_A'}}});assert.equal(x.page.data.password,'');
 x.page.toggleRememberAccount();assert.equal(x.remembered.read('/api/v1','SYNTHETIC_A'),null);assert.ok(x.remembered.read('/api/v1','SYNTHETIC_B'));await submit(x,'13900000000','Synthetic-Only-Phone',false);assert.equal(x.calls.at(-1)[1],'13900000000');
});
test('saved account labels isolated by Web mode, origin, API and company',async()=>{
 const x=fixture();await submit(x);
 for(const config of [{mode:'preview'},{origin:'http://localhost:5187'},{apiBase:'/other/api/v1'}]){const other=fixture({local:x.local,...config});other.page.onLoad();assert.equal(other.page.data.account,'');}
 x.remembered.save('/api/v1','SAME','alpha');x.remembered.save('/api/v1','SAME','beta');x.remembered.clear('/api/v1','SAME','alpha');assert.equal(x.remembered.read('/api/v1','SAME','alpha'),null);assert.ok(x.remembered.read('/api/v1','SAME','beta'));
});
test('legacy password record is removed and malformed V3 fields are scrubbed',()=>{
 const x=fixture();x.wx.setStorageSync('salesRememberedLoginV1',{version:2,entries:[{baseUrl:'/api/v1',account:'old',password:'Legacy-Synthetic'}]});x.remembered.cleanup();assert.equal(x.wx.getStorageSync('salesRememberedLoginV1'),'');assert.equal(x.remembered.read('/api/v1','OLD').account,'OLD');assert.doesNotMatch(JSON.stringify(x.local),/Legacy-Synthetic|password/);
 x.wx.setStorageSync(KEY,{version:3,entries:[{baseUrl:'/api/v1',workspace:'alpha',account:'new',password:'Corrupt-Synthetic'}]});x.remembered.cleanup();assert.doesNotMatch(JSON.stringify(x.local),/Corrupt-Synthetic|password/);
});
test('forced password change stores account labels only after success',async()=>{
 const x=fixture({login:async()=>({role:'sales',account:'SYNTHETIC_A',mustChangePassword:true})});await submit(x);assert.equal(x.wx.getStorageSync(KEY),'');assert.equal(x.page.data.mustChangePassword,true);
 x.page.setData({newPassword:'Synthetic-New-Only'});await x.page.submitPasswordChange();assert.equal(x.remembered.read('/api/v1','SYNTHETIC_A').account,'SYNTHETIC_A');assert.doesNotMatch(JSON.stringify(x.local),/Synthetic-New-Only|password/);
});
test('unavailable persistent storage reports opt-in failure without breaking successful login',async()=>{
 const local={getItem:()=>null,setItem(){throw Error('synthetic quota');},removeItem(){throw Error('synthetic denied');}};const x=fixture({local});await submit(x);assert.ok(x.app.globalData.session);assert.equal(x.page.data.rememberAccount,false);assert.match(x.notices[0],/未能记住/);
});
function taskFixture(self = false) {
  let user = 'owner', task = {id: '11111111-1111-4111-8111-111111111111', status: 'pending_execution', creator_user_ref_id: self ? 'owner' : 'creator', creator_name: '合成发起人', assignees: [{user_id: 'owner', name: '合成接收人', responsibility: 'owner'}], version_no: 3, events: []};
  const calls = [], stored = [], modals = [];
  const api = {getTask: async () => structuredClone(task), completeTask: async (id, note, version) => {
    calls.push({event: 'complete', note, version}); assert.equal(version, task.version_no);
    task = {...task, status: self ? 'completed' : 'pending_review', completion_note: note, version_no: version + 1};
    task.events.push({id: String(version), event_type: self ? 'complete' : 'submit_completion', note}); return structuredClone(task);
  }, respondTask: async (id, event, note, version) => {
    calls.push({event, note, version}); assert.equal(version, task.version_no);
    task = {...task, status: event === 'approve_completion' ? 'completed' : 'in_progress', last_event_type: event, completion_review_note: note, version_no: version + 1};
    task.events.push({id: String(version), event_type: event, note}); return structuredClone(task);
  }};
  let definition;
  vm.runInNewContext(read('source/miniprogram/pages/task-detail/index.js'), {Page: value => {definition = value;}, wx: {showToast() {}, showModal: value => modals.push(value), setStorageSync: (...args) => stored.push(args), vibrateShort() {}}, getApp: () => ({globalData: {session: {userId: user, role: 'sales'}}}), setTimeout() {}, require: name => name.includes('apiClient') ? api : name.includes('/access') ? {...util('access'),can:()=>true} : name.includes('statusLight') ? {taskLight: () => ({tone: 'yellow'})} : util(name.split('/').pop())});
  const page = {...definition, data: {...definition.data}, setData(value) {Object.assign(this.data, value);}}; page.data.taskId = task.id;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  return {page, calls, stored, modals, async as(next) {user = next; page.loadTask(); await tick();}, async confirm() {modals.at(-1).success({confirm: true}); await tick();}};
}
test('completion requires creator review, permits rejection/resubmission and preserves versions/history', async () => {
  const x = taskFixture(); await x.as('owner'); x.page.markCompleted(); assert.equal(x.modals.length, 0);
  x.page.setData({completionNote: '合成交付第一版'}); x.page.markCompleted(); await x.confirm();
  assert.equal(x.page.data.task.status, 'pending_review'); assert.equal(x.page.data.task.canComplete, false); assert.equal(x.stored.length, 0);
  await x.as('observer'); assert.equal(x.page.data.task.canReview, false); x.page.reviewCompletion({currentTarget: {dataset: {event: 'approve_completion'}}}); assert.equal(x.calls.length, 1);
  await x.as('creator'); assert.equal(x.page.data.task.canReview, true);
  x.page.reviewCompletion({currentTarget: {dataset: {event: 'reject_completion'}}}); assert.equal(x.modals.length, 1);
  x.page.setData({reviewNote: '补齐合成验证'}); x.page.reviewCompletion({currentTarget: {dataset: {event: 'reject_completion'}}}); await x.confirm();
  await x.as('owner'); assert.equal(x.page.data.task.reviewRejected, true); assert.equal(x.page.data.task.reviewNote, '补齐合成验证');
  x.page.setData({completionNote: '合成交付第二版'}); x.page.markCompleted(); await x.confirm();
  await x.as('creator'); x.page.reviewCompletion({currentTarget: {dataset: {event: 'approve_completion'}}}); await x.confirm();
  assert.equal(x.page.data.task.status, 'completed'); assert.equal(x.page.data.task.history.length, 4);
  assert.deepEqual(x.calls.map(call => call.version), [3, 4, 5, 6]); assert.deepEqual(x.calls.map(call => call.event), ['complete', 'reject_completion', 'complete', 'approve_completion']);
});
test('self-created/self-owned tasks complete directly after mandatory explanation', async () => {
  const x = taskFixture(true); await x.as('owner'); assert.equal(x.page.data.task.selfAssigned, true);
  x.page.markCompleted(); assert.equal(x.calls.length, 0);
  x.page.setData({completionNote: '合成自建任务已交付'}); x.page.markCompleted(); await x.confirm();
  assert.equal(x.page.data.task.status, 'completed'); assert.equal(x.stored.length, 1); assert.equal(x.stored[0][0], 'lastCompletedTaskId');
});
