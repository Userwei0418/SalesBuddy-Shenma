const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {pathToFileURL} = require('node:url');
const assets = path.resolve(__dirname, '../src/sales_backend/web/assets');
const helpers = () => import(pathToFileURL(path.join(assets, 'company-login.js')).href);
const deferred = () => {let resolve, reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const tick = () => new Promise(resolve=>setImmediate(resolve));
function storage(initial) {
  const values=new Map(initial?Object.entries(initial):[]);
  return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),values};
}
async function consoleHarness({search='',hint,resolveCompany,loginAccount,logoutAccount,permissions={'access.console':true,'customer.read':true}}={}) {
  const h=await helpers(),nodes=new Map(),calls={shell:0,login:0,password:0,logouts:0};
  let activeForm,location=new URL('https://sales.example/admin'+search);
  function node(id) {
    if(nodes.has(id))return nodes.get(id);
    const value={id,hidden:false,isConnected:true,value:'',checked:false,disabled:false,textContent:'',innerHTML:'',
      close(){this.open=false;},focus(){},addEventListener(){},setAttribute(){},removeAttribute(){},
      classList:{add(){},remove(){},toggle(){return false;}},querySelectorAll(){return [];}};
    nodes.set(id,value);return value;
  }
  const app=node('#app');let html='';
  Object.defineProperty(app,'innerHTML',{get:()=>html,set(value){
    html=value;if(activeForm)activeForm.isConnected=false;
    for(const [,id] of value.matchAll(/id="([^"]+)"/g))nodes.set('#'+id,{...node('#'+id),isConnected:true});
    if(value.includes('id="login-form"')) {
      const elements=Object.fromEntries(['workspace','account','password','remember'].map(name=>[name,{name,value:'',checked:false,disabled:false,focus(){}}]));
      const submit={disabled:false};activeForm={isConnected:true,elements,querySelectorAll:()=>[...Object.values(elements),submit],querySelector:()=>submit};
      nodes.set('#login-form',activeForm);nodes.set('#login-password',elements.password);
    }
  }});
  const state={actor:null,permissions:null,page:'customers',filters:{},offset:0};
  const context={...h,URL,URLSearchParams,AbortController,Map,console,Date,setTimeout,clearTimeout,
    state,roles:{},esc:value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),
    $:(selector,root)=>root?.querySelector?root.querySelector(selector):node(selector),
    clearSession:()=>{state.actor=null;state.permissions=null;},
    loginAccount:loginAccount||(async()=>{throw Error('not used');}),
    logoutAccount:async()=>{calls.logouts++;state.actor=null;state.permissions=null;return logoutAccount?.();},
    refreshToken:async()=>{throw Error('not used');},toast(){},field(){return '';},
    api:async route=>route==='/auth/me'?{actor:{company_code:'company-a',company_name:'公司甲',display_name:'管理员',account_code:'ADMIN',role:'administrator'}}:
      route==='/permissions/me'?{permissions}:{},
    loadCompanies:async()=>{},chooseCompany:async()=>{},companyLabel:()=>'',
    document:{querySelector:node,querySelectorAll:()=>[],addEventListener(){}},
    window:{get location(){return location;},localStorage:hint||storage(),history:{replaceState(_state,_title,url){location=new URL(url,location);}},addEventListener(){},scrollTo(){},confirm:()=>true},
    FormData:class {constructor(form){this.values=new Map(Object.entries(form.elements).filter(([key,value])=>!value.disabled&&(key!=='remember'||value.checked)).map(([key,value])=>[key,value.value]));}get(key){return this.values.get(key)??null;}has(key){return this.values.has(key);}},
  };
  for(const name of ['customers','claims','customerCreationRequests','materials','opportunityInitialization','opportunities','visits','accounts','aiUsage','audit','systemLogs','modelApis','businessActivities','agentRuns','companyRules','agentExecution','partners','taskInbox','targets','targetRequests','permissionsPage','feishuSync','supportAccess'])context[name]=()=>({html:'隔离业务页'});
  if(resolveCompany)context.resolveCompany=resolveCompany;
  let source=fs.readFileSync(path.join(assets,'console.js'),'utf8').replace(/^import[\s\S]*?from\s+['"][^'"]+['"];\n/gm,'').replace('export const nav','const nav');
  source=source.slice(0,source.indexOf('try {\n  const s = await refreshToken();'));
  source+='\nconst actualLogin=login,actualShell=shell;shell=async()=>{__calls.shell++;};passwordScreen=()=>{__calls.password++;};globalThis.__ui={login:actualLogin,renderShell:actualShell,acceptCompanyEntry,canonicalCompanyURL,entry:()=>companyEntry};';
  context.__calls=calls;vm.runInNewContext(source,context,{filename:'console.js'});
  return {ui:context.__ui,nodes,calls,state,get form(){return activeForm;},get html(){return html;},get location(){return location;}};
}

