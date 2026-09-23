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
  const module={exports:{}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/utils/rememberedLogin.js','utf8'),{module,wx});
  vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/login/index.js','utf8'),{
    Page:d=>{definition=d;},getApp:()=>app,require:name=>name.includes("rememberedLogin")?module.exports:({changePassword,getBaseUrl:()=>"https://test.invalid/api/v1"}),wx,
  });
  const page={...definition,data:{...definition.data},setData:data=>Object.assign(page.data,data)};
  return {page,notices,saved,calls,app,storage,wx};
}

test('账号与密码交给服务端验证，任意真实密码不会被本地样例口令拒绝',async()=>{
  const {page,calls,saved}=loginPage(async()=>({role:'sales',mustChangePassword:false}));
  assert.equal(page.data.account,'');assert.equal(page.data.password,'');
  Object.assign(page.data,{agreed:true,account:' native-account ',password:'Real-User-Password-2026'});
  await page.submitLogin();
  assert.deepEqual(calls,[[null,'NATIVE-ACCOUNT','Real-User-Password-2026']]);
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

const rememberedKey='salesRememberedLoginV1';
const credentials={account:'member@example.com',password:'Isolated-Remember-2026',agreed:true};

test('默认不勾选，成功登录不存密码；主动勾选成功才存且重新打开回填为隐藏状态',async()=>{
  const plain=loginPage(async()=>({role:'sales'}));plain.page.onLoad();
  assert.equal(plain.page.data.rememberPassword,false);
  Object.assign(plain.page.data,credentials);await plain.page.submitLogin();
  assert.equal(plain.storage.has(rememberedKey),false);
  const x=loginPage(async()=>({role:'sales'}));Object.assign(x.page.data,credentials);
  x.page.toggleRememberPassword();assert.equal(x.storage.has(rememberedKey),false);
  await x.page.submitLogin();assert.equal(x.storage.get(rememberedKey).entries[0].password,credentials.password);
  const reopened=loginPage(async()=>{},undefined,x.storage);reopened.page.onLoad();
  assert.equal(reopened.page.data.account,credentials.account);
  assert.equal(reopened.page.data.password,credentials.password);
  assert.equal(reopened.page.data.rememberPassword,true);
  assert.equal(reopened.page.data.passwordVisible,false);
  assert.equal(reopened.page.data.agreed,false);
});

test('登录失败不存密码，未经同意也不存',async()=>{
  const x=loginPage(async()=>{throw Object.assign(Error('denied'),{statusCode:401});});
  Object.assign(x.page.data,credentials,{rememberPassword:true});await x.page.submitLogin();
  assert.equal(x.storage.has(rememberedKey),false);
  x.page.data.agreed=false;await x.page.submitLogin();assert.equal(x.calls.length,1);
});

test('取消勾选删除当前记录；切换账号只清空输入并保留记忆',async()=>{
  const x=loginPage(async()=>({role:'sales'}));Object.assign(x.page.data,credentials,{rememberPassword:true});
  await x.page.submitLogin();x.page.toggleRememberPassword();assert.equal(x.storage.has(rememberedKey),false);
  const fresh=loginPage(async()=>{},undefined,x.storage);fresh.page.onLoad();assert.equal(fresh.page.data.password,'');
  Object.assign(x.page.data,credentials,{rememberPassword:true});await x.page.submitLogin();
  x.page.switchAccount();assert.equal(x.storage.has(rememberedKey),true);
  assert.equal(x.page.data.rememberPassword,false);assert.equal(x.page.data.password,'');assert.equal(x.page.data.account,'');
});

test('手动修改账号不带出旧密码，大小写与首尾空格不视为新账号',async()=>{
  const x=loginPage(async()=>({role:'sales'}));Object.assign(x.page.data,credentials,{rememberPassword:true});await x.page.submitLogin();
  const fresh=loginPage(async()=>{},undefined,x.storage);fresh.page.onLoad();
  fresh.page.inputAccount({detail:{value:' MEMBER@example.com '}});assert.equal(fresh.page.data.password,credentials.password);
  fresh.page.inputAccount({detail:{value:'other@example.com'}});
  assert.equal(fresh.page.data.password,'');assert.equal(fresh.storage.has(rememberedKey),true);assert.equal(fresh.page.data.rememberPassword,false);
  fresh.page.inputAccount({detail:{value:credentials.account}});assert.equal(fresh.page.data.password,credentials.password);
});

test('请求中取消勾选或切换账号，迟到成功不能保存旧凭据',async()=>{
  for(const action of ['toggleRememberPassword','switchAccount']){
    let done;const x=loginPage(()=>new Promise(r=>{done=r;}));Object.assign(x.page.data,credentials,{rememberPassword:true});
    const pending=x.page.submitLogin();x.page[action]();done({role:'sales'});await pending;
    assert.equal(x.storage.has(rememberedKey),false);
  }
});

test('强制改密不记初始密码，成功后只记提交的新密码',async()=>{
  let finish;const x=loginPage(async()=>({role:'sales',mustChangePassword:true}),()=>new Promise(r=>{finish=r;}));
  Object.assign(x.page.data,credentials,{rememberPassword:true});await x.page.submitLogin();assert.equal(x.storage.has(rememberedKey),false);
  x.page.data.newPassword='Isolated-Changed-2026';const pending=x.page.submitPasswordChange();
  x.page.inputNewPassword({detail:{value:'not-the-submitted-value'}});finish();await pending;
  assert.equal(x.storage.get(rememberedKey).entries[0].password,'Isolated-Changed-2026');
});

test('存储写入失败不阻断登录；清除失败不能声称取消，但不影响切换账号',async()=>{
  const x=loginPage(async()=>({role:'sales'}));Object.assign(x.page.data,credentials,{rememberPassword:true});
  x.wx.setStorageSync=()=>{throw Error('storage unavailable');};await x.page.submitLogin();
  assert.equal(x.saved.length,1);assert.equal(x.page.data.rememberPassword,false);assert.match(x.notices[0],/未能记住/);
  x.page.data.rememberPassword=true;x.wx.removeStorageSync=()=>{throw Error('unavailable');};
  x.page.toggleRememberPassword();assert.equal(x.page.data.rememberPassword,true);
  x.page.switchAccount();assert.equal(x.page.data.account,'');
});

test('不同API环境、损坏缓存不回填；离开页面重新隐藏密码',()=>{
  for(const record of [null,{}, {version:1,baseUrl:'https://other.invalid',...credentials},
    {version:1,baseUrl:'https://test.invalid/api/v1',account:'user',password:12}]) {
    const x=loginPage(async()=>{},undefined,new Map([[rememberedKey,record]]));x.page.onLoad();
    assert.equal(x.page.data.password,'');assert.equal(x.page.data.rememberPassword,false);
  }
  const x=loginPage(async()=>{});x.page.data.passwordVisible=true;x.page.onHide();assert.equal(x.page.data.passwordVisible,false);
});

test('A记住→切换B记住→再输入A，回填各自密码；取消A不影响B',async()=>{
  const x=loginPage(async()=>({role:'sales'}));
  Object.assign(x.page.data,credentials,{rememberPassword:true});await x.page.submitLogin();
  x.page.switchAccount();
  x.page.inputAccount({detail:{value:'second@example.com'}});
  assert.equal(x.page.data.password,'');assert.equal(x.page.data.rememberPassword,false);
  Object.assign(x.page.data,{password:'Isolated-Second-2026',agreed:true,rememberPassword:true});
  await x.page.submitLogin();assert.equal(x.storage.get(rememberedKey).entries.length,2);
  x.page.switchAccount();x.page.inputAccount({detail:{value:' MEMBER@EXAMPLE.COM '}});
  assert.equal(x.page.data.password,credentials.password);assert.equal(x.page.data.passwordVisible,false);
  assert.equal(x.page.data.rememberPassword,true);
  x.page.toggleRememberPassword();assert.equal(x.storage.get(rememberedKey).entries.length,1);
  x.page.switchAccount();x.page.inputAccount({detail:{value:credentials.account}});
  assert.equal(x.page.data.password,'');assert.equal(x.page.data.rememberPassword,false);
  x.page.inputAccount({detail:{value:'second@example.com'}});
  assert.equal(x.page.data.password,'Isolated-Second-2026');assert.equal(x.page.data.rememberPassword,true);
});

test('旧单账号记录可回填且保存第二账号时迁移，取消只删目标环境',async()=>{
  const legacy={version:1,baseUrl:'https://test.invalid/api/v1',...credentials};
  const storage=new Map([[rememberedKey,legacy]]);
  const x=loginPage(async()=>({role:'sales'}),undefined,storage);x.page.onLoad();
  assert.equal(x.page.data.password,credentials.password);
  x.page.switchAccount();Object.assign(x.page.data,{...credentials,account:'new@example.com',rememberPassword:true});
  await x.page.submitLogin();assert.equal(storage.get(rememberedKey).entries.length,2);
  storage.get(rememberedKey).entries.push({baseUrl:'https://other.invalid',...credentials});
  x.page.inputAccount({detail:{value:credentials.account}});x.page.toggleRememberPassword();
  const entries=storage.get(rememberedKey).entries;
  assert.equal(entries.length,2);assert.equal(entries.some(v=>v.baseUrl==='https://other.invalid'),true);
});

test('当前账号密码被拒绝只删除自己记录，不删除其他已记住账号',async()=>{
  const storage=new Map([[rememberedKey,{version:2,entries:[
    {baseUrl:'https://test.invalid/api/v1',...credentials},
    {baseUrl:'https://test.invalid/api/v1',account:'other@example.com',password:'Isolated-Other-2026'}]}]]);
  const x=loginPage(async()=>{throw Object.assign(Error('denied'),{statusCode:401});},undefined,storage);
  x.page.inputAccount({detail:{value:credentials.account}});x.page.data.agreed=true;
  await x.page.submitLogin();
  assert.equal(storage.get(rememberedKey).entries.length,1);
  assert.equal(storage.get(rememberedKey).entries[0].account,'other@example.com');
});

test('账号前缀推荐只显示本机当前环境账号，点选后补全并回填对应密码',()=>{
  const storage=new Map([[rememberedKey,{version:2,entries:[
    {baseUrl:'https://test.invalid/api/v1',account:'hanqiwei@example.com',password:'Isolated-Han-Only'},
    {baseUrl:'https://other.invalid/api/v1',account:'hanqi-other@example.com',password:'Isolated-Other'},
    {baseUrl:'https://test.invalid/api/v1',account:'zhou@example.com',password:'Isolated-Zhou'}]}]]);
  const x=loginPage(async()=>{},undefined,storage);
  x.page.inputAccount({detail:{value:'HaNqI'}});
  assert.deepEqual(Array.from(x.page.data.accountSuggestions),['hanqiwei@example.com']);
  assert.equal(x.page.data.password,'');
  assert.equal(JSON.stringify(x.page.data.accountSuggestions).includes('Isolated'),false);
  x.page.blurAccount(); // blur from a real tap must not remove the target before its tap event
  x.page.selectAccountSuggestion({currentTarget:{dataset:{account:'hanqiwei@example.com'}}});
  assert.equal(x.page.data.account,'hanqiwei@example.com');assert.equal(x.page.data.password,'Isolated-Han-Only');
  assert.equal(x.page.data.rememberPassword,true);assert.equal(x.page.data.passwordVisible,false);
  assert.equal(x.page.data.accountSuggestions.length,0);assert.equal(x.page.data.agreed,false);
  x.page.switchAccount();assert.equal(x.page.data.accountSuggestions.length,0);
});

test('无匹配和空输入不推荐；完整账号不重复推荐，推荐最多5条',()=>{
  const entries=Array.from({length:8},(_,i)=>({baseUrl:'https://test.invalid/api/v1',account:'han'+i+'@example.com',password:'Isolated-Only'}));
  const x=loginPage(async()=>{},undefined,new Map([[rememberedKey,{version:2,entries}]]));
  for(const value of ['','unknown','han7@example.com']){
    x.page.inputAccount({detail:{value}});assert.equal(x.page.data.accountSuggestions.length,0);
  }
  x.page.inputAccount({detail:{value:'han'}});assert.equal(x.page.data.accountSuggestions.length,5);
  x.page.hideAccountSuggestions();assert.equal(x.page.data.accountSuggestions.length,0);
});
