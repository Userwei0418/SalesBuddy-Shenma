const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const makeAuth=(id='session-a')=>({session_kind:'demo_readonly',access_token:'synthetic-token-'+id,expires_at:new Date(Date.now()+1800000).toISOString(),identity_id:'identity-a',session_id:id,account_code:'FXS001',company:{id:'10b037a3-0d6a-4d63-ba69-f7ffb33dc474',code:'shenma-isolated-demo-20260929',name:'神码演示测试公司（隔离）'},modules:['customers','opportunities','follow-ups','tasks'],data_version:'1'});
function harness(){
 const storage=new Map(),requests=[],navigations=[],pages=[];
 global.wx={getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),removeStorageSync:key=>storage.delete(key),reLaunch:options=>{navigations.push(options.url);options.complete?.();},request:options=>{const task={abort(){options.fail?.({errMsg:'abort'});options.complete?.();}};requests.push({options,task});return task;}};
 global.Page=definition=>pages.push(definition);
 for(const name of ['../miniprogram/utils/restrictedSession','../miniprogram/utils/apiClient'])delete require.cache[require.resolve(name)];
 const auth=require('../miniprogram/utils/restrictedSession'),api=require('../miniprogram/utils/apiClient');
 let app;global.getApp=()=>app;
 vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/app.js','utf8'),{require:name=>name==='./utils/apiClient'?api:name==='./utils/restrictedSession'?auth:require('../miniprogram/'+name.slice(2)),App:def=>app={...def,globalData:{...def.globalData}},wx:global.wx,Page:global.Page});
 return{storage,requests,navigations,pages,auth,api,app,reply:(i,statusCode,data)=>{requests[i].options.success({statusCode,data});requests[i].options.complete?.();}};
}

test('restricted login negotiates protocol and never stores a normal role/access/refresh session',async()=>{
 const h=harness();const result=h.app.loginWithApi(null,'FXS001','synthetic-test-only');
 assert.equal(h.requests[0].options.data.restricted_session_version,1);
 h.reply(0,200,makeAuth());const session=await result;
 assert.equal(session.sessionKind,'demo_readonly');assert.equal(session.role,undefined);assert.equal(h.app.globalData.role,null);
 assert.equal(h.storage.has('salesSession'),false);assert.equal(h.storage.has('salesApiAuth'),false);
 const count=h.requests.length;await h.app.refreshCapabilities();assert.equal(h.requests.length,count);
 await assert.rejects(h.api.request({path:'/tasks',method:'POST',data:{}}),{code:'RESTRICTED_ENDPOINT_DENIED'});
 await assert.rejects(h.api.getCurrentActor(),{code:'RESTRICTED_ENDPOINT_DENIED'});
 await assert.rejects(h.api.taskLightweightCommand('task','submit_completion',{}),{code:'RESTRICTED_ENDPOINT_DENIED'});
 assert.equal(h.requests.length,count);
});

test('restored session uses dedicated verification, no refresh, and removes expired or wrong-environment state',async()=>{
 const h=harness();const auth=makeAuth();h.auth.accept(auth,h.api.getBaseUrl());
 h.app.onLaunch();assert.equal(h.app.globalData.session.sessionKind,'demo_readonly');assert.equal(h.requests.length,0);
 const checked=h.auth.verify();assert.match(h.requests[0].options.url,/\/demo-view\/session$/);
 assert.equal(h.requests[0].options.header.Authorization,'Bearer '+auth.access_token);
 const metadata={...auth};delete metadata.access_token;h.reply(0,200,metadata);await checked;
 assert.equal(h.requests.length,1);
 h.storage.set('salesApiBaseUrl','https://different.invalid/api/v1');assert.equal(h.auth.restore().sessionKind,'demo_readonly');
 h.storage.set('salesReadonlySession',{baseUrl:'https://different.invalid/api/v1',auth});assert.equal(h.auth.restore(),null);
 h.storage.set('salesReadonlySession',{baseUrl:h.api.getBaseUrl(),auth:{...auth,expires_at:'2000-01-01'}});
 assert.equal(h.auth.restore(),null);assert.equal(h.storage.has('salesReadonlySession'),false);
});

test('all ordinary page handlers are blocked before initialization and permitted pages keep their handlers',()=>{
 const h=harness();h.auth.accept(makeAuth(),h.api.getBaseUrl());h.auth.installPageGuard();
 let called=0;global.Page({onLoad(){called++;},beginRecording(){called++;},cleanup(){called+=10;},onUnload(){this.cleanup();}});
 const definition=h.pages.at(-1);const old={...definition,route:'pages/index/index'};
 old.onLoad();old.beginRecording();assert.equal(called,0);assert.equal(h.navigations.at(-1),h.auth.HOME);
 old.onUnload();assert.equal(called,10);
 definition.onLoad.call({route:'pages/demo-view/home/index'});assert.equal(called,11);
 definition.onLoad.call({route:'pages/login/index'});assert.equal(called,12);
});