test('company query is exact; duplicate or malformed explicit entries never become no-entry hints',async()=>{
  const h=await helpers();assert.deepEqual(h.readCompanyEntry(''),{code:'',invalid:false});
  assert.deepEqual(h.readCompanyEntry('?company=DEMO-ONE'),{code:'demo-one',invalid:false});
  for(const search of ['?company=','?company=one&company=two','?company=bad%00code']) {
    const value=h.readCompanyEntry(search);assert.equal(value.invalid,true);assert.equal(value.code,'');
  }
});

test('material reader sees the product-line/material/rules entry in the actual sidebar',async()=>{
  const view=await consoleHarness({permissions:{'access.console':true,'material.read':true}});
  await view.ui.renderShell();
  assert.equal((view.html.match(/data-nav="materials"/g)||[]).length,1);
  assert.match(view.html,/data-nav="materials"[^>]*title="产品线\/物料与规则"/);
  assert.equal(view.state.page,'materials');
});

test('material management or a hash alone cannot reveal a page without material.read',async()=>{
  const view=await consoleHarness({search:'#materials',permissions:{'access.console':true,'customer.read':true,'material.manage':true}});
  await view.ui.renderShell();
  assert.doesNotMatch(view.html,/data-nav="materials"/);
  assert.equal(view.state.page,'customers');
});

test('every registered management page is reachable once through the grouped sidebar',async()=>{
  const view=await consoleHarness({permissions:new Proxy({}, {get:()=>true})});
  await view.ui.renderShell();
  const rendered=[...view.html.matchAll(/data-nav="([^"]+)"/g)].map(match=>match[1]);
  const declared=[...fs.readFileSync(path.join(assets,'console.js'),'utf8').split('export const nav = [')[1].split('];')[0].matchAll(/\["([^"]+)",/g)].map(match=>match[1]);
  assert.deepEqual([...rendered].sort(),declared.sort());
  assert.equal(new Set(rendered).size,rendered.length);
});

for(const manage of [false,true])test(`material page preserves read/manage controls (${manage})`,async()=>{
  const core=await import(pathToFileURL(path.join(assets,'core.js')).href);
  const page=await import(pathToFileURL(path.join(assets,'materials.js')).href);
  core.clearSession();Object.assign(core.state,{token:'synthetic',actor:{workspace_id:'w',user_id:'u',role:'operations'},
    permissions:{'material.read':true,'material.manage':manage},offset:0,filters:{}});
  const calls=[];
  global.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>({total:1,items:[
    {id:'m',code:'M',name:'测试物料',product_line_name:'测试产品线',unit:'项',catalog_price:'100',tax_rate:'0.06',recognition_rule:'manual',is_active:true,version_no:1}
  ]})};};
  const view=await page.materials();
  for(const action of ['lines','create','edit'])assert.equal(view.html.includes(`data-action="${action}"`),manage);
  assert.match(view.html,/测试产品线/);
  assert.equal(calls.length,1);assert.equal(calls[0].options.method,'GET');
  assert.equal(new URL(calls[0].url,'https://test.invalid').searchParams.get('status'),manage?'all':'active');
});

