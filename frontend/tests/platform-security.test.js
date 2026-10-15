const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../../backend/src/sales_backend/web');
const tick=()=>new Promise(setImmediate);
function setup(){
 const timers=new Map();let nextTimer=1;const listeners={};
 const descendants=node=>[node,...node.children.flatMap(descendants)];
 const matches=(node,selector)=>selector.startsWith('.')?node.className.split(/\s+/).includes(selector.slice(1)):selector.startsWith('#')?node.id===selector.slice(1):node.tagName===selector.toUpperCase();
 const element=tag=>({tagName:tag.toUpperCase(),textContent:'',value:'',className:'',hidden:false,open:false,children:[],listeners:{},
  setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
  append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
  addEventListener(name,fn){const previous=this.listeners[name];this.listeners[name]=()=>{previous?.();fn();};},
  querySelectorAll(selector){return this.children.flatMap(descendants).filter(node=>matches(node,selector));},
  reset(){this.resetCount=(this.resetCount||0)+1;this.fields={};for(const input of descendants(this)){if(['INPUT','TEXTAREA','SELECT'].includes(input.tagName))input.value=input.defaultValue||'';}},
  classList:{toggle(){}},close(){const wasOpen=this.open;this.open=false;if(wasOpen){this.onclose?.();this.listeners.close?.();}},showModal(){this.open=true;}
 });
 // Keep actual tag types/descendants so FormData, dialog cleanup, and secret fields are tested.
 const nodes=new Map(),tree=element('document'),stack=[tree];
 for(const match of fs.readFileSync(path.join(root,'platform.html'),'utf8').matchAll(/<(\/?)([a-z][a-z0-9-]*)([^>]*)>/gi)){
  const [,closing,tag,attrs]=match;
  if(closing){while(stack.length>1){if(stack.pop().tagName===tag.toUpperCase())break;}continue;}
  const node=element(tag);for(const attr of attrs.matchAll(/([a-zA-Z-]+)(?:="([^"]*)")?/g)){const [,key,value]=attr;node[key==='class'?'className':key]=value===undefined?true:value;}
  node.defaultValue=node.value;stack.at(-1).append(node);if(node.id)nodes.set('#'+node.id,node);
  if(!['meta','link','input','img','hr','br'].includes(tag.toLowerCase()))stack.push(node);
 }
 const requests=[];
 const ctx={setTimeout:(fn,delay)=>{const id=nextTimer++;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),document:{body:descendants(tree).find(n=>n.tagName==='BODY'),hidden:false,addEventListener:(name,fn)=>{const previous=listeners[name];listeners[name]=()=>{previous?.();fn();};},createElement:element,querySelectorAll:selector=>descendants(tree).filter(node=>matches(node,selector)),querySelector:id=>{assert(nodes.has(id),'missing UI element '+id);return nodes.get(id);}},
  window:{location:{origin:'https://local.test'}},URL,navigator:{clipboard:{writeText:async()=>{}}},
  fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject})),console,
  FormData:class{constructor(form){if(form&&form.tagName!=='FORM')throw new TypeError('FormData requires a form');this.fields=form?.fields||Object.fromEntries(form?descendants(form).filter(n=>n.name&&!n.disabled&&(n.type!=='checkbox'||n.checked)).map(n=>[n.name,n.value]):[]);}get(name){return this.fields[name]??null;}has(name){return Object.hasOwn(this.fields,name);}[Symbol.iterator](){return Object.entries(this.fields)[Symbol.iterator]();}},
  crypto:{randomUUID:()=> 'fixture-id',getRandomValues:bytes=>bytes.fill(42)}};
 vm.createContext(ctx);
 vm.runInContext(fs.readFileSync(path.join(root,'assets/platform-configuration.js'),'utf8').replace('export function','function')+'\n'+fs.readFileSync(path.join(root,'assets/platform-support.js'),'utf8').replace('export function','function')+'\n'+fs.readFileSync(path.join(root,'assets/platform-runtime.js'),'utf8').replace('export function','function')+'\n'+fs.readFileSync(path.join(root,'assets/platform.js'),'utf8').replace(/^import[^\n]+\n/gm,'')+'\nglobalThis.subject={api,loggedIn,loggedOut,showModules,showJobs,showQuota,showStorage,showHTTPLimits,showCalls,configurationUI};',ctx);
 const respond=(req,status,data)=>req.resolve({status,ok:status>=200&&status<300,json:async()=>data});
 return {nodes,requests,subject:ctx.subject,respond,timers,listeners,document:ctx.document};
}

test('password-only platform session shows enrollment without fetching tenant data',async()=>{
 const x=setup();x.respond(x.requests[0],200,{operator:{display_name:'合成管理员',mfa_enrolled:false,mfa_verified:false}});await tick();
 assert.equal(x.nodes.get('#workspace').hidden,true);
 assert.equal(x.nodes.get('#mfa-panel').hidden,false);
 assert.equal(x.requests.length,1);
});

test('logout clears the displayed enrollment seed and closes privileged dialogs',async()=>{
 const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
 x.nodes.get('#mfa-secret').textContent='synthetic-seed';x.nodes.get('#reauth-dialog').open=true;
 x.subject.loggedOut();
 assert.equal(x.nodes.get('#mfa-secret').textContent,'');assert.equal(x.nodes.get('#reauth-dialog').open,false);
 assert.equal(x.nodes.get('#mfa-panel').hidden,true);
});

