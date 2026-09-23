const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const actor=(user,extra={})=>({workspace_id:'workspace',user_id:user,role:'operations',...extra});
async function setup(){
 const calls=[],events=[],cookies={refresh:null,binding:null};global.document={dispatchEvent:event=>events.push(event.type)};
 global.fetch=(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,cookies:{...cookies},resolve,reject}));
 const source=fs.readFileSync(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/core.js'),'utf8');
 const api=await import('data:text/javascript;base64,'+Buffer.from(source+'\n//'+Math.random()).toString('base64'));
 const respond=(call,status,data,setCookies)=>{
  // Browsers apply HttpOnly Set-Cookie before delivering the fetch response to JS.
  if(setCookies)Object.assign(cookies,setCookies);
  call.resolve({status,ok:status>=200&&status<300,json:async()=>data});
 };
 async function login(user,token=user){const p=api.loginAccount({account_code:user,password:'fixture-only'});respond(calls.at(-1),200,{access_token:token,actor:actor(user)},{refresh:'refresh-'+token,binding:'binding-'+token});await p;}
 return {api,calls,respond,login,cookies,events};
}
const tick=()=>new Promise(setImmediate);
for(const next of ['B','A'])test(`console does not replay old writes after logging into ${next}`,async()=>{
 const {api,calls,respond,login}=await setup();await login('A');
 const pending=api.api('/customers',{method:'POST',body:{name:'old intent'}});
 const rejected=assert.rejects(pending,e=>e.code==='SESSION_CHANGED');
 const old=calls.at(-1);await login(next);respond(old,401,{});await rejected;
 assert.equal(calls.length,3);assert.equal(api.state.token,next);
});
test('late refresh cannot resurrect a logged out identity or clear a newer flight',async()=>{
 const {api,calls,respond,login}=await setup();await login('A');
 const pending=api.api('/customers');const rejected=assert.rejects(pending,e=>e.code==='SESSION_CHANGED');
 respond(calls.at(-1),401,{});await tick();const oldRefresh=calls.at(-1);
 api.clearSession();await login('B');respond(oldRefresh,200,{access_token:'A2',actor:actor('A')});await rejected;
 assert.equal(api.state.token,'B');
});
test('concurrent console 401s share one refresh and each intent retries once',async()=>{
 const {api,calls,respond,login}=await setup();await login('A');
 const first=api.api('/customers'),second=api.api('/claims');respond(calls[1],401,{});respond(calls[2],401,{});await tick();
 assert.equal(calls.length,4);respond(calls[3],200,{access_token:'A2',actor:actor('A')});await tick();
 assert.equal(calls.length,6);assert.equal(calls[4].options.headers.Authorization,'Bearer A2');
 respond(calls[4],200,{items:[]});respond(calls[5],200,{items:[]});await Promise.all([first,second]);
});
test('successful but delayed console body is discarded after logout',async()=>{
 const {api,calls,login}=await setup();await login('A');let body;
 const pending=api.api('/customers');const rejected=assert.rejects(pending,e=>e.code==='SESSION_CHANGED');
 calls.at(-1).resolve({status:200,ok:true,json:()=>new Promise(r=>body=r)});await tick();api.clearSession();body({items:['old']});await rejected;
});

