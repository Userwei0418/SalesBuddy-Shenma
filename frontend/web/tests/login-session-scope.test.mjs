import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Execute the real Web scope proxy and imported login/API modules without a
// browser, network, build output or account credentials. Only renderer mounting
// is replaced: this defect happens before the request reaches wx.request.
const root = new URL('../', import.meta.url);
const runtime = fs.readFileSync(new URL('runtime.js', root), 'utf8');
const modules = Object.fromEntries(fs.readdirSync(new URL('source/miniprogram/utils/', root))
  .filter(name => name.endsWith('.js')).map(name => ['utils/' + name.slice(0, -3), fs.readFileSync(new URL('source/miniprogram/utils/' + name, root), 'utf8')]));
for (const id of ['app', 'config', 'pages/login/index']) modules[id] = fs.readFileSync(new URL(`source/miniprogram/${id}.js`, root), 'utf8');
modules['pages/synthetic/index'] = "const api=require('../../utils/apiClient');Page({read(){return api.getCurrentActor();}});";
const instrumented = runtime.replace(/\}\)\(\);\s*$/, `
  globalThis.fixtureRuntime = {
    init(modules) { bundle={modules}; requireModule('app'); installSessionBoundary(); },
    mount(id) {
      const scope=instanceDefinition(id), page={...scope.definition,route:id,_destroyed:false,
        data:clone(scope.definition.data || {}),setData(values){Object.assign(this.data,values);}};
      scope.owner=page; current=page; return {page,api:scope.apiProxy};
    },
    setCurrent(page) { current=page; },
    get app() { return app; },get wx(){return wx;}
  };
})();`);

function harness({remembered = true, companyStatus = 200, mustChangePassword = false} = {}) {
  const storage = new Map(), sessionStorage = new Map(), requests = [], pending = [];
  if (remembered) storage.set('salesAccountHistoryV3', {version: 3, entries: [{baseUrl: '/api/v1', account: 'XS001', workspace: 'synthetic-company', loginType: 'account'}]});
  const element = {addEventListener() {},style:{setProperty() {}}};
  const window = {SALES_MODE:'live', addEventListener() {},dispatchEvent() {},setTimeout, setInterval,
    matchMedia:()=>({matches:true,addEventListener(){}})};
  const context = vm.createContext({window, URL, URLSearchParams, console, setTimeout, clearTimeout,
    CustomEvent: class {constructor(type,options){this.type=type;this.detail=options?.detail;}},
    document: {getElementById:()=>element,addEventListener(){}},
    sessionStorage: {getItem:key=>sessionStorage.get(key),setItem:(key,value)=>sessionStorage.set(key,value),removeItem:key=>sessionStorage.delete(key)},
  });
  vm.runInContext(instrumented, context, {filename:'runtime.js'});
  const host=context.fixtureRuntime;
  let deferCompany = false;
  Object.assign(host.wx, {
    getStorageSync: key=>key==='salesApiBaseUrl'?'/api/v1':storage.get(key),
    setStorageSync: (key,value)=>storage.set(key,value),removeStorageSync:key=>storage.delete(key),
    nextTick() {},showToast() {},
    request(options) {
      const path=new URL(options.url,'https://synthetic.invalid').pathname;
      requests.push({path,method:options.method});
      if(path==='/api/v1/auth/company-entry') {
        const finish=()=>options.success({statusCode:companyStatus,data:companyStatus===200?{company_code:'synthetic-company',company_name:'合成公司'}:{detail:'合成公司不存在'}});
        if(deferCompany)pending.push(finish);else finish();
      } else if(path==='/api/v1/auth/password/login') {
        options.success({statusCode:200,data:{access_token:'synthetic-only',auth_method:'password',must_change_password:mustChangePassword,
          actor:{workspace_id:'synthetic-workspace',user_id:'synthetic-user',account_code:'XS001',role:'sales',company_code:'synthetic-company',company_name:'合成公司'}}});
      } else options.success({statusCode:200,data:{}});
    }
  });
  host.init(modules);
  const mounted=host.mount('pages/login/index'); mounted.page.onLoad();
  return {...mounted,host,storage,requests,pending,deferCompany(value){deferCompany=value;}};
}
function enter(page) {
  page.inputAccount({detail:{value:'XS001'}});
  page.inputPassword({detail:{value:'synthetic-password'}});
  page.toggleAgreement({detail:{checked:true}});
}

test('same login page can switch remembered account and validate its company before first submit', async()=>{
  const h=harness();const history=JSON.stringify(h.storage.get('salesAccountHistoryV3'));
  h.page.switchAccount();enter(h.page);
  assert.equal(h.page.data.workspace,'synthetic-company');
  await h.page.submitLogin();
  assert.deepEqual(h.requests.map(r=>r.path),['/api/v1/auth/company-entry','/api/v1/auth/password/login']);
  assert.equal(h.host.app.globalData.session.account,'XS001');
  assert.equal(h.page.data.companyState,'ready');
  assert.equal(JSON.stringify(h.storage.get('salesAccountHistoryV3')).includes('synthetic-password'),false);
  assert.ok(history.includes('synthetic-company'),'switching does not require deleting account history');
});

test('account switching also permits the subsequent required password change on the current login page', async()=>{
  const h=harness({mustChangePassword:true});h.page.switchAccount();enter(h.page);await h.page.submitLogin();
  assert.equal(h.page.data.mustChangePassword,true);
  h.page.inputNewPassword({detail:{value:'synthetic-new-password'}});await h.page.submitLogin();
  assert.equal(h.requests.filter(r=>r.path==='/api/v1/auth/password').length,1);
  assert.equal(h.host.app.globalData.session.mustChangePassword,false);
});

test('session change still blocks old business scopes and business requests through the retained login scope', async()=>{
  const h=harness();const old=h.host.mount('pages/synthetic/index');h.host.setCurrent(h.page);h.page.switchAccount();
  await assert.rejects(old.page.read(),{code:'SESSION_CHANGED'});
  await assert.rejects(h.api.getCurrentActor(),{code:'SESSION_CHANGED'});
  assert.equal(h.requests.length,0);
});

test('hidden or destroyed login instances cannot use the active login session exception', async()=>{
  for(const state of ['hidden','destroyed']) {
    const h=harness();h.page.switchAccount();
    if(state==='hidden')h.host.mount('pages/synthetic/index');else h.page._destroyed=true;
    await assert.rejects(h.api.getCompanyEntry('synthetic-company'),{code:'SESSION_CHANGED'});
    assert.equal(h.requests.length,0);
  }
});

test('a company response started before logout stays rejected while a fresh login request can proceed', async()=>{
  const h=harness();h.deferCompany(true);
  const old=h.api.getCompanyEntry('synthetic-company').then(()=>null,error=>error);
  h.page.switchAccount();h.pending.shift()();assert.equal((await old).code,'SESSION_CHANGED');
  h.deferCompany(false);
  assert.equal((await h.api.getCompanyEntry('synthetic-company')).company_code,'synthetic-company');
});

test('company lookup failure still blocks login until explicit general-login recovery', async()=>{
  const h=harness({companyStatus:404});h.page.switchAccount();enter(h.page);await h.page.submitLogin();
  assert.equal(h.page.data.companyState,'error');assert.match(h.page.data.companyError,/未找到可用公司/);
  assert.equal(h.requests.filter(r=>r.path==='/api/v1/auth/password/login').length,0);
  h.page.useSimpleLogin();h.page.inputPassword({detail:{value:'synthetic-password'}});await h.page.submitLogin();
  assert.equal(h.requests.filter(r=>r.path==='/api/v1/auth/password/login').length,1);
});
