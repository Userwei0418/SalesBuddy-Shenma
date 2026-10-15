const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loginPage(login, changePassword=async()=>{}, storage=new Map()) {
  let definition;
  const notices=[], saved=[], calls=[];
  const app={globalData:{},loginWithApi:(...args)=>{calls.push(args);return login(...args).then(s=>{app.globalData.session=s;return s;});},logout(){app.globalData.session=null;}};
  const wx={showToast:r=>notices.push(r.title),switchTab:r=>saved.push(r.url),
    getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),
    removeStorageSync:key=>storage.delete(key),nextTick:f=>f()};
  const module={exports:{}},secureModule={exports:{}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/utils/rememberedLogin.js','utf8'),{module,wx});
  vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/utils/encryptedCredentials.js','utf8'),{module:secureModule,wx,Map});
  vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/login/index.js','utf8'),{
    Page:d=>{definition=d;},getApp:()=>app,require:name=>name.includes("encryptedCredentials")?secureModule.exports:name.includes("rememberedLogin")?module.exports:name.includes("companyEntry")?require("../miniprogram/utils/companyEntry"):({changePassword,getBaseUrl:()=>"https://test.invalid/api/v1",getCompanyEntry:async code=>({company_code:code,company_name:"公司 "+code}),cancelPendingLogin(){}}),wx,
  });
  const page={...definition,data:{...definition.data},setData:data=>Object.assign(page.data,data)};
  return {page,notices,saved,calls,app,storage,wx};
}

test('账号与密码交给服务端验证，任意真实密码不会被本地样例口令拒绝',async()=>{
  const {page,calls,saved}=loginPage(async()=>({role:'sales',mustChangePassword:false}));
  assert.equal(page.data.account,'');assert.equal(page.data.password,'');
  Object.assign(page.data,{agreed:true,account:' native-account ',password:'Real-User-Password-2026'});
  await page.submitLogin();
  assert.deepEqual(calls,[[null,'NATIVE-ACCOUNT','Real-User-Password-2026',undefined,'auto']]);
  assert.equal(saved.length,1);assert.equal(page.data.password,'');
});

test('服务端拒绝密码时不进入业务页，初始密码须修改成功才进入',async()=>{
  const denied=loginPage(async()=>{throw Error('账号或密码不正确');});
  Object.assign(denied.page.data,{agreed:true,account:'user',password:'123456'});
  await denied.page.submitLogin();assert.equal(denied.saved.length,0);assert.match(denied.notices[0],/密码/);
  const changed=[];
  const first=loginPage(async()=>({role:'sales',mustChangePassword:true}),async(...args)=>changed.push(args));
  Object.assign(first.page.data,{agreed:true,account:'user',password:'Initial-Password-2026'});
  await first.page.submitLogin();assert.equal(first.saved.length,0);assert.equal(first.page.data.mustChangePassword,true);
  first.page.data.newPassword='Changed-Password-2026';await first.page.submitLogin();
  assert.deepEqual(changed,[['Initial-Password-2026','Changed-Password-2026']]);
  assert.equal(first.app.globalData.session.mustChangePassword,false);assert.equal(first.saved.length,1);
});

test('重复点击只发一次登录请求，身份以服务端验证结果为准',async()=>{
  let resolve;
  const x=loginPage(()=>new Promise(r=>{resolve=r;}));Object.assign(x.page.data,{agreed:true,account:'user',password:'Any-Password-2026'});
  const running=x.page.submitLogin();x.page.submitLogin();assert.equal(x.calls.length,1);
  resolve({role:'manager',roleName:'总经理'});await running;
  assert.equal(x.saved.length,1);assert.equal(x.app.globalData.session.role,'manager');assert.equal(x.page.data.password,'');
});


test('首次打开不替用户同意隐私，未勾选不会发送密码请求',async()=>{
  const x=loginPage(async()=>({role:'sales'}));
  assert.equal(x.page.data.agreed,false);
  Object.assign(x.page.data,{account:'user',password:'Private-Password-2026'});
  await x.page.submitLogin();
  assert.equal(x.calls.length,0);assert.match(x.notices[0],/隐私/);
  x.page.toggleAgreement();await x.page.submitLogin();assert.equal(x.calls.length,1);
});