test('logout aborts pending reads, does not refresh, preserves saved accounts and only revokes its current session',async()=>{
 const h=harness();h.storage.set('salesRememberedAccounts',{entries:['other-account']});h.auth.accept(makeAuth(),h.api.getBaseUrl());
 const response=h.auth.request('/customers');const rejected=assert.rejects(response,{code:'SESSION_CHANGED'});
 h.auth.logout();await rejected;h.reply(0,200,{items:[{name:'late private data'}]});
 assert.equal(h.auth.active(),false);assert.deepEqual(h.storage.get('salesRememberedAccounts'),{entries:['other-account']});
 assert.equal(h.requests.length,2);assert.match(h.requests[1].options.url,/\/demo-view\/logout$/);
 assert.equal(h.storage.has('salesReadonlySession'),false);
});

test('a delayed old response cannot overwrite a new restricted identity and server revocation returns to login',async()=>{
 const h=harness();h.auth.accept(makeAuth(),h.api.getBaseUrl());
 const old=h.auth.verify();const rejected=assert.rejects(old,{code:'SESSION_CHANGED'});
 h.auth.accept(makeAuth('session-b'),h.api.getBaseUrl());await rejected;
 h.reply(0,200,makeAuth());assert.equal(h.auth.session().sessionId,'session-b');
 const read=h.auth.request('/overview');const expired=assert.rejects(read,{statusCode:401});h.reply(1,401,{});await expired;
 assert.equal(h.auth.active(),false);assert.equal(h.navigations.at(-1),'/pages/login/index');
});

test('no invented progress and rendering keeps only explicit approved field labels',()=>{
 harness();const view=require('../miniprogram/pages/demo-view/view');
 assert.equal(view.progress(null),'暂无进度');assert.equal(view.progress(0),'0%');assert.equal(view.progress(120),'暂无进度');
 const rows=view.fields('follow-ups',{display_text:'',phone:'forbidden',raw_summary:'AI-generated'});
 assert.equal(rows.some(row=>['forbidden','AI-generated'].includes(row.value)),false);
 assert.equal(rows.find(row=>row.label==='跟进内容').value,'暂无信息');
 assert.equal(view.fields('opportunities',{amount:'125000'}).find(row=>row.label==='金额').value,'12.5 万元');
 assert.match(view.present('customers',{industry:'enterprise_software',stage:'prospect'}).subtitle,/企业软件.*潜在客户/);
 assert.equal(view.fields('follow-ups',{type:'offline_meeting'}).find(row=>row.label==='类型').value,'线下会议');
 assert.equal(view.fields('opportunities',{stage:'solution'}).find(row=>row.label==='阶段').value,'方案沟通－50%');
});


test('a later login or logout invalidates an already received restricted login receipt',async()=>{
 const h=harness(),response=h.api.loginWithAccount('FXS001','synthetic-test-only');
 h.reply(0,200,makeAuth());const auth=await response;assert.equal(h.api.isCurrentRestrictedLogin(auth),true);
 h.api.saveAuth(null);assert.equal(h.api.isCurrentRestrictedLogin(auth),false);
});