test('expired platform verification keeps the intent and never retries a mutation automatically',async()=>{
 const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
 x.subject.loggedIn({display_name:'合成管理员',mfa_enrolled:true,mfa_verified:true});
 const saving=x.subject.api('/tenants',{name:'reviewed intent'});const rejected=assert.rejects(saving,/敏感操作验证/);
 x.respond(x.requests.at(-1),403,{detail:'PLATFORM_REAUTH_REQUIRED'});await rejected;
 assert.equal(x.nodes.get('#reauth-dialog').open,true);
 assert.equal(x.requests.filter(r=>r.url.endsWith('/tenants')).length,1);
 assert.equal(x.nodes.get('#create-form').resetCount,undefined);
});

test('late platform response cannot restore data after logout',async()=>{
 const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
 x.subject.loggedIn({display_name:'合成管理员',mfa_enrolled:true,mfa_verified:true});
 const request=x.subject.api('/tenants');const rejected=assert.rejects(request,/平台身份已变化/);
 x.subject.loggedOut();x.respond(x.requests.at(-1),200,{items:['old-tenant']});await rejected;
 assert.equal(x.nodes.get('#workspace').hidden,true);
});

test('enrollment action accepts a section container instead of treating it as a form',async()=>{
 const x=setup();x.respond(x.requests[0],200,{operator:{display_name:'合成管理员',mfa_enrolled:false,mfa_verified:false}});await tick();
 const action=x.nodes.get('#prepare-mfa').onclick();
 const request=x.requests.at(-1);assert(request.url.endsWith('/auth/mfa/enroll'));
 x.respond(request,200,{secret:'synthetic-seed'});await action;
 assert.equal(x.nodes.get('#mfa-secret').textContent,'synthetic-seed');
 assert.equal(x.nodes.get('#mfa-enrollment').hidden,false);
});

for(const [kind,open,label] of [['modules','showModules','功能模块'],['jobs','showJobs','任务运行']]){
 const payload=id=>({workspace_id:id,version_no:1,modules:[],concurrency_limit:2,running:0,waiting:0,dead_letter:0});
 test(kind+' dialog keeps the latest company when earlier response arrives last',async()=>{
  const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
  x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
  const first=x.subject[open]({id:'a',name:'公司甲'}),a=x.requests.at(-1);
  const second=x.subject[open]({id:'b',name:'公司乙'}),b=x.requests.at(-1);
  x.respond(b,200,payload('b'));await second;
  x.respond(a,200,payload('a'));await first;
  assert.equal(x.nodes.get('#'+kind+'-title').textContent,'公司乙 · '+label);
  assert.equal(x.nodes.get('#'+kind+'-form').resetCount,1);
  assert.equal(x.nodes.get('#'+kind+'-dialog').open,true);
 });
 for(const cancel of ['button','escape','logout'])test(kind+' dialog ignores pending response after '+cancel,async()=>{
  const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
  x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
  const second=x.subject[open]({id:'b',name:'公司乙'}),b=x.requests.at(-1);
  x.respond(b,200,payload('b'));await second;
  const first=x.subject[open]({id:'a',name:'公司甲'}),a=x.requests.at(-1);
  const dialog=x.nodes.get('#'+kind+'-dialog');
  if(cancel==='button')x.nodes.get('#cancel-'+kind).onclick();
  else if(cancel==='escape'){dialog.oncancel?.();dialog.close();}
  else x.subject.loggedOut();
  x.respond(a,200,payload('a'));
  if(cancel==='logout')await assert.rejects(first,/平台身份已变化/);else await first;
  assert.equal(dialog.open,false);
  assert.equal(x.nodes.get('#'+kind+'-form').resetCount,1);
 });
}

test('quota retains zero versus unlimited, and a late company response cannot replace the current selection',async()=>{
 const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
 x.subject.loggedIn({display_name:'合成管理员',mfa_enrolled:true,mfa_verified:true});
 const first=x.subject.showQuota({id:'a',name:'公司甲'}),a=x.requests.at(-1);
 const second=x.subject.showQuota({id:'b',name:'公司乙'}),b=x.requests.at(-1);
 x.respond(b,200,{workspace_id:'b',version_no:2,account_limit:0,remaining:0,active_accounts:3,over_limit:true});await second;
 x.respond(a,200,{workspace_id:'a',version_no:1,account_limit:null,remaining:null,active_accounts:1,over_limit:false});await first;
 assert.equal(x.nodes.get('#quota-title').textContent,'公司乙 · 账号额度');
 assert.equal(String(x.nodes.get('#quota-limit').value),'0');
 assert.match(x.nodes.get('#quota-counts').textContent,/超过新上限/);
 const unlimited=x.subject.showQuota({id:'b',name:'公司乙'});
 x.respond(x.requests.at(-1),200,{workspace_id:'b',version_no:3,account_limit:null,remaining:null,active_accounts:3,over_limit:false});await unlimited;
 assert.equal(x.nodes.get('#quota-limit').value,'');
 assert.match(x.nodes.get('#quota-counts').textContent,/未设上限/);
});

test('cancelling quota selection discards an in-flight response',async()=>{
 const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
 x.subject.loggedIn({display_name:'合成管理员',mfa_enrolled:true,mfa_verified:true});
 const pending=x.subject.showQuota({id:'a',name:'公司甲'});
 x.nodes.get('#cancel-quota').onclick();
 x.respond(x.requests.at(-1),200,{workspace_id:'a',version_no:1,account_limit:10,remaining:9,active_accounts:1});await pending;
 assert.equal(x.nodes.get('#quota-dialog').open,false);
});