for(const backendChecksBinding of [true,false])test(`late A refresh cookie cannot replay B mutation (${backendChecksBinding?'binding rejects':'unexpected actor rejected in JS'})`,async()=>{
 const {api,calls,respond,login,cookies,events}=await setup();await login('A');
 const old=api.api('/old');const oldRejected=assert.rejects(old,{code:'SESSION_CHANGED'});
 respond(calls.at(-1),401,{});await tick();const oldRefresh=calls.at(-1);
 assert.deepEqual(oldRefresh.cookies,{refresh:'refresh-A',binding:'binding-A'});
 // A slow refresh never blocks B's explicit password login.
 await login('B');
 respond(oldRefresh,200,{access_token:'A2',actor:actor('A')},{refresh:'refresh-A2'});
 await oldRejected;
 assert.equal(api.state.token,'B');
 assert.deepEqual(cookies,{refresh:'refresh-A2',binding:'binding-B'});
 const body={name:'Intent authored by B'};
 const write=api.api('/customers',{method:'POST',body,key:'request-by-B'});
 const rejected=assert.rejects(write,{code:backendChecksBinding?'SESSION_EXPIRED':'SESSION_IDENTITY_MISMATCH'});
 const originalWrite=calls.at(-1);
 respond(originalWrite,401,{});await tick();
 const refresh=calls.at(-1);
 assert.deepEqual(refresh.cookies,{refresh:'refresh-A2',binding:'binding-B'});
 if(backendChecksBinding){
  // The server's signed B binding does not match the A refresh family.
  respond(refresh,401,{detail:'SESSION_EXPIRED'});
 }else{
  // Defence in depth also stops a bad/mismatched 200 before any token acceptance.
  respond(refresh,200,{access_token:'A3',actor:actor('A')},{refresh:'refresh-A3'});
 }
 await rejected;
 const writes=calls.filter(call=>call.url.endsWith('/customers'));
 assert.equal(writes.length,1);
 assert.equal(writes[0].options.headers.Authorization,'Bearer B');
 assert.equal(writes[0].options.body,JSON.stringify(body));
 assert.equal(api.state.token,null);
 assert.equal(api.state.actor,null);
 assert.deepEqual(events,['session-expired']);
});

for(const [name,replacement] of [
 ['workspace',actor('A',{workspace_id:'other-workspace'})],
 ['user',actor('other-user')],
 ['role',actor('A',{role:'administrator'})],
 ['missing identity',{role:'operations'}],
])test(`refresh rejects changed ${name} before replaying a write`,async()=>{
 const {api,calls,respond,login}=await setup();await login('A');
 const pending=api.api('/accounts',{method:'POST',body:{display_name:'original intent'}});
 const rejected=assert.rejects(pending,error=>['SESSION_IDENTITY_MISMATCH','SESSION_IDENTITY_INVALID'].includes(error.code));
 respond(calls.at(-1),401,{});await tick();
 respond(calls.at(-1),200,{access_token:'unexpected',actor:replacement});await rejected;
 assert.equal(calls.filter(call=>call.url.endsWith('/accounts')).length,1);
 assert.equal(api.state.token,null);
});

test('same identity may receive changed permissions and scope before one authorized retry',async()=>{
 const {api,calls,respond,login}=await setup();await login('A');
 const pending=api.api('/customers',{method:'POST',body:{name:'same actor'},key:'keep-this-key'});
 respond(calls.at(-1),401,{});await tick();
 const updated=actor('A',{data_scope:'department',team_ids:['new-team'],capabilities:{'customer.edit':false},permission_version:'v2'});
 respond(calls.at(-1),200,{access_token:'A2',actor:updated},{refresh:'refresh-A2'});await tick();
 const retry=calls.at(-1);
 assert.equal(retry.options.headers.Authorization,'Bearer A2');
 assert.equal(retry.options.headers['Idempotency-Key'],'keep-this-key');
 assert.equal(retry.options.body,JSON.stringify({name:'same actor'}));
 assert.deepEqual(api.state.actor,updated);
 respond(retry,200,{saved:true});assert.deepEqual(await pending,{saved:true});
});

test('explicit bootstrap can restore a bound cookie, but an unidentified business intent cannot',async()=>{
 const {api,calls,respond}=await setup();
 const initial=api.refreshToken();
 respond(calls.at(-1),200,{access_token:'A',actor:actor('A')});await initial;
 assert.equal(api.state.actor.user_id,'A');
 api.clearSession();
 const pending=api.api('/customers',{method:'POST',body:{name:'no logged-in author'}});
 const rejected=assert.rejects(pending,{code:'SESSION_EXPIRED'});
 respond(calls.at(-1),401,{});await rejected;
 assert.equal(calls.length,2,'business 401 must not bootstrap from whichever cookie is present');
});

