const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const entry=require('../miniprogram/utils/companyEntry');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const base='https://isolated.invalid/api/v1';
function harness(initial=new Map()) {
  const storage=initial,requests=[],nav=[],modals=[],notices=[],modules=new Map();
  let app,page,definition;
  const wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    request:r=>requests.push(r),showToast:r=>notices.push(r.title),showModal:r=>modals.push(r),
    nextTick:f=>f(),switchTab:r=>nav.push({kind:'tab',...r}),reLaunch:r=>nav.push({kind:'launch',...r})};
  storage.set('salesApiBaseUrl',base);
  function load(file) {
    const name=path.resolve(__dirname,'../miniprogram',file);
    // Exercise environment switching independently of the Shenma release profile.
    if (file === 'config.js') return {...require('../miniprogram/config'), API_BASE_URL: base, LOCK_API_BASE_URL: false};
    if(modules.has(name)) return modules.get(name).exports;
    const module={exports:{}};modules.set(name,module);
    vm.runInNewContext(fs.readFileSync(name,'utf8'),{module,exports:module.exports,
      require:relative=>load(path.relative(path.resolve(__dirname,'../miniprogram'),path.resolve(path.dirname(name),relative+'.js'))),
      App:d=>definition=d,Page:d=>definition=d,wx,getApp:()=>app,getCurrentPages:()=>page?[page]:[],
      setTimeout,clearTimeout,Date,Map,Set,Promise,console},{filename:name});
    return module.exports;
  }
  const api=load('utils/apiClient.js');load('app.js');
  app={...definition,globalData:{...definition.globalData,session:null}};
  load('pages/login/index.js');
  page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData:v=>Object.assign(page.data,v)};
  function session(company='alpha',extra={}) {
    const s={role:'sales',workspaceId:company,userId:company+'-person',account:'ADMIN',loginAt:1,
      companyCode:company,companyName:company+'公司',apiBaseUrl:base,remote:true,authMethod:'password',...extra};
    app.globalData.session=s;storage.set('salesSession',s);api.saveAuth(auth(company));return s;
  }
  function auth(company='alpha',extra={}) {return {access_token:company+'-token',refresh_token:company+'-refresh',auth_method:'password',
    actor:{role:'sales',workspace_id:company,user_id:company+'-person',account_code:'ADMIN',company_code:company,company_name:company+'公司',...extra}};}
  return {wx,api,app,page,storage,requests,nav,modals,notices,session,auth,
    reply:(r,statusCode,data={})=>r.success({statusCode,data}),
    lookup:()=>requests.filter(r=>r.url.includes('/auth/company-entry?')),
    logins:()=>requests.filter(r=>r.url.endsWith('/auth/password/login'))};
}
function fill(h,values={}) {Object.assign(h.page.data,{account:'ADMIN',password:'Only-In-Test-2026',agreed:true,...values});}
function replyCompany(h,request=h.lookup().at(-1),code='alpha') {h.reply(request,200,{company_code:code,company_name:code+'公司'});}