test('clearing a quota sends explicit null with the reviewed version and reason',async()=>{
 const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
 x.subject.loggedIn({display_name:'合成管理员',mfa_enrolled:true,mfa_verified:true});
 const opening=x.subject.showQuota({id:'a',name:'公司甲'});
 x.respond(x.requests.at(-1),200,{workspace_id:'a',version_no:42,account_limit:10,remaining:9,active_accounts:1});await opening;
 const form=x.nodes.get('#quota-form');form.fields={account_limit:'',reason:'取消账号上限'};
 form.onsubmit({preventDefault(){},target:form});
 const request=x.requests.at(-1);
 assert.deepEqual(JSON.parse(request.options.body),{version_no:42,account_limit:null,reason:'取消账号上限'});
 x.respond(request,200,{});await tick();
 x.respond(x.requests.at(-1),200,{items:[]});await tick();
 x.respond(x.requests.at(-1),200,{items:[]});await tick();
 assert.equal(x.nodes.get('#quota-dialog').open,false);
 assert.match(x.nodes.get('#notice').textContent,/账号额度已保存/);
});


test('storage quota ignores stale tenants and a cancelled request',async()=>{
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const first=x.subject.showStorage({id:'a',name:'甲'}),a=x.requests.at(-1);
 const second=x.subject.showStorage({id:'b',name:'乙'}),b=x.requests.at(-1);
 x.respond(b,200,{workspace_id:'b',version_no:3,limit_bytes:0,stored_bytes:6,reserved_bytes:2,remaining_bytes:0,pending_count:1,oldest_pending_at:'2026-09-26T00:00:00Z'});await second;
 x.respond(a,200,{workspace_id:'a',version_no:1,limit_bytes:null,stored_bytes:0,reserved_bytes:0,remaining_bytes:null,pending_count:0});await first;
 assert.equal(x.nodes.get('#storage-title').textContent,'乙 · 原件存储额度');assert.equal(x.nodes.get('#storage-limit').value,0);
 assert.match(x.nodes.get('#storage-pending').textContent,/不能按时间自动释放/);
 const pending=x.subject.showStorage({id:'a',name:'甲'});x.nodes.get('#cancel-storage').onclick();
 x.respond(x.requests.at(-1),200,{});await pending;assert.equal(x.nodes.get('#storage-dialog').open,false);
});

for(const [input,expected] of [['',null],['0',0],['1.5',1572864]])test('storage quota saves exact bytes for '+JSON.stringify(input),async()=>{
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const opening=x.subject.showStorage({id:'a',name:'甲'});x.respond(x.requests.at(-1),200,{workspace_id:'a',version_no:8,limit_bytes:null,stored_bytes:0,reserved_bytes:0,remaining_bytes:null,pending_count:0});await opening;
 const form=x.nodes.get('#storage-form');form.fields={limit_mib:input,reason:'额度验收'};form.onsubmit({preventDefault(){},target:form});
 const request=x.requests.at(-1);assert.match(request.url,/tenants\/a\/storage-quota$/);
 assert.deepEqual(JSON.parse(request.options.body),{version_no:8,limit_bytes:expected,reason:'额度验收'});
 x.respond(request,200,{});await tick();x.respond(x.requests.at(-1),200,{items:[]});await tick();x.respond(x.requests.at(-1),200,{items:[]});await tick();
 assert.equal(x.nodes.get('#storage-dialog').open,false);
});

function httpLimits(id,version,rate=12,burst=3){return {workspace_id:id,version_no:version,items:[
 {bucket:'read',label:'后台返回的读取名称',rate_per_minute:rate,burst,accepted:4,rejected:2}
]};}

test('HTTP limits discard stale and cancelled company responses',async()=>{
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const a=x.subject.showHTTPLimits({id:'a',name:'甲'}),ar=x.requests.at(-1);
 const b=x.subject.showHTTPLimits({id:'b',name:'乙'}),br=x.requests.at(-1);
 x.respond(br,200,httpLimits('b',5));await b;x.respond(ar,200,httpLimits('a',1,null,null));await a;
 assert.equal(x.nodes.get('#http-title').textContent,'乙 · 请求频率');assert.equal(x.nodes.get('#http-rate').value,12);
 const c=x.subject.showHTTPLimits({id:'c',name:'丙'});x.nodes.get('#cancel-http').onclick();
 x.respond(x.requests.at(-1),200,httpLimits('c',2));await c;assert.equal(x.nodes.get('#http-dialog').open,false);
});

test('unlimited HTTP settings send two explicit nulls and reviewed version',async()=>{
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const opening=x.subject.showHTTPLimits({id:'a',name:'甲'});x.respond(x.requests.at(-1),200,httpLimits('a',9));await opening;
 const form=x.nodes.get('#http-form');form.fields={bucket:'read',rate_per_minute:'',burst:'',reason:'取消频率限制'};
 form.onsubmit({preventDefault(){},target:form});const req=x.requests.at(-1);
 assert.deepEqual(JSON.parse(req.options.body),{version_no:9,bucket:'read',rate_per_minute:null,burst:null,reason:'取消频率限制'});
 x.respond(req,200,{});await tick();x.respond(x.requests.at(-1),200,{items:[]});await tick();x.respond(x.requests.at(-1),200,{items:[]});await tick();
 assert.equal(x.nodes.get('#http-dialog').open,false);
});