test('late 401 uses already rotated token without a second refresh',async()=>{
 const {api,calls,respond,login}=await setup();await login('A');
 const first=api.api('/one'),late=api.api('/late');const lateCall=calls.at(-1);
 respond(calls[1],401,{});await tick();
 respond(calls.at(-1),200,{access_token:'A2',actor:actor('A')});await tick();
 respond(calls.at(-1),200,{ok:1});await first;
 respond(lateCall,401,{});await tick();
 assert.equal(calls.filter(call=>call.url.endsWith('/auth/refresh')).length,1);
 assert.equal(calls.at(-1).options.headers.Authorization,'Bearer A2');
 respond(calls.at(-1),200,{ok:2});await late;
});

test('old refresh failure does not clear new account or its pending refresh flight',async()=>{
 const {api,calls,respond,login}=await setup();await login('A');
 const old=api.api('/old');const oldRejected=assert.rejects(old,{code:'SESSION_CHANGED'});
 respond(calls.at(-1),401,{});await tick();const oldRefresh=calls.at(-1);
 await login('B');
 const current=api.api('/current');respond(calls.at(-1),401,{});await tick();const newRefresh=calls.at(-1);
 respond(oldRefresh,500,{});await oldRejected;
 assert.equal(api.state.token,'B');
 const another=api.api('/another');respond(calls.at(-1),401,{});await tick();
 assert.equal(calls.filter(call=>call.url.endsWith('/auth/refresh')).length,2);
 respond(newRefresh,200,{access_token:'B2',actor:actor('B')});await tick();
 const retries=calls.filter(call=>call.options.headers?.Authorization==='Bearer B2');
 assert.equal(retries.length,2);
 retries.forEach(call=>respond(call,200,{}));await Promise.all([current,another]);
});

test('late refresh JSON is discarded after login changes even if its HTTP response was already delivered',async()=>{
 const {api,calls,respond,login}=await setup();await login('A');let deliverBody;
 const old=api.api('/old');const rejected=assert.rejects(old,{code:'SESSION_CHANGED'});
 respond(calls.at(-1),401,{});await tick();
 calls.at(-1).resolve({status:200,ok:true,json:()=>new Promise(resolve=>{deliverBody=resolve})});await tick();
 await login('B');deliverBody({access_token:'A2',actor:actor('A')});await rejected;
 assert.equal(api.state.token,'B');
});

test('logout 401 is not reported as confirmed persistent logout',async()=>{
 const {api,calls,respond,login,cookies}=await setup();await login('A');
 const logout=api.logoutAccount();const rejected=assert.rejects(logout,/服务端退出尚未确认/);
 respond(calls.at(-1),401,{detail:'expired access'});await rejected;
 assert.equal(api.state.token,null);
 assert.equal(cookies.refresh,'refresh-A');
});

test('successful logout clears browser cookies even when access has expired',async()=>{
 const {api,calls,respond,login,cookies}=await setup();await login('A');
 const logout=api.logoutAccount();
 respond(calls.at(-1),200,{logged_out:true},{refresh:null,binding:null});await logout;
 assert.equal(api.state.token,null);
 assert.deepEqual(cookies,{refresh:null,binding:null});
});

test('login waits for same-tab logout cookie deletion before writing its own cookies',async()=>{
 const {api,calls,respond,login,cookies}=await setup();await login('A');
 const logout=api.logoutAccount();const rejected=assert.rejects(logout,{code:'SESSION_CHANGED'});
 const oldLogout=calls.at(-1);
 const next=api.loginAccount({account_code:'B',password:'fixture-only'});
 assert.equal(calls.at(-1),oldLogout,'new login must not race an in-flight logout Set-Cookie');
 respond(oldLogout,200,{logged_out:true},{refresh:null,binding:null});await tick();await rejected;
 assert.ok(calls.at(-1).url.endsWith('/auth/login'));
 respond(calls.at(-1),200,{access_token:'B',actor:actor('B')},{refresh:'refresh-B',binding:'binding-B'});
 await next;
 assert.equal(api.state.token,'B');
 assert.deepEqual(cookies,{refresh:'refresh-B',binding:'binding-B'});
});

