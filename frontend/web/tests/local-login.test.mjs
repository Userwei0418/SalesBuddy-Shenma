import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createSalesWebServer} from '../server.mjs';
import {createLocalLogin} from '../local-login.mjs';

const listen = server => new Promise(resolve => server.listen(0,'127.0.0.1',()=>resolve('http://127.0.0.1:'+server.address().port)));
const close = server => new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
const account='LOCAL_FIXTURE_SALES',password='SyntheticOnly_2026!';
const session=()=>({access_token:'local-fixture-access',refresh_token:'local-fixture-refresh',expires_at:new Date(Date.now()+3600000).toISOString(),token_type:'bearer',auth_method:'password',must_change_password:false,actor:{account_code:account,role:'sales',display_name:'Isolated fixture',role_name:'一线销售',data_scope:'self',scope_name:'仅本人',team_ids:[],team_names:[],permission_version:'fixture',user_id:'fixture-user',workspace_id:'fixture-workspace',capabilities:{'customer.read':true,'task.create':false}},debug_password:password});
async function fixture(run,{duplicate=false,timeout=1000}={}){
  const dir=await mkdtemp(join(tmpdir(),'sales-local-login-')),guidePath=join(dir,'guide.md');
  const row=`| ${account} | Isolated fixture | 一线销售 | Fixture | \`${password}\` |`;
  await writeFile(guidePath,'| 企业账号 | 姓名 | 已分配角色 | 部门 | 密码 |\n|---|---|---|---|---|\n'+row+'\n'+(duplicate?row+'\n':''));
  const received=[];let behavior='success';
  const upstream=http.createServer(async(req,res)=>{
    if(req.url.endsWith('/health/ready')){res.end('{"status":"ok"}');return;}
    let raw='';for await(const chunk of req)raw+=chunk;
    received.push({method:req.method,url:req.url,body:JSON.parse(raw),authorization:req.headers.authorization});
    if(behavior==='silent')return;
    if(behavior==='redirect'){res.writeHead(302,{Location:'/different-service'});res.end();return;}
    if(behavior==='401'){res.writeHead(401);res.end(JSON.stringify({detail:password}));return;}
    if(behavior==='badjson'){res.end('not json '+password);return;}
    const auth=session();
    if(behavior==='mismatch')auth.actor.account_code='OTHER_ACCOUNT';
    if(behavior==='incomplete')delete auth.actor.data_scope;
    if(behavior==='badcap')auth.actor.capabilities['customer.read']='false';
    if(behavior==='expired')auth.expires_at='2000-01-01T00:00:00Z';
    if(behavior==='mustchange')auth.must_change_password=true;
    res.setHeader('Content-Type','application/json');res.end(JSON.stringify(auth));
  });
  const target=await listen(upstream),config={guidePath,accountCode:account,role:'sales',apiTarget:target+'/api/v1'};
  const server=createSalesWebServer({target:config.apiTarget,localLogin:config,localLoginTimeout:timeout}),base=await listen(server);
  const login=(options={})=>fetch(base+'/local-login',{method:'POST',headers:{Origin:base},...options});
  try{await run({server,base,target,config,received,login,setBehavior:value=>{behavior=value;}});}finally{await close(server);await close(upstream);await rm(dir,{recursive:true,force:true});}
}

test('local quick login is opt-in, binds its target and returns no credential metadata',async()=>{
  const server=createSalesWebServer({target:''}),base=await listen(server);
  try{assert.equal((await fetch(base+'/local-login',{method:'POST',headers:{Origin:base}})).status,404);}finally{await close(server);}
  await fixture(async({base,config})=>{
    const status=await(await fetch(base+'/connection-status')).json();
    assert.deepEqual(status.localQuickLogin,{available:true,role:'sales',label:'交付账号 · 一线销售'});
    assert.equal(JSON.stringify(status).includes(account),false);assert.equal(JSON.stringify(status).includes(password),false);assert.equal(JSON.stringify(status).includes(config.guidePath),false);
    const localPort=Number(new URL(base).port),quick=createLocalLogin({config,upstream:config.apiTarget});
    for(const host of ['127.1','2130706433','0x7f000001','localhost?ignored','localhost#ignored','localhost:1','127.0.0.1:'+localPort+'?ignored','127.0.0.1:'+localPort+'#ignored']){
      assert.deepEqual(quick.statusFor({headers:{host},socket:{localAddress:'127.0.0.1',remoteAddress:'127.0.0.1',localPort}}),{available:false});
    }
    const wrong=createSalesWebServer({target:config.apiTarget+'/different',localLogin:config}),wrongBase=await listen(wrong);
    try{assert.equal((await fetch(wrongBase+'/local-login',{method:'POST',headers:{Origin:wrongBase}})).status,404);}finally{await close(wrong);}
  });
});
test('only local same-origin empty POST may use the configured account; session fields retain permissions and password-change requirement',async()=>fixture(async({base,login,received,setBehavior})=>{
  for(const options of [{method:'GET'},{headers:{}},{headers:{Origin:'https://external.invalid'}},{headers:{Origin:base,'Sec-Fetch-Site':'cross-site'}},{body:'{}'},{headers:{Origin:'http://attacker.invalid',Host:'attacker.invalid'}}]){
    const response=await login(options);assert.ok([400,403,405].includes(response.status),String(response.status));
  }
  assert.equal(received.length,0);
  setBehavior('mustchange');const response=await login(),auth=await response.json();assert.equal(response.status,200);
  assert.deepEqual(received,[{method:'POST',url:'/api/v1/auth/password/login',body:{account_code:account,password,role:'sales'},authorization:undefined}]);
  assert.equal(auth.must_change_password,true);assert.equal(auth.actor.capabilities['task.create'],false);assert.equal(auth.auth_method,'password');
  assert.equal(JSON.stringify(auth).includes(password),false);assert.equal(auth.debug_password,undefined);
}));
test('authentication errors, redirects, invalid identities and malformed sessions fail without leaking or retrying',async()=>fixture(async({login,received,setBehavior})=>{
  for(const behavior of ['401','redirect','badjson','mismatch','incomplete','badcap','expired']){
    setBehavior(behavior);const before=received.length,response=await login(),text=await response.text();
    assert.equal(response.status,behavior==='401'?401:502);assert.equal(received.length,before+1);
    assert.equal(text.includes(password),false);assert.equal(text.includes('local-fixture-access'),false);
  }
}));
test('duplicate guide rows fail closed before sending any authentication request',async()=>fixture(async({login,received})=>{
  assert.equal((await login()).status,503);assert.equal(received.length,0);
},{duplicate:true}));
test('one local login may run at a time and timeout releases the lock',async()=>fixture(async({login,setBehavior,received})=>{
  setBehavior('silent');const first=login();
  for(let i=0;i<40&&received.length===0;i++)await new Promise(resolve=>setTimeout(resolve,5));
  assert.equal((await login()).status,409);assert.equal((await first).status,504);
  setBehavior('success');assert.equal((await login()).status,200);
},{timeout:100}));
test('a disconnected browser cancels its pending local login and releases the lock',async()=>fixture(async({login,setBehavior,received})=>{
  setBehavior('silent');const controller=new AbortController();const first=login({signal:controller.signal});
  for(let i=0;i<40&&received.length===0;i++)await new Promise(resolve=>setTimeout(resolve,5));
  controller.abort();await assert.rejects(first,error=>error.name==='AbortError');
  await new Promise(resolve=>setTimeout(resolve,30));
  setBehavior('success');assert.equal((await login()).status,200);
},{timeout:1000}));