test('incomplete HTTP limits never submit and logout closes the dialog',async()=>{
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const opening=x.subject.showHTTPLimits({id:'a',name:'甲'});x.respond(x.requests.at(-1),200,httpLimits('a',9));await opening;
 const form=x.nodes.get('#http-form');form.fields={bucket:'read',rate_per_minute:'12',burst:'',reason:'无效'};
 const count=x.requests.length;form.onsubmit({preventDefault(){},target:form});await tick();
 assert.equal(x.requests.length,count);assert.match(x.nodes.get('#notice').textContent,/速率和突发容量/);
 x.subject.loggedOut();assert.equal(x.nodes.get('#http-dialog').open,false);
});

const callFixture=id=>({workspace_id:id,version_no:12,periods:[{code:'lifetime',label:'累计'}],items:[{resource:'ai_calls',label:'AI 业务调用',period:'lifetime',call_limit:0,used:2,reserved:1,remaining:0,pending_attempts:1}]});
test('call quota selection rejects stale and cancelled responses and preserves zero',async()=>{
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const a=x.subject.showCalls({id:'a',name:'甲'}),reqA=x.requests.at(-1);
 const b=x.subject.showCalls({id:'b',name:'乙'}),reqB=x.requests.at(-1);
 x.respond(reqB,200,callFixture('b'));await b;x.respond(reqA,200,callFixture('a'));await a;
 assert.equal(x.nodes.get('#calls-title').textContent,'乙 · AI 与语音额度');assert.equal(x.nodes.get('#calls-limit').value,0);
 assert.match(x.nodes.get('#calls-counts').textContent,/未结算请求 1/);
 const pending=x.subject.showCalls({id:'a',name:'甲'});x.nodes.get('#cancel-calls').onclick();x.respond(x.requests.at(-1),200,callFixture('a'));await pending;
 assert.equal(x.nodes.get('#calls-dialog').open,false);
});
test('saving call quota carries the resource, period, reviewed version and explicit unlimited',async()=>{
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const opening=x.subject.showCalls({id:'a',name:'甲'});x.respond(x.requests.at(-1),200,callFixture('a'));await opening;
 const form=x.nodes.get('#calls-form');form.fields={resource:'ai_calls',period:'month_utc',call_limit:'',reason:'调整配额'};form.onsubmit({preventDefault(){},target:form});
 const request=x.requests.at(-1);assert.deepEqual(JSON.parse(request.options.body),{version_no:12,resource:'ai_calls',call_limit:null,period:'month_utc',reason:'调整配额'});
 x.respond(request,200,{});await tick();x.respond(x.requests.at(-1),200,{items:[]});await tick();x.respond(x.requests.at(-1),200,{items:[]});await tick();
 assert.equal(x.nodes.get('#calls-dialog').open,false);
});

const descend=node=>[node,...(node.children||[]).flatMap(descend)];
const configurationFixture=(id='a')=>({workspace_id:id,version_no:4,ai_service:{workspace_id:id,version_no:4,shared_enabled:true},sampled_at:'2026-09-26T00:00:00Z',
 configuration_readiness:{kind:'static_configuration',required_ready:true,checks:[{code:'text',enabled:false,required:false,configuration_ready:false,issues:['shared_service_not_configured']},{code:'asr',enabled:false,required:false,configuration_ready:false,issues:['credential_missing']},{code:'tts',enabled:false,required:false,configuration_ready:false,issues:['purpose_disabled']},{code:'feishu',enabled:false,required:false,configuration_ready:true,issues:[]}]},
 identity_checks:{administrator_ready:true,activation_complete:true,permissions_ready:true},
 modules:[{code:'ai',enabled:false,effective:false},{code:'feishu',enabled:true,effective:false},{code:'visit',enabled:true,effective:true}],
 model_connections:[{purpose:'text',source:'instance_default',version_no:null},{purpose:'asr',source:'tenant_purpose',version_no:2,fields_present:true,credential_saved:false},{purpose:'tts',source:'disabled',version_no:1}],
 feishu:{configured:true,revision:3,enabled:false,validated:true,validation_pending:false,credential_saved:true,fields_present:true,direction_supported:true,enabled_mappings:2}});