for(const options of [{company:'alpha'},{scene:'c=alpha'},{scene:'c%3Dalpha'},{query:{scene:'c%3Dalpha'},scene:1047}]) {
  test('公司入口解析 '+JSON.stringify(options),()=>assert.equal(entry.parse(options).code,'alpha'));
}
test('普通启动无入口，冲突/损坏/越界/额外scene参数不能静默选公司',()=>{
  assert.equal(entry.parse({scene:1001,query:{}}),null);
  for(const options of [{company:''},{company:'a'.repeat(129)},{company:'alpha\n'},
    {scene:'c%ZZalpha'},{scene:'c=alpha&admin=true'},{scene:'c='+ 'a'.repeat(31)},{company:'alpha',scene:'c=beta'},{company:['alpha']}]) assert.ok(entry.parse(options).error);
  assert.equal(entry.validCode('a'.repeat(48)),true);assert.equal(entry.validCode('ab-'),true);
});
test('专属入口只公开精确查询名称，不携带Bearer、不改变会话权限',async()=>{
  const h=harness();h.page.onLoad({scene:'c%3Dalpha'});h.page.onShow();
  assert.equal(h.lookup().length,1);assert.match(h.lookup()[0].url,/company_code=alpha$/);
  assert.equal(h.lookup()[0].header.Authorization,undefined);assert.equal(h.app.globalData.session,null);
  replyCompany(h);await tick();assert.equal(h.page.data.companyName,'alpha公司');assert.equal(h.page.data.companyState,'ready');
  assert.equal(h.nav.length,0);assert.equal(h.page.data.editingCompany,false);
});
test('未知公司保留输入并给重试入口，禁止带原公司/空公司继续登录',async()=>{
  const h=harness();h.page.onLoad({company:'missing'});h.reply(h.lookup()[0],404);await tick();
  assert.match(h.page.data.companyError,/未找到/);assert.equal(h.page.data.workspace,'missing');fill(h);
  const saving=h.page.submitLogin();h.reply(h.lookup().at(-1),404);await saving;
  assert.equal(h.logins().length,0);assert.equal(h.page.data.password,'Only-In-Test-2026');
});
test('非法专属入口不会退回旧公司或唯一账号登录',async()=>{
  const h=harness();h.page.onLoad({scene:'c=bad/code'});fill(h);await h.page.submitLogin();
  assert.equal(h.lookup().length,0);assert.equal(h.logins().length,0);assert.ok(h.page.data.companyError);
});
test('公司查询乱序只展示最新选择，错误响应不能盖掉新公司',async()=>{
  const h=harness();h.page.onLoad({company:'alpha'});const old=h.lookup()[0];
  h.page.onShow({company:'beta'});replyCompany(h,h.lookup()[1],'beta');await tick();
  h.reply(old,404);await tick();assert.equal(h.page.data.workspace,'beta');assert.equal(h.page.data.companyName,'beta公司');assert.equal(h.page.data.companyError,'');
});
test('已登录扫描其他公司保持原会话，取消切换不退出，继续原公司仍可使用',async()=>{
  const h=harness(),original=h.session();h.page.onLoad({company:'beta'});h.page.onShow();replyCompany(h,undefined,'beta');await tick();
  assert.equal(h.app.globalData.session,original);assert.equal(h.page.data.companySwitchRequired,true);assert.equal(h.nav.length,0);
  h.page.confirmCompanySwitch();h.modals[0].success({confirm:false});assert.equal(h.app.globalData.session,original);
  h.page.continueCurrentCompany();assert.equal(h.page.data.workspace,'alpha');assert.equal(h.nav.at(-1).kind,'tab');
});
test('确认切换才退出原会话并重建页面栈，旧业务响应不能返回新公司',async()=>{
  const h=harness();h.session();
  const old=h.api.request({path:'/customers'}),rejected=assert.rejects(old,{code:'SESSION_CHANGED'}),read=h.requests.at(-1);
  h.page.onLoad({company:'beta'});replyCompany(h,undefined,'beta');await tick();h.page.confirmCompanySwitch();h.modals[0].success({confirm:true});
  assert.equal(h.app.globalData.session,null);assert.equal(h.api.getAuth(),null);assert.equal(h.storage.has('salesSession'),false);
  assert.equal(h.nav.at(-1).url,'/pages/login/index?company=beta');
  h.reply(read,200,{items:[{id:'old-company-customer'}]});await rejected;
});
test('隐藏或改选公司后的迟到切换确认不能退出当前身份',async()=>{
  for(const abandon of ['hide','new-entry']) {
    const h=harness(),original=h.session();h.page.onLoad({company:'beta'});replyCompany(h,undefined,'beta');await tick();h.page.confirmCompanySwitch();
    if(abandon==='hide')h.page.onHide();else h.page.onShow({company:'gamma'});
    h.modals[0].success({confirm:true});assert.equal(h.app.globalData.session,original);assert.equal(h.nav.length,0);
  }
});
test('同公司入口恢复已有会话，原强制改密身份继续改密而非再次登录',async()=>{
  for(const mustChangePassword of [false,true]) {
    const h=harness();h.session('alpha',{mustChangePassword});h.page.onLoad({company:'alpha'});h.page.onShow();replyCompany(h);await tick();
    assert.equal(h.page.data.companySwitchRequired,false);
    assert.equal(h.page.data.mustChangePassword,mustChangePassword);
    assert.equal(h.nav.length,mustChangePassword?0:1);
  }
});
test('旧会话没有公司码时不猜归属，明确确认后才切换',async()=>{
  const h=harness();const original=h.session('alpha',{companyCode:undefined,companyName:undefined});
  h.page.onLoad({company:'alpha'});replyCompany(h);await tick();
  assert.equal(h.page.data.companySwitchRequired,true);assert.equal(h.app.globalData.session,original);assert.equal(h.nav.length,0);
});
test('唯一账号无公司码提交后，记住后端确认公司而非空串且不保存密码',async()=>{
  const h=harness();h.page.onLoad();fill(h,{rememberAccount:true});const saving=h.page.submitLogin();
  assert.equal(h.lookup().length,0);assert.equal(h.logins()[0].data.workspace,undefined);
  h.reply(h.logins()[0],200,h.auth());await saving;
  assert.equal(h.page.data.workspace,'alpha');assert.equal(h.app.globalData.session.companyName,'alpha公司');
  const records=h.storage.get('salesAccountHistoryV3').entries;assert.equal(records[0].workspace,'alpha');
  assert.equal(JSON.stringify(records).includes('Only-In-Test-2026'),false);
  const next=harness(h.storage);next.page.onLoad();assert.equal(next.page.data.workspace,'alpha');assert.equal(next.page.data.password,'');
});
test('首次改密后记住的公司仍来自服务器确认身份',async()=>{
  const h=harness();h.page.onLoad();fill(h,{rememberAccount:true});const saving=h.page.submitLogin();
  h.reply(h.logins()[0],200,{...h.auth(),must_change_password:true});await saving;
  assert.equal(h.page.data.workspace,'alpha');assert.equal(h.page.data.mustChangePassword,true);assert.equal(h.nav.length,0);
  h.page.data.newPassword='Changed-Test-Only-2026';const changed=h.page.submitPasswordChange();
  // Secure credential cleanup is asynchronous before the password mutation
  // request is queued; answer the actual request rather than the prior login.
  await tick(); h.reply(h.requests.at(-1),200);await changed;
  assert.equal(h.storage.get('salesAccountHistoryV3').entries[0].workspace,'alpha');assert.equal(h.app.globalData.session.mustChangePassword,false);
});
test('登录在途收到新的公司入口后，旧登录成功不得建立旧公司会话',async()=>{
  const h=harness();h.page.onLoad();fill(h);const saving=h.page.submitLogin(),old=h.logins()[0];
  h.page.onShow({company:'beta'});h.reply(old,200,h.auth());await saving;replyCompany(h,undefined,'beta');await tick();
  assert.equal(h.app.globalData.session,null);assert.equal(h.api.getAuth(),null);assert.equal(h.page.data.workspace,'beta');assert.equal(h.nav.length,0);
});
test('公司查询离页或环境变化后不能回填旧元数据',async()=>{
  for(const abandon of ['unload','base']) {
    const h=harness();h.page.onLoad({company:'alpha'});
    if(abandon==='unload'){h.page.onUnload();h.page.setData=()=>assert.fail('已卸载页面不能更新');}
    else h.storage.set('salesApiBaseUrl','https://other.invalid/api/v1');
    replyCompany(h);await tick();assert.equal(h.page.data.companyName,'');assert.equal(h.nav.length,0);
  }
});
test('App热启动公司入口保留会话并引导登录页处理，不隐式退出',()=>{
  const h=harness(),original=h.session();h.app.onShow({path:'pages/index/index',query:{scene:'c%3Dbeta'},scene:1047});
  assert.equal(h.app.globalData.session,original);assert.equal(h.app._pendingCompanyEntry.code,'beta');assert.equal(h.nav[0].url,'/pages/login/index');
  h.page.onLoad();assert.equal(h.page.data.workspace,'beta');assert.equal(h.app._pendingCompanyEntry,null);
});
test('后端确认公司与专属入口不一致时不建立小程序会话',async()=>{
  const h=harness();h.page.onLoad({company:'alpha'});replyCompany(h);await tick();fill(h);const saving=h.page.submitLogin();
  h.reply(h.logins()[0],200,h.auth('beta'));await saving;assert.equal(h.app.globalData.session,null);assert.equal(h.api.getAuth(),null);assert.equal(h.nav.length,0);assert.match(h.notices.at(-1),/公司.*不一致/);
});
test('记住账号以API环境隔离，跨环境已有会话不会恢复',()=>{
  const stored=new Map([['salesAccountHistoryV3',{version:3,entries:[{baseUrl:'https://other.invalid/api/v1',workspace:'other-company',account:'OTHER'}]}],
    ['salesSession',{role:'sales',remote:true,authMethod:'password',apiBaseUrl:'https://other.invalid/api/v1',companyCode:'other-company'}]]);
  const h=harness(stored);h.app.onLaunch();h.page.onLoad();assert.equal(h.page.data.account,'');assert.equal(h.page.data.workspace,'');assert.equal(h.app.globalData.session,null);
});