test('failed logout settles before new login and cannot clear that new identity',async()=>{
 const {api,calls,respond,login}=await setup();await login('A');
 const logout=api.logoutAccount();const rejected=assert.rejects(logout,{code:'SESSION_CHANGED'});
 const oldLogout=calls.at(-1),next=api.loginAccount({account_code:'B',password:'fixture-only'});
 respond(oldLogout,500,{});await tick();await rejected;
 respond(calls.at(-1),200,{access_token:'B',actor:actor('B')});await next;
 assert.equal(api.state.token,'B');
});

test('unresponsive logout has a deadline and does not indefinitely block a new password login',async context=>{
 context.mock.timers.enable({apis:['setTimeout']});
 const {api,calls,respond,login}=await setup();await login('A');
 const logout=api.logoutAccount();const rejected=assert.rejects(logout,{code:'SESSION_CHANGED'});
 const oldLogout=calls.at(-1),next=api.loginAccount({account_code:'B',password:'fixture-only'});
 assert.equal(calls.at(-1),oldLogout);
 context.mock.timers.tick(10000);await tick();await rejected;
 assert.equal(oldLogout.options.signal.aborted,true);
 assert.ok(calls.at(-1).url.endsWith('/auth/login'));
 respond(calls.at(-1),200,{access_token:'B',actor:actor('B')});await next;
 assert.equal(api.state.token,'B');
});

test('company switch drops delayed mutation and never retries in another company',async()=>{
 const {api,calls,respond,login}=await setup();await login('admin');api.setCompany({id:'demo'});
 const pending=api.api('/customers',{method:'POST',body:{name:'demo intent'},key:'demo-key'});
 const rejected=assert.rejects(pending,{code:'COMPANY_CHANGED'});const old=calls.at(-1);
 assert.equal(old.options.headers['X-Company-ID'],'demo');api.setCompany({id:'formal'});respond(old,401,{});await rejected;
 assert.equal(calls.length,2);assert.equal(api.state.token,'admin');
 const next=api.api('/organization');assert.equal(calls.at(-1).options.headers['X-Company-ID'],'formal');respond(calls.at(-1),200,{});await next;
});
test('company switch during response body decoding prevents stale company rendering',async()=>{
 const {api,calls,login}=await setup();await login('admin');api.setCompany({id:'demo'});let body;
 const pending=api.api('/organization');const rejected=assert.rejects(pending,{code:'COMPANY_CHANGED'});
 calls.at(-1).resolve({status:200,ok:true,json:()=>new Promise(r=>body=r)});await tick();
 api.state.org={accounts:['demo']};api.state.filters={team:'demo-team'};api.setCompany({id:'formal'});
 assert.equal(api.state.org,null);assert.deepEqual(api.state.filters,{});body({accounts:['demo']});await rejected;
});
test('company switch during refresh does not replay or log out the authenticated admin',async()=>{
 const {api,calls,respond,login}=await setup();await login('admin');api.setCompany({id:'demo'});
 const pending=api.api('/customers',{method:'POST',body:{name:'demo intent'}});const rejected=assert.rejects(pending,{code:'COMPANY_CHANGED'});
 respond(calls.at(-1),401,{});await tick();const refresh=calls.at(-1);api.setCompany({id:'formal'});
 respond(refresh,200,{access_token:'admin2',actor:actor('admin')});await rejected;
 assert.equal(calls.filter(c=>c.url.endsWith('/customers')).length,1);assert.equal(api.state.token,'admin2');assert.equal(api.state.company.id,'formal');
});
test('auth and company-directory requests do not borrow the selected company identity',async()=>{
 const {api,calls,respond,login}=await setup();await login('admin');api.setCompany({id:'formal'});
 for(const path of ['/auth/me','/companies','/companies/select']){
  const pending=api.api(path);assert.equal(calls.at(-1).options.headers['X-Company-ID'],undefined);respond(calls.at(-1),200,{});await pending;
 }
});