async function configurationView(){
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const pending=x.subject.configurationUI.open({id:'a',name:'合成甲'});return {...x,pending,request:x.requests.at(-1)};
}
test('configuration source is tenant-specific and missing data is not shown as connected',async()=>{
 const x=await configurationView();x.respond(x.request,200,configurationFixture());await x.pending;
 assert.match(x.request.url,/\/tenants\/a\/configuration$/);
 const texts=descend(x.nodes.get('#configuration-body')).map(n=>n.textContent).join('\n');
 assert.match(texts,/继承实例默认配置/);assert.match(texts,/未保存本公司凭据/);
 assert.match(texts,/当前版本留有通过验证的记录；本次未重新连接飞书/);
 assert.match(texts,/飞书同步：已到期/);assert.match(texts,/语音合成（预留）/);
 assert(x.requests.every(r=>r.options.method==='GET'));
});
test('configuration selection discards a late company response and clears errors before retry',async()=>{
 const x=await configurationView();
 const other=x.subject.configurationUI.open({id:'b',name:'<img src=x onerror=steal()>'});
 x.respond(x.requests.at(-1),200,configurationFixture('b'));await other;
 x.respond(x.request,200,configurationFixture());await x.pending;
 assert.equal(x.nodes.get('#configuration-title').textContent,'<img src=x onerror=steal()> · 配置检查');
 const retry=x.nodes.get('#refresh-configuration').onclick();
 assert(!descend(x.nodes.get('#configuration-body')).some(n=>n.textContent==='管理员与权限'));
 x.respond(x.requests.at(-1),503,{});await retry;
 assert.equal(x.nodes.get('#configuration-sampled').textContent,'');
 assert(!descend(x.nodes.get('#configuration-body')).some(n=>n.innerHTML!==undefined));
});
test('configuration rejects a mismatched workspace response',async()=>{
 const x=await configurationView();x.respond(x.request,200,configurationFixture('b'));await x.pending;
 assert.match(descend(x.nodes.get('#configuration-body')).map(n=>n.textContent).join(''),/公司配置响应不匹配/);
});
test('configuration shows missing required integration without claiming a live connection',async()=>{
 const x=await configurationView(),report=configurationFixture();
 report.configuration_readiness.required_ready=false;
 report.configuration_readiness.checks[0]={code:'text',enabled:true,required:true,configuration_ready:false,issues:['source_not_authorized']};
 x.respond(x.request,200,report);await x.pending;
 const text=descend(x.nodes.get('#configuration-body')).map(n=>n.textContent).join('\n');
 assert.match(text,/必要配置缺失/);assert.match(text,/缺少平台授权或本公司自有配置/);
 assert.match(text,/不表示已解密或连接成功/);assert.match(text,/不阻塞文字业务/);
 assert(x.requests.every(r=>r.options.method==='GET'));
});
test('configuration with a mismatched backend contract never displays ready',async()=>{
 const x=await configurationView(),report=configurationFixture();delete report.configuration_readiness;
 x.respond(x.request,200,report);await x.pending;
 const text=descend(x.nodes.get('#configuration-body')).map(n=>n.textContent).join('\n');
 assert.match(text,/缺少配套的配置检查结果/);assert.doesNotMatch(text,/必要配置齐备/);
});
for(const action of ['close','logout','hidden','cancel'])test('configuration late response is discarded after '+action,async()=>{
 const x=await configurationView();
 if(action==='close')x.nodes.get('#close-configuration').onclick();
 if(action==='logout')x.subject.loggedOut();
 if(action==='hidden'){x.document.hidden=true;x.listeners.visibilitychange();}
 if(action==='cancel')x.nodes.get('#configuration-dialog').listeners.cancel();
 x.respond(x.request,200,configurationFixture());await x.pending;
 assert.deepEqual(x.nodes.get('#configuration-body').children,[]);
 assert.equal(x.nodes.get('#configuration-sampled').textContent,'');
});
const runtimeFixture=(name='合成公司',next=null)=>({sampled_at:'2026-09-26T00:00:00Z',wait_minutes:15,next_cursor:next,items:[{
 workspace_id:'a',name,company_code:'synthetic',status:'active',
 jobs:{running:1,waiting:2,overdue:0,expired_leases:0,dead_letter:0},
 sync:{enabled:false,waiting:0,overdue:0,expired_leases:0,dead_letter:0,unknown_deliveries:0},
 originals:{available:true,limit_bytes:null,stored_bytes:1024,reserved_bytes:0,pending:0,overdue:0,over_limit:false},
 calls:{pending:0,overdue:0,unknown:0,failed_last_24h:0}
}]});
async function runtimeView(){
 const x=setup();x.respond(x.requests[0],401,{});await tick();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:true});
 const pending=x.nodes.get('#open-runtime').onclick();return {...x,pending,request:x.requests.at(-1)};
}
test('runtime snapshot only requests metadata; values render as text and incomplete counts stay unknown',async()=>{
 const x=await runtimeView(),data=runtimeFixture('<img src=x onerror=steal()>');
 data.items[0].originals={available:false,limit_bytes:null,stored_bytes:null,reserved_bytes:null,pending:null,overdue:null,over_limit:false};
 x.respond(x.request,200,data);await x.pending;
 const nodes=descend(x.nodes.get('#runtime-body'));
 assert(nodes.some(n=>n.textContent==='<img src=x onerror=steal()>'));
 assert(!nodes.some(n=>n.innerHTML!==undefined));
 assert(nodes.some(n=>n.textContent==='原件统计未就绪，请核对公司初始化'));
 assert.equal(x.requests.filter(r=>r.url.includes('/runtime?')).length,1);
 assert(x.requests.every(r=>!r.options?.method||r.options.method==='GET'));
});
test('runtime paging uses the server cursor and threshold changes return to the first page',async()=>{
 const x=await runtimeView();x.respond(x.request,200,runtimeFixture('甲','page-two'));await x.pending;
 const next=x.nodes.get('#runtime-next').onclick();assert.match(x.requests.at(-1).url,/wait_minutes=15&after_id=page-two/);
 x.respond(x.requests.at(-1),200,runtimeFixture('乙'));await next;
 const refresh=x.nodes.get('#runtime-refresh').onclick();assert.match(x.requests.at(-1).url,/after_id=page-two/);
 x.respond(x.requests.at(-1),200,runtimeFixture('乙'));await refresh;
 x.nodes.get('#runtime-wait').value=60;const changed=x.nodes.get('#runtime-wait').onchange();
 assert.match(x.requests.at(-1).url,/runtime\?wait_minutes=60$/);x.respond(x.requests.at(-1),200,runtimeFixture('甲'));await changed;
 x.nodes.get('#runtime-wait').value=0;const count=x.requests.length;await x.nodes.get('#runtime-wait').onchange();assert.equal(x.requests.length,count);
});
test('runtime failure clears old counts and a superseded response cannot restore them',async()=>{
 const x=await runtimeView();x.respond(x.request,200,runtimeFixture('旧快照'));await x.pending;
 const a=x.nodes.get('#runtime-refresh').onclick(),ra=x.requests.at(-1);
 assert(!descend(x.nodes.get('#runtime-body')).some(n=>n.textContent==='旧快照'));
 const b=x.nodes.get('#runtime-refresh').onclick();x.respond(x.requests.at(-1),503,{detail:'计数暂不可用'});await b;
 x.respond(ra,200,runtimeFixture('迟到快照'));await a;
 assert(descend(x.nodes.get('#runtime-body')).some(n=>n.textContent==='操作未完成，请刷新后重试。'));
 assert(!descend(x.nodes.get('#runtime-body')).some(n=>n.textContent==='迟到快照'));
 assert.equal(x.nodes.get('#runtime-sampled').textContent,'');
});
for(const action of ['close','logout','hidden','cancel'])test('runtime pending response is discarded after '+action,async()=>{
 const x=await runtimeView();
 if(action==='close')x.nodes.get('#close-runtime').onclick();
 if(action==='logout')x.subject.loggedOut();
 if(action==='hidden'){x.document.hidden=true;x.listeners.visibilitychange();}
 if(action==='cancel')x.nodes.get('#runtime-dialog').listeners.cancel();
 x.respond(x.request,200,runtimeFixture('不可回填'));await x.pending;
 assert.deepEqual(x.nodes.get('#runtime-body').children,[]);
 assert.equal(x.nodes.get('#runtime-sampled').textContent,'');
});
const grant=()=>({id:'grant-a',company_name:'合成甲公司',reason:'approved',approver_name:'company-admin',
 active:true,expires_at:new Date(Date.now()+3600000).toISOString(),scopes:[{code:'customers',label:'客户基本资料',available:true}]});