test('隐藏期间公司查询完成不会新发密码请求，返回仍可正常登录',async()=>{
  const h=harness();h.page.onLoad();h.page.inputWorkspace({detail:{value:'alpha'}});fill(h);
  const pending=h.page.submitLogin();h.page.onHide();replyCompany(h);await pending;
  assert.equal(h.logins().length,0);assert.equal(h.page.data.loading,false);
  h.page.onShow();const retry=h.page.submitLogin();h.reply(h.logins()[0],200,h.auth());await retry;assert.equal(h.nav.length,1);
});
test('已发送登录在后台完成保留会话，回来才进入首页',async()=>{
  const h=harness();h.page.onLoad();fill(h);const pending=h.page.submitLogin();h.page.onHide();h.reply(h.logins()[0],200,h.auth());await pending;
  assert.equal(h.app.globalData.session.companyCode,'alpha');assert.equal(h.nav.length,0);h.page.onShow();assert.equal(h.nav.length,1);
});
test('卸载后旧登录成功不重新建立会话或写页面',async()=>{
  const h=harness();h.page.onLoad();fill(h);const pending=h.page.submitLogin();h.page.onUnload();h.page.setData=()=>assert.fail('卸载后不得写页面');
  h.reply(h.logins()[0],200,h.auth());await pending;assert.equal(h.app.globalData.session,null);assert.equal(h.api.getAuth(),null);assert.equal(h.nav.length,0);
});
test('新登录的确认公司替换旧空公司标签，不删除其他明确公司同名账号',async()=>{
  const storage=new Map([['salesAccountHistoryV3',{version:3,entries:[{baseUrl:base,workspace:'',account:'ADMIN'},{baseUrl:base,workspace:'beta',account:'ADMIN'}]}]]);
  const h=harness(storage);fill(h,{rememberAccount:true});const pending=h.page.submitLogin();h.reply(h.logins()[0],200,h.auth());await pending;
  assert.deepEqual(Array.from(h.storage.get('salesAccountHistoryV3').entries,v=>v.workspace),['beta','alpha']);
});
test('退出后旧权限回读不能清理新公司的在途权限请求',async()=>{
  const h=harness();h.session();const old=h.app.refreshCapabilities(true),oldRequest=h.requests.at(-1);
  h.app.logout();h.session('beta');const fresh=h.app.refreshCapabilities(true),freshRequest=h.requests.at(-1);
  h.reply(oldRequest,200,{actor:h.auth().actor});await assert.rejects(old,{code:'SESSION_CHANGED'});
  const again=h.app.refreshCapabilities(true);assert.equal(h.requests.filter(r=>r.url.endsWith('/auth/me')).length,2);
  h.reply(freshRequest,200,{actor:h.auth('beta').actor});await Promise.all([fresh,again]);
  assert.equal(h.app.globalData.session.companyCode,'beta');assert.equal(h.app.globalData.session.companyName,'beta公司');
});
test('老actor未提供公司字段时兼容原会话，不凭未验证字段补造公司',async()=>{
  const h=harness();h.page.onLoad();fill(h);const pending=h.page.submitLogin();
  h.reply(h.logins()[0],200,h.auth('alpha',{company_code:undefined,company_name:undefined}));await pending;
  assert.equal(h.app.globalData.session.companyCode,undefined);assert.equal(h.nav.length,1);
});
test('仅岗位名称更新时刷新当前显示而保留未提交表单',async()=>{
  const h=harness();h.session('alpha',{roleName:'一线销售',permissions:null,capabilities:{},permissionVersion:''});
  h.page.data.roleName='一线销售';h.page.data.customer={id:'draft-customer'};h.page.data.description='尚未提交的内容';
  const pending=h.app.refreshCapabilities(true);
  h.reply(h.requests.at(-1),200,{actor:h.auth('alpha',{role_name:'客户经理'}).actor});await pending;
  assert.equal(h.app.globalData.session.roleName,'客户经理');assert.equal(h.page.data.roleName,'客户经理');
  assert.equal(h.page.data.description,'尚未提交的内容');assert.equal(h.page.data.customer.id,'draft-customer');
  assert.equal(h.app.globalData.session.role,'sales');assert.equal(h.nav.length,0);
});