test('pagination resets the visible list when the server data version changes',async()=>{
 const h=harness();h.auth.accept(makeAuth(),h.api.getBaseUrl());
 let definition;const view=require('../miniprogram/pages/demo-view/view');
 const calls=[];const auth={...h.auth,request:async path=>{calls.push(path);return {items:[{id:'new',name:'New'}],total:2,data_version:'2'};}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/demo-view/list/index.js','utf8'),{require:()=>({...view,auth}),Page:d=>definition=d,Set});
 const page={...definition,data:{...definition.data,items:[{id:'old'}],ready:true,loading:false,more:true},kind:'customers',_visible:true,_loadSerial:1,_dataVersion:'1',setData(data){Object.assign(this.data,data);},async load(){this.resetCount=(this.resetCount||0)+1;assert.equal(this.data.items.length,0);}};
 await page.more();assert.equal(page.resetCount,1);assert.deepEqual(calls,['/customers']);assert.equal(page.data.items.length,0);
});


test('read-only lightweight tasks use four states and never expose checklist progress or actions',()=>{
 harness();const view=require('../miniprogram/pages/demo-view/view');
 for(const [status,label] of Object.entries({in_progress:'进行中',completed:'已完成',deferred:'已延期',needs_adjustment:'需调整'})){
  const task={execution_mode:'execution_v3',status,processing_reason:status==='needs_adjustment'?'awaiting_review':null,progress_percent:99};
  const card=view.present('tasks',task),rows=view.fields('tasks',task);
  assert.ok(card.subtitle.startsWith(label));assert.doesNotMatch(card.subtitle,/%|暂无进度/);assert.equal(rows.some(row=>row.label==='实际进度'),false);
 }
 for(const [processing_reason,label] of Object.entries({adjustment_request:'调整请求',paused:'已暂停',awaiting_review:'待验收'}))assert.match(view.present('tasks',{execution_mode:'execution_v3',status:'needs_adjustment',processing_reason}).subtitle,new RegExp(label));
 assert.match(view.present('tasks',{status:'in_progress',execution_mode:'execution_v2',progress_percent:0}).subtitle,/0%/);
});


test('business navigation keeps working from detail and exposes only available modules',()=>{
 const view=require('../miniprogram/pages/demo-view/view');
 assert.deepEqual(view.navigation(['customers','opportunities','follow-ups','tasks']).map(item=>item.label),['总览','客户','商机','任务','我的']);
 assert.deepEqual(view.navigation([]).map(item=>item.key),['home','account']);
 for(const item of view.navigation(['customers','opportunities','tasks']))for(const icon of [item.icon,item.selectedIcon])assert.ok(fs.existsSync(__dirname+'/../miniprogram'+icon));
 const destinations=[];const page={data:{activeKey:'customers'},openModule:e=>destinations.push(e.currentTarget.dataset.module),openHome:()=>destinations.push('home'),openAccount:()=>destinations.push('account')};
 view.lifecycle.openNavigation.call(page,{currentTarget:{dataset:{key:'customers'}}});
 view.lifecycle.openNavigation.call(page,{currentTarget:{dataset:{key:'home'}}});
 assert.deepEqual(destinations,['customers','home']);
 for(const name of ['common','home/index','list/index','detail/index','account/index'])assert.doesNotMatch(fs.readFileSync(__dirname+'/../miniprogram/pages/demo-view/'+name+'.wxml','utf8'),/访客|只读|受限|专用浏览|AI|人工智能|语音|sb-ai|新增|创建|提交|编辑/);
});

test('overview renders counts before recent sections finish and ignores responses after leaving',async()=>{
 const view=require('../miniprogram/pages/demo-view/view');let definition,current=true,finish;
 const calls=[];const auth={request:async path=>{calls.push(path);if(path==='/overview')return {customer_count:8,opportunity_count:9,follow_up_count:10,task_count:11};if(path==='/follow-ups')return new Promise(resolve=>finish=resolve);throw new Error('暂时无法加载，请重试');}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/demo-view/home/index.js','utf8'),{require:()=>({...view,auth}),Page:d=>definition=d,Set,Date,Promise});
 const page={...definition,data:{...definition.data,modules:[{key:'customers'},{key:'follow-ups'},{key:'tasks'}]},setData(values){for(const [key,value] of Object.entries(values)){const match=/^sections\[(\d+)\]$/.exec(key);if(match)this.data.sections[Number(match[1])]=value;else this.data[key]=value;}},checked:fn=>fn(()=>current)};
 const pending=page.load();await new Promise(setImmediate);
 assert.equal(page.data.ready,true);assert.equal(page.data.counts[0].value,8);assert.equal(page.data.sections[0].loading,true);assert.equal(page.data.sections[1].loading,false);assert.match(page.data.sections[1].error,/暂时/);
 current=false;finish({items:[{id:'late',customer:{name:'old session record'}}]});await pending;
 assert.equal(page.data.sections[0].items.length,0);assert.deepEqual(calls,['/overview','/follow-ups','/tasks']);
});


test('Shenma read-only session rejects another company and ignores a stale foreign endpoint',async()=>{
 const h=harness();
 h.storage.set('salesApiBaseUrl','https://foreign.invalid/api/v1');
 const good=makeAuth();
 assert.throws(()=>h.auth.accept({...good,company:{...good.company,code:'demo-sales-workspace'}},h.api.getBaseUrl()),{code:'SESSION_CHANGED'});
 assert.throws(()=>h.auth.accept({...good,company:{...good.company,id:'another-company'}},h.api.getBaseUrl()),{code:'SESSION_CHANGED'});
 h.auth.accept(good,h.api.getBaseUrl());
 const read=h.auth.request('/overview');
 assert.equal(h.requests[0].options.url,'https://salesbuddy.shenzhoukuntai.com:28899/api/v1/demo-view/overview');
 h.reply(0,200,{customer_count:8});await read;
 h.auth.logout();assert.equal(h.requests[1].options.url,'https://salesbuddy.shenzhoukuntai.com:28899/api/v1/demo-view/logout');
});