async function supportView(x){
 x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();
 x.subject.loggedIn({display_name:'合成平台人员',mfa_enrolled:true,mfa_verified:true});
 const action=x.nodes.get('#open-support').onclick();x.respond(x.requests.at(-1),200,{items:[grant()]});await action;
 const button=descend(x.nodes.get('#support-body')).find(n=>n.textContent==='客户基本资料');
 const pending=button.onclick();return {pending,request:x.requests.at(-1)};
}
test('support dialog rejects a late business response after closing',async()=>{
 const x=setup(),{pending,request}=await supportView(x);
 x.nodes.get('#close-support').onclick();
 x.respond(request,200,{items:[{id:'a',name:'private business'}]});await pending;
 assert.equal(x.nodes.get('#support-dialog').open,false);assert.deepEqual(x.nodes.get('#support-body').children,[]);
 assert.equal(x.timers.size,0);
});
test('support lease expiry clears displayed data and pending revalidation',async()=>{
 const x=setup(),{pending,request}=await supportView(x);
 x.respond(request,200,{items:[{id:'a',name:'private business'}]});await pending;
 assert(descend(x.nodes.get('#support-body')).some(n=>n.textContent==='private business'));
 const expiry=[...x.timers.values()].find(t=>t.delay>15000);expiry.fn();
 assert(!descend(x.nodes.get('#support-body')).some(n=>n.textContent==='private business'));
 assert.equal(x.timers.size,0);
});
test('revocation refresh removes prior records and cannot refill after logout',async()=>{
 const x=setup(),{pending,request}=await supportView(x);
 x.respond(request,200,{items:[{id:'a',name:'private business'}]});await pending;
 const refresh=[...x.timers.values()].find(t=>t.delay===15000).fn();
 x.respond(x.requests.at(-1),200,{items:[{...grant(),active:false,revoked_at:new Date().toISOString()}]});await refresh;
 assert(!descend(x.nodes.get('#support-body')).some(n=>n.textContent==='private business'));
 x.subject.loggedOut();assert.equal(x.nodes.get('#support-dialog').open,false);
});
test('hiding a support tab clears its business page',async()=>{
 const x=setup(),{pending,request}=await supportView(x);
 x.respond(request,200,{items:[{id:'a',name:'<script>literal text</script>'}]});await pending;
 assert(descend(x.nodes.get('#support-body')).some(n=>n.textContent==='<script>literal text</script>'));
 x.document.hidden=true;x.listeners.visibilitychange();
 assert.equal(x.nodes.get('#support-dialog').open,false);assert.equal(x.timers.size,0);
});

test('shared AI changes require reason and exact company version, with no automatic retry',async()=>{
 const x=await configurationView();x.respond(x.request,200,configurationFixture());await x.pending;
 const form=descend(x.nodes.get('#configuration-body')).find(n=>n.tagName==='FORM');
 const reason=form.children.find(n=>n.tagName==='TEXTAREA'), check=form.children[0].children[0];
 const count=x.requests.length;await form.onsubmit({preventDefault(){}});assert.equal(x.requests.length,count);
 reason.value='合成停用';check.checked=false;
 const save=form.onsubmit({preventDefault(){}}), request=x.requests.at(-1);
 assert.match(request.url,/\/tenants\/a\/ai-service$/);
 assert.deepEqual(JSON.parse(request.options.body),{version_no:4,shared_enabled:false,reason:'合成停用'});
 x.respond(request,409,{detail:'VERSION_CONFLICT'});await save;
 assert.equal(x.requests.length,count+1);
 assert.match(form.children.at(-1).textContent,/刷新配置状态/);
});