test('legacy canonical codes supported by the API remain usable as company entries',async()=>{
  const h=await helpers();assert.equal(h.normalizeCompanyCode('legacy_code'),'legacy_code');
  assert.equal(h.normalizeCompanyCode('历史中文公司码'),'历史中文公司码');
  assert.deepEqual(h.readCompanyEntry('?company=legacy_code'),{code:'legacy_code',invalid:false});
  const company=await h.resolveCompany('legacy_code',{fetcher:async()=>({ok:true,json:async()=>({company_code:'legacy_code',company_name:'旧公司'})})});
  assert.equal(company.company_code,'legacy_code');
});

test('remembering stores canonical hints only and opting out removes previous account and company',async()=>{
  const h=await helpers(),s=storage();
  h.saveLoginHint(s,{company_code:'DEMO-ONE',company_name:'公司一',account_code:'USER',password:'DO-NOT-STORE',token:'DO-NOT-STORE'},true);
  assert.deepEqual(h.readLoginHint(s),{remember:true,company_code:'demo-one',company_name:'公司一',account_code:'USER'});
  assert.ok(![...s.values.values()].join('').includes('DO-NOT-STORE'));
  h.saveLoginHint(s,{},false);assert.deepEqual(h.readLoginHint(s),{remember:false});
  assert.ok(![...s.values.values()].join('').includes('USER'));
  const blocked={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
  assert.deepEqual(h.readLoginHint(blocked),{remember:true});assert.doesNotThrow(()=>h.saveLoginHint(blocked,{},true));
});

test('lookup omits credentials, does not cache, and rejects a mismatched returned company',async()=>{
  const h=await helpers();let observed;
  const response=await h.resolveCompany(' COMPANY-A ',{fetcher:async(url,options)=>{observed={url,options};return {ok:true,json:async()=>({company_code:'company-a',company_name:'公司甲'})};}});
  assert.deepEqual(response,{company_code:'company-a',company_name:'公司甲'});
  assert.equal(observed.url,'/api/v1/auth/company-entry?company_code=company-a');
  assert.equal(observed.options.credentials,'omit');assert.equal(observed.options.cache,'no-store');
  await assert.rejects(h.resolveCompany('company-a',{fetcher:async()=>({ok:true,json:async()=>({company_code:'company-b',company_name:'公司乙'})})}),/返回异常/);
  await assert.rejects(h.resolveCompany('某公司的显示名称',{fetcher:async()=>({ok:false,status:404})}),/公司码不是公司名称，请使用管理员提供的公司码或专属链接/);
  await assert.rejects(h.resolveCompany('company-a',{fetcher:async()=>({ok:false,status:429})}),/公司查询过于频繁/);
});

test('invalid explicit company link cannot silently enter an already authenticated company',async()=>{
  const view=await consoleHarness({search:'?company=company-a&company=company-b'});
  const accepted=await view.ui.acceptCompanyEntry({actor:{company_code:'company-a',company_name:'公司甲'}});
  assert.equal(accepted,false);assert.equal(view.calls.shell,0);
});

test('cross-company link asks before logout; retaining the current account canonicalizes the URL',async()=>{
  const view=await consoleHarness({search:'?company=company-b#tasks'});
  assert.equal(await view.ui.acceptCompanyEntry({actor:{company_code:'company-a',company_name:'公司甲'}}),false);
  assert.equal(view.calls.logouts,0);assert.equal(view.calls.shell,0);
  await view.nodes.get('#continue-company').onclick();
  assert.equal(view.location.search,'?company=company-a');assert.equal(view.location.hash,'#tasks');
  assert.equal(view.calls.shell,1);assert.equal(view.calls.logouts,0);
});

test('explicit company B never prefills remembered company A account',async()=>{
  const h=await helpers(),hint=storage();h.saveLoginHint(hint,{company_code:'company-a',company_name:'甲',account_code:'ADMIN-A'},true);
  const view=await consoleHarness({search:'?company=company-b',hint,resolveCompany:async()=>({company_code:'company-b',company_name:'乙'})});
  view.ui.login();await tick();
  assert.equal(view.form.elements.workspace.value,'company-b');assert.equal(view.form.elements.account.value,'');
  assert.equal(view.form.elements.password.value,'');
});

test('late company lookup cannot restore an old company after user starts a new selection',async()=>{
  const first=deferred(),view=await consoleHarness({search:'?company=company-a',resolveCompany:()=>first.promise});
  view.ui.login();view.nodes.get('#change-login-company').onclick();
  view.form.elements.workspace.value='company-b';view.form.elements.workspace.oninput();
  first.resolve({company_code:'company-a',company_name:'旧公司'});await tick();
  assert.equal(view.form.elements.workspace.value,'company-b');
  assert.equal(view.nodes.get('#login-company-card').hidden,true);
});

test('failed company resolution blocks password submission without guessing another company',async()=>{
  let logins=0;
  const view=await consoleHarness({search:'?company=company-a',resolveCompany:async()=>{throw Error('公司不可用');},loginAccount:async()=>{logins++;}});
  view.ui.login();await tick();view.form.elements.account.value='ADMIN';view.form.elements.password.value='test-password';
  assert.match(view.html,/<b>公司码（非公司名称）<\/b>/);
  assert.match(view.html,/placeholder="请输入管理员提供的公司码"/);
  assert.match(view.html,/name="workspace" type="text" autocomplete="off"/);
  assert.match(view.html,/公司码不是公司名称，请使用管理员提供的公司码或专属链接/);
  assert.match(view.html,/原有唯一账号仍可留空登录/);
  await view.form.onsubmit({preventDefault(){},currentTarget:view.form});
  assert.equal(logins,0);assert.equal(view.form.elements.password.value,'test-password');
  assert.match(view.nodes.get('#login-error').innerHTML,/公司不可用/);
});

test('invalid explicit entry plus an empty company cannot submit a company-less password login',async()=>{
  const writes=[];
  const view=await consoleHarness({search:'?company=company-a&company=company-b',loginAccount:async body=>{writes.push(body);}});
  view.ui.login();
  view.form.elements.workspace.value='';view.form.elements.account.value='ADMIN';view.form.elements.password.value='test-password';
  await view.form.onsubmit({preventDefault(){},currentTarget:view.form});
  assert.equal(writes.length,0);assert.equal(view.calls.shell,0);
  assert.match(view.nodes.get('#login-error').innerHTML,/此公司链接无效/);
  assert.equal(view.form.elements.password.value,'test-password');
  assert.ok(view.form.querySelectorAll().every(control=>control.disabled===false));
});

test('failed logout while switching companies returns to login from both shell and identity gate',async()=>{
  for(const entry of ['shell','gate']) {
    const view=await consoleHarness({search:'?company=company-b',
      resolveCompany:async()=>({company_code:'company-b',company_name:'公司乙'}),
      logoutAccount:async()=>{throw Error('服务端退出超时');}});
    if(entry==='shell') {
      await view.ui.renderShell();assert.match(view.html,/class="shell"/);
      await view.nodes.get('#switch-login-company').onclick();
    } else {
      view.state.actor={company_code:'company-a',company_name:'公司甲'};
      await view.ui.acceptCompanyEntry({actor:view.state.actor});
      await view.nodes.get('#switch-company').onclick();
    }
    await tick();
    assert.equal(view.calls.logouts,1,entry);assert.equal(view.state.actor,null,entry);
    assert.match(view.html,/id="login-form"/,entry);assert.doesNotMatch(view.html,/class="shell"/,entry);
    assert.match(view.html,/服务端退出超时/,entry);
    assert.equal(view.form.elements.password.value,'',entry);
  }
});