test('打开隐私入口失败不代替用户勾选，说明链接阻止勾选事件冒泡',()=>{
  const x=loginPage(async()=>({role:'sales'}));x.page.openPrivacy();
  assert.equal(x.page.data.agreed,false);assert.match(x.notices[0],/隐私指引暂不可用/);
  assert.match(fs.readFileSync(__dirname+'/../miniprogram/pages/login/index.wxml','utf8'),/catchtap="openPrivacy"/);
});

test('切换账号退出强制改密并清空凭据，另一账号可以直接登录',async()=>{
  const x=loginPage(async()=>({account:'OTHER',role:'sales',mustChangePassword:false}));
  x.app.globalData.session={account:'FIRST',mustChangePassword:true};
  x.page.onShow();
  Object.assign(x.page.data,{password:'test-original-password',newPassword:'test-new-password',passwordVisible:true,agreed:true});
  x.page.switchAccount();
  assert.equal(x.app.globalData.session,null);
  for(const field of ['account','password','newPassword']) assert.equal(x.page.data[field],'');
  for(const field of ['mustChangePassword','passwordVisible','agreed','loading']) assert.equal(x.page.data[field],false);
  x.page.onShow();assert.equal(x.page.data.mustChangePassword,false);
  Object.assign(x.page.data,{account:'other',password:'test-other-password',agreed:true});
  await x.page.submitLogin();assert.equal(x.saved.length,1);
  assert.equal(x.calls[0][1],'OTHER');
});

test('旧会话失效后重进登录页解除改密状态',()=>{
  const x=loginPage(async()=>({role:'sales'}));
  Object.assign(x.page.data,{mustChangePassword:true,password:'old',newPassword:'new',loading:true});
  x.page.onShow();
  assert.equal(x.page.data.mustChangePassword,false);
  assert.equal(x.page.data.password,'');assert.equal(x.page.data.newPassword,'');
});

test('切换后旧改密响应不能恢复身份、跳转或结束新账号请求',async()=>{
  let finishChange, finishLogin;
  const x=loginPage(()=>new Promise(resolve=>{finishLogin=resolve;}),()=>new Promise(resolve=>{finishChange=resolve;}));
  x.app.globalData.session={account:'FIRST',mustChangePassword:true};
  Object.assign(x.page.data,{mustChangePassword:true,password:'test-old-password',newPassword:'test-new-password'});
  const old=x.page.submitPasswordChange();
  x.page.switchAccount();
  Object.assign(x.page.data,{account:'other',password:'test-other-password',agreed:true});
  const next=x.page.submitLogin();
  finishChange();await old;
  assert.equal(x.app.globalData.session,null);assert.equal(x.saved.length,0);assert.equal(x.page.data.loading,true);
  finishLogin({account:'OTHER',role:'sales'});await next;
  assert.equal(x.saved.length,1);assert.equal(x.app.globalData.session.account,'OTHER');
});

test('切换后旧登录失败不能覆盖新账号表单或弹出旧错误',async()=>{
  let reject;
  const x=loginPage(()=>new Promise((resolve,fail)=>{reject=fail;}));
  Object.assign(x.page.data,{account:'first',password:'test-password',agreed:true});
  const old=x.page.submitLogin();x.page.switchAccount();x.page.inputAccount({detail:{value:'other'}});
  reject(Error('SESSION_CHANGED'));await old;
  assert.equal(x.page.data.account,'other');assert.equal(x.notices.length,0);assert.equal(x.saved.length,0);
});