test('shared AI save completion cannot reopen a closed company dialog',async()=>{
 const x=await configurationView();x.respond(x.request,200,configurationFixture());await x.pending;
 const form=descend(x.nodes.get('#configuration-body')).find(n=>n.tagName==='FORM');
 form.children.find(n=>n.tagName==='TEXTAREA').value='合成修改';
 const save=form.onsubmit({preventDefault(){}}),request=x.requests.at(-1);
 x.nodes.get('#close-configuration').onclick();const count=x.requests.length;
 x.respond(request,200,{workspace_id:'a',version_no:5,shared_enabled:false});await save;
 assert.equal(x.requests.length,count);assert.equal(x.nodes.get('#configuration-dialog').open,false);
 assert.deepEqual(x.nodes.get('#configuration-body').children,[]);
});

async function signedOut(){const x=setup();x.respond(x.requests[0],401,{detail:'PLATFORM_AUTH_REQUIRED'});await tick();return x;}
async function finishRefresh(x){assert.match(x.requests.at(-1).url,/\/tenants$/);x.respond(x.requests.at(-1),200,{items:[]});await tick();assert.match(x.requests.at(-1).url,/\/audit$/);x.respond(x.requests.at(-1),200,{items:[]});await tick();}
test('two-step platform login sends password first, clears it, then sends only OTP before fetching companies',async()=>{
 const x=await signedOut(),login=x.nodes.get('#login-form');login.fields={account_code:'PLATFORM_QA',password:'synthetic-password'};login.onsubmit({preventDefault(){},target:login});
 const start=x.requests.at(-1);assert.match(start.url,/\/auth\/start$/);assert.deepEqual(JSON.parse(start.options.body),{account_code:'PLATFORM_QA',password:'synthetic-password'});
 x.respond(start,200,{operator:{display_name:'平台验收',mfa_enrolled:true,mfa_verified:false,expires_at:new Date(Date.now()+120000).toISOString()}});await tick();
 assert.deepEqual(login.fields,{});assert.equal(x.nodes.get('#otp-panel').hidden,false);assert.equal(x.nodes.get('#workspace').hidden,true);assert.equal(x.requests.length,2);
 assert(x.document.querySelectorAll('.auth-expiry').every(n=>/分钟内/.test(n.textContent)));
 const otp=x.nodes.get('#otp-form');otp.fields={otp:'123456'};const verify=otp.onsubmit({preventDefault(){},target:otp});
 const request=x.requests.at(-1);assert.match(request.url,/\/auth\/mfa\/login$/);assert.deepEqual(JSON.parse(request.options.body),{otp:'123456'});
 x.respond(request,200,{operator:{display_name:'平台验收',mfa_enrolled:true,mfa_verified:true}});await tick();await finishRefresh(x);await verify;
 assert.deepEqual(otp.fields,{});assert.equal(x.nodes.get('#otp-panel').hidden,true);assert.equal(x.nodes.get('#workspace').hidden,false);
});
test('returning from OTP explicitly logs out and clears password, OTP, enrollment QR and seed',async()=>{
 const x=await signedOut();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:false});
 x.nodes.get('#login-form').fields={password:'previous-password'};x.nodes.get('#otp-form').fields={otp:'123456'};
 x.nodes.get('#mfa-form').fields={otp:'654321'};x.nodes.get('#reauth-form').fields={password:'old-reauth-password',otp:'111111'};
 x.nodes.get('#mfa-secret').textContent='old-seed';x.nodes.get('#mfa-qr').src='data:old-qr';x.nodes.get('#mfa-qr').hidden=false;
 const back=x.nodes.get('#otp-back').onclick(),request=x.requests.at(-1);assert.match(request.url,/\/auth\/logout$/);x.respond(request,200,{});await back;
 for(const id of ['login-form','otp-form','mfa-form','reauth-form'])assert.deepEqual(x.nodes.get('#'+id).fields,{});
 assert.equal(x.nodes.get('#mfa-secret').textContent,'');assert.equal(x.nodes.get('#mfa-qr').src,undefined);assert.equal(x.nodes.get('#mfa-qr').hidden,true);
 assert.equal(x.nodes.get('#login-panel').hidden,false);assert.equal(x.nodes.get('#otp-panel').hidden,true);assert.equal(x.nodes.get('#workspace').hidden,true);
});
test('successful enrollment consumes its code and removes the former setup QR and seed',async()=>{
 const x=await signedOut();x.subject.loggedIn({mfa_enrolled:false,mfa_verified:false});const prepare=x.nodes.get('#prepare-mfa').onclick();
 x.respond(x.requests.at(-1),200,{secret:'synthetic-seed',qr_svg:'<svg>synthetic</svg>'});await prepare;
 assert.match(x.nodes.get('#mfa-qr').src,/^data:image/);const form=x.nodes.get('#mfa-form');form.fields={otp:'123456'};form.onsubmit({preventDefault(){},target:form});
 assert.match(x.requests.at(-1).url,/\/auth\/mfa\/confirm$/);x.respond(x.requests.at(-1),200,{operator:{mfa_enrolled:true,mfa_verified:true}});await tick();await finishRefresh(x);
 assert.deepEqual(form.fields,{});assert.equal(x.nodes.get('#mfa-secret').textContent,'');assert.equal(x.nodes.get('#mfa-qr').src,undefined);assert.equal(x.nodes.get('#mfa-enrollment').hidden,true);
});
test('expired incomplete platform login returns to credentials and discards pending enrollment secrets',async()=>{
 const x=await signedOut();x.subject.loggedIn({mfa_enrolled:false,mfa_verified:false,expires_at:new Date(Date.now()+120000).toISOString()});
 x.nodes.get('#mfa-secret').textContent='synthetic-seed';x.nodes.get('#mfa-qr').src='data:seed';
 assert.equal(x.timers.size,1);Array.from(x.timers.values())[0].fn();
 assert.equal(x.nodes.get('#login-panel').hidden,false);assert.equal(x.nodes.get('#mfa-secret').textContent,'');assert.equal(x.nodes.get('#mfa-qr').src,undefined);assert.equal(x.timers.size,0);
 assert.equal(x.requests.length,1);
});

