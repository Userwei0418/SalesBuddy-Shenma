import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const code=await readFile(new URL('../browser-platform.js',import.meta.url),'utf8');
function setup({mode='live',host='localhost',reject=false}={}){
 const calls=[],events=[],previewCalls=[];
 const window={SALES_MODE:mode,location:{href:`http://${host}:5186/`,origin:`http://${host}:5186`},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},dispatchEvent:event=>events.push(event.detail.pending),SalesPreview:{request:options=>{previewCalls.push(options.url);options.success({statusCode:200,data:{preview:true}});}}};
 const context={window,Blob,File,URL,FormData,Headers,AbortController,setTimeout,clearTimeout,queueMicrotask,sessionStorage:{getItem:()=>null},navigator:{},screen:{},requestAnimationFrame:callback=>callback(),fetch:async(url,options)=>{calls.push({url:String(url),...options});if(reject)throw new Error('fixture transport failure');return new Response('{}');}};
 vm.runInNewContext(code,context);const wx={};window.SalesPlatform.install(wx);
 const send=(extra={})=>new Promise((resolve,reject)=>wx.request({url:'/api/v1/auth/password/login',method:'POST',header:{Authorization:'Bearer fixture-only','X-Fixture-Key':'not-a-real-key','Content-Type':'application/json'},data:{account_code:'fixture',password:'fixture',role:'sales'},...extra,success:resolve,fail:reject}));
 return{calls,events,previewCalls,send,withLocalLogin:window.SalesPlatform.withLocalLogin};
}
test('local login rewrites only one synchronous exact password POST and removes all browser credentials',async()=>{
 const p=setup();await p.withLocalLogin(()=>Promise.all([p.send(),p.send()]));
 assert.equal(new URL(p.calls[0].url).pathname,'/local-login');assert.equal(p.calls[0].body,undefined);assert.deepEqual([...p.calls[0].headers],[]);assert.equal(p.calls[0].credentials,'omit');
 assert.equal(new URL(p.calls[1].url).pathname,'/api/v1/auth/password/login');assert.equal(p.calls[1].credentials,'same-origin');assert.equal(p.calls[1].headers.get('authorization'),'Bearer fixture-only');assert.equal(JSON.parse(p.calls[1].body).account_code,'fixture');
 assert.deepEqual(p.events,[true,true,false,false]);
});
test('unmatched requests cannot consume the next exact login and redirects do not survive scope exit',async()=>{
 const p=setup();await p.withLocalLogin(()=>Promise.all([p.send({method:'GET',data:undefined}),p.send({url:'/api/v1/auth/password/login?x=1'}),p.send()]));
 assert.equal(new URL(p.calls[0].url).pathname,'/api/v1/auth/password/login');assert.match(p.calls[1].url,/\?x=1$/);assert.equal(new URL(p.calls[2].url).pathname,'/local-login');
 await p.send();assert.equal(new URL(p.calls[3].url).pathname,'/api/v1/auth/password/login');
});
test('exceptions and deferred callbacks cannot leak a local-login redirect to later requests',async()=>{
 const p=setup();assert.throws(()=>p.withLocalLogin(()=>{throw new Error('fixture callback failure');}),/fixture/);await p.send();
 await p.withLocalLogin(()=>Promise.resolve().then(()=>p.send()));
 assert.ok(p.calls.every(call=>new URL(call.url).pathname==='/api/v1/auth/password/login'));
 const failed=setup({reject:true});await assert.rejects(failed.withLocalLogin(()=>failed.send()));await assert.rejects(failed.send());assert.equal(new URL(failed.calls[1].url).pathname,'/api/v1/auth/password/login');
});
test('preview and nonlocal origins never enable the shortcut or change ordinary authentication',async()=>{
 const preview=setup({mode:'preview'});assert.throws(()=>preview.withLocalLogin(()=>preview.send()),/本机企业工作区/);await preview.send();assert.equal(preview.calls.length,0);assert.equal(preview.previewCalls.length,1);
 const remote=setup({host:'example.invalid'});assert.throws(()=>remote.withLocalLogin(()=>remote.send()),/本机企业工作区/);assert.equal(remote.calls.length,0);
});