const rememberedKey='salesAccountHistoryV3',legacyKey='salesRememberedLoginV1';
const credentials={account:'ADMIN',password:'Isolated-Only-In-Memory-2026',agreed:true};
test('remember account stores labels only, never old or new passwords',async()=>{
 const x=loginPage(async()=>({role:'sales'}));Object.assign(x.page.data,credentials);
 x.page.toggleRememberAccount();await x.page.submitLogin();
 const stored=x.storage.get(rememberedKey);assert.equal(stored.entries[0].account,'ADMIN');
 assert.equal(JSON.stringify([...x.storage]).includes(credentials.password),false);
 const next=loginPage(async()=>{},undefined,x.storage);next.page.onLoad();
 assert.equal(next.page.data.account,'ADMIN');assert.equal(next.page.data.password,'');
 assert.equal(next.page.data.rememberAccount,true);next.page.toggleRememberAccount();
 assert.equal(next.storage.has(rememberedKey),false);
});
test('legacy single and multi-account passwords are removed during migration',()=>{
 for(const legacy of [{version:1,baseUrl:'https://test.invalid/api/v1',...credentials},
  {version:2,entries:[{baseUrl:'https://test.invalid/api/v1',...credentials},{baseUrl:'https://other.invalid',account:'OTHER',password:'other-secret'}]}]) {
  const storage=new Map([[legacyKey,legacy]]);const x=loginPage(async()=>{},undefined,storage);x.page.onLoad();
  assert.equal(storage.has(legacyKey),false);assert.equal(x.page.data.password,'');
  const serialized=JSON.stringify([...storage]);assert.ok(!serialized.includes(credentials.password));assert.ok(!serialized.includes('other-secret'));
  assert.equal(x.page.data.account,'ADMIN');
 }
});
test('company histories never mix account suggestions or retain password on selection',async()=>{
 const x=loginPage(async(_role,_account,_password,workspace)=>({role:'sales',companyCode:workspace}));
 x.page.inputWorkspace({detail:{value:'alpha'}});Object.assign(x.page.data,credentials,{rememberAccount:true});await x.page.submitLogin();
 x.page.switchAccount();x.page.inputWorkspace({detail:{value:'beta'}});Object.assign(x.page.data,credentials,{account:'ADMIN2',rememberAccount:true});await x.page.submitLogin();
 x.page.switchAccount();
 x.page.inputWorkspace({detail:{value:'alpha'}});x.page.inputAccount({detail:{value:'A'}});
 assert.deepEqual(Array.from(x.page.data.accountSuggestions),['ADMIN']);
 x.page.selectAccountSuggestion({currentTarget:{dataset:{account:'ADMIN'}}});
 assert.equal(x.page.data.password,'');assert.equal(x.page.data.workspace,'alpha');
 x.page.inputWorkspace({detail:{value:'beta'}});x.page.inputAccount({detail:{value:'A'}});
 assert.deepEqual(Array.from(x.page.data.accountSuggestions),['ADMIN2']);
});
test('account history remains opt-in and storage failure does not cancel successful login',async()=>{
 const x=loginPage(async()=>({role:'sales'}));Object.assign(x.page.data,credentials);await x.page.submitLogin();
 assert.equal(x.storage.has(rememberedKey),false);
 Object.assign(x.page.data,credentials,{rememberAccount:true});x.wx.setStorageSync=()=>{throw Error('storage unavailable');};
 await x.page.submitLogin();assert.equal(x.saved.length,2);assert.equal(x.page.data.password,'');assert.match(x.notices.at(-1),/未能记住账号/);
});
test('history migration removes old password even if saving sanitized labels fails',()=>{
 const storage=new Map([[legacyKey,{version:1,baseUrl:'https://test.invalid/api/v1',...credentials}]]);
 const x=loginPage(async()=>{},undefined,storage);x.wx.setStorageSync=()=>{throw Error('unavailable');};x.page.onLoad();
 assert.equal(storage.has(legacyKey),false);assert.equal(x.page.data.password,'');
});
test('a failed login or an abandoned request never remembers input credentials',async()=>{
 const x=loginPage(async()=>{throw Object.assign(Error('incorrect'),{statusCode:401});});Object.assign(x.page.data,credentials,{rememberAccount:true});await x.page.submitLogin();assert.equal(x.storage.has(rememberedKey),false);
 let complete;const late=loginPage(()=>new Promise(r=>complete=r));Object.assign(late.page.data,credentials,{rememberAccount:true});
 const pending=late.page.submitLogin();late.page.onUnload();complete({role:'sales'});await pending;
 assert.equal(late.storage.has(rememberedKey),false);
});

test('SaaS 同名账号显式传公司码，切换公司清空密码且不带入旧账号密码',async()=>{
  const x=loginPage(async()=>({role:'sales'}));
  Object.assign(x.page.data,{account:'admin',password:'old-password',rememberAccount:true,agreed:true});
  x.page.inputWorkspace({detail:{value:' Customer-A '}});
  assert.equal(x.page.data.workspace,'customer-a');assert.equal(x.page.data.password,'');
  assert.equal(x.page.data.rememberAccount,false);
  x.page.inputAccount({detail:{value:'admin'}});
  x.page.inputPassword({detail:{value:'tenant-new-password'}});
  await x.page.submitLogin();
  assert.deepEqual(x.calls,[[null,'ADMIN','tenant-new-password','customer-a','auto']]);
  assert.equal(x.storage.has('salesRememberedLoginV1'),false);
});