for(const [formId,endpoint] of [['login-form','/auth/start'],['otp-form','/auth/mfa/login'],['mfa-form','/auth/mfa/confirm']]) test('uncertain '+endpoint+' recovers a verified session with one read and never repeats the write',async()=>{
 const x=await signedOut();if(formId!=='login-form')x.subject.loggedIn({mfa_enrolled:formId==='otp-form',mfa_verified:false});
 const form=x.nodes.get('#'+formId);form.fields=formId==='login-form'?{account_code:'QA',password:'synthetic-password'}:{otp:'123456'};
 form.onsubmit({preventDefault(){},target:form});const write=x.requests.at(-1);assert.equal(write.options.method,'POST');assert(write.url.endsWith(endpoint));
 // A server may have committed and set its cookie before the response body was interrupted.
 write.resolve({status:200,ok:true,json:async()=>{throw Error('truncated response');}});await tick();
 const recover=x.requests.at(-1);assert.match(recover.url,/\/auth\/me$/);assert.equal(recover.options.method,'GET');assert.equal(recover.options.body,undefined);assert.equal(recover.options.cache,'no-store');
 assert.equal(x.requests.filter(r=>r.options.method==='POST').length,1);
 x.respond(recover,200,{operator:{mfa_enrolled:true,mfa_verified:true,display_name:'Recovered QA'}});await tick();await finishRefresh(x);
 assert.equal(x.nodes.get('#workspace').hidden,false);assert.deepEqual(form.fields,{});assert.equal(x.requests.filter(r=>r.url.endsWith('/auth/me')).length,2);
 assert.equal(x.requests.filter(r=>r.options.method==='POST').length,1);
});
for(const enrolled of [false,true]) test('uncertain password verification resumes only the current '+(enrolled?'OTP':'enrollment')+' challenge',async()=>{
 const x=await signedOut(),form=x.nodes.get('#login-form');form.fields={account_code:'QA',password:'synthetic-password'};form.onsubmit({preventDefault(){},target:form});
 x.requests.at(-1).reject(Error('connection lost'));await tick();
 const read=x.requests.at(-1);assert.match(read.url,/\/auth\/me$/);x.respond(read,200,{operator:{mfa_enrolled:enrolled,mfa_verified:false}});await tick();
 assert.equal(x.nodes.get('#workspace').hidden,true);assert.equal(x.nodes.get('#otp-panel').hidden,!enrolled);assert.equal(x.nodes.get('#mfa-panel').hidden,enrolled);
 assert.deepEqual(form.fields,{});assert.equal(x.requests.length,3);assert.equal(x.requests.filter(r=>r.options.method==='POST').length,1);
 assert.match(x.nodes.get('#notice').textContent,/当前验证步骤/);
});
test('failed uncertain-session read stops after one GET without blind OTP replay',async()=>{
 const x=await signedOut();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:false});const form=x.nodes.get('#otp-form');form.fields={otp:'123456'};
 const action=form.onsubmit({preventDefault(){},target:form});x.requests.at(-1).reject(Error('write response lost'));await tick();
 assert.match(x.requests.at(-1).url,/\/auth\/me$/);x.requests.at(-1).reject(Error('read unavailable'));await action;
 assert.equal(x.requests.length,3);assert.equal(x.requests.filter(r=>r.options.method==='POST').length,1);assert.equal(x.nodes.get('#workspace').hidden,true);
 assert.match(x.nodes.get('#notice').textContent,/未收到完整结果/);
});
test('a certain authentication rejection does not trigger recovery or replay',async()=>{
 const x=await signedOut();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:false});const form=x.nodes.get('#otp-form');form.fields={otp:'123456'};
 const action=form.onsubmit({preventDefault(){},target:form});x.respond(x.requests.at(-1),403,{detail:'PLATFORM_MFA_INVALID'});await action;
 assert.equal(x.requests.length,2);assert.equal(x.nodes.get('#workspace').hidden,true);assert.match(x.nodes.get('#notice').textContent,/验证码无效/);
});
test('logout while uncertain authentication is recovering prevents the late read from reopening platform access',async()=>{
 const x=await signedOut();x.subject.loggedIn({mfa_enrolled:true,mfa_verified:false});const form=x.nodes.get('#otp-form');form.fields={otp:'123456'};
 const action=form.onsubmit({preventDefault(){},target:form});x.requests.at(-1).reject(Error('write response lost'));await tick();const recover=x.requests.at(-1);
 x.subject.loggedOut();x.respond(recover,200,{operator:{mfa_enrolled:true,mfa_verified:true}});await action;
 assert.equal(x.requests.length,3);assert.equal(x.nodes.get('#workspace').hidden,true);assert.equal(x.nodes.get('#login-panel').hidden,false);
});