for(const code of ['a','legacy_code','Legacy_Code','a'.repeat(128)]) test('历史公司码兼容 '+code.slice(0,20),async()=>{
  const canonical=code.toLowerCase(),h=harness();h.page.onLoad({company:code});replyCompany(h,undefined,canonical);await tick();
  fill(h,{rememberAccount:true});const pending=h.page.submitLogin();assert.equal(h.logins()[0].data.workspace,canonical);
  h.reply(h.logins()[0],200,h.auth(canonical));await pending;assert.equal(h.app.globalData.session.companyCode,canonical);
  assert.equal(h.storage.get('salesAccountHistoryV3').entries[0].workspace,canonical);
});
test('历史下划线公司 scene 可用，scene 与普通公司码长度分别校验',()=>{
  assert.equal(entry.parse({scene:'c%3DLegacy_Code'}).code,'legacy_code');
  assert.equal(entry.parse({scene:'c='+ 'a'.repeat(30)}).code,'a'.repeat(30));
  assert.ok(entry.parse({scene:'c='+ 'a'.repeat(31)}).error);
});
for(const options of [{company:['alpha','alpha']},{scene:'c=alpha&c=beta'},{company:'bad\ncode'}]) test('已登录遇损坏/重复入口须明确选择留下 '+JSON.stringify(options),()=>{
  const h=harness(),session=h.session();h.page.onLoad(options);h.page.onShow();
  assert.equal(h.app.globalData.session,session);assert.equal(h.nav.length,0);assert.equal(h.page.data.companySwitchRequired,true);assert.ok(h.page.data.companyError);
  h.page.continueCurrentCompany();assert.equal(h.nav.length,1);assert.equal(h.page.data.workspace,'alpha');
});

test('数据库保留混合大小写公司码时查询和登录回执按规范公司码比较',async()=>{
  const h=harness();h.page.onLoad({company:'Legacy_Code.X'});
  assert.match(h.lookup()[0].url,/company_code=legacy_code.x$/);
  replyCompany(h,undefined,'Legacy_Code.X');await tick();
  assert.equal(h.page.data.companyState,'ready');assert.equal(h.page.data.workspace,'legacy_code.x');
  fill(h,{rememberAccount:true});const saving=h.page.submitLogin();
  assert.equal(h.logins()[0].data.workspace,'legacy_code.x');
  h.reply(h.logins()[0],200,h.auth('legacy_code.x',{company_code:'Legacy_Code.X',company_name:'历史公司'}));await saving;
  assert.equal(h.app.globalData.session.companyCode,'legacy_code.x');assert.equal(h.page.data.companyName,'历史公司');
  assert.equal(h.storage.get('salesAccountHistoryV3').entries[0].workspace,'legacy_code.x');assert.equal(h.nav.length,1);
});
