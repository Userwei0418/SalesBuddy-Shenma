const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
function setup({memory=new Map(),user='u',version=2,fail=false}={}) {
  let definition;
  const session={workspaceId:'w',userId:user,role:'sales'}, calls=[], modals=[];
  const app={ensureLogin:()=>true,globalData:{session}};
  const record={id:'v1',customer_id:'c1',customer_name:'客户一',version_no:version,contact_name:'原联系人',follow_up_record:'正文只读'};
  const wx={getStorageSync:k=>structuredClone(memory.get(k)),setStorageSync:(k,v)=>memory.set(k,structuredClone(v)),removeStorageSync:k=>memory.delete(k),
    showToast:r=>calls.push(r),showModal:r=>modals.push(r)};
  const api={request:async r=>{if(r.method==='PATCH'){calls.push(r);if(fail)throw Error('保存失败');return {...record,...r.data,version_no:version+1};}return r.path.startsWith('/visits/')?record:{items:[]};}};
  const file=path.resolve(__dirname,'../miniprogram/pages/visit-confirm/index.js');
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:d=>definition=d,wx,getApp:()=>app,
    require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),n)),setTimeout,clearTimeout,setInterval,clearInterval});
  const page={...definition,data:structuredClone(definition.data),setData(values){for(const [key,value] of Object.entries(values)){
    const parts=key.split('.');let target=this.data;for(const part of parts.slice(0,-1))target=target[part];target[parts.at(-1)]=value;
  }},loadBusinessOptions(){}};
  return {page,memory,session,app,calls,modals,async load(id='v1'){page.onLoad({visitId:id});await tick();},input(value){page.inputField({currentTarget:{dataset:{key:'contact_name'}},detail:{value}});}};
}
for(const visitId of ['', 'v1'])test(`退出登录后${visitId?'已有跟进补充':'新建确认'}离页不抛错、不写匿名或覆盖原稿`,async()=>{
 const h=setup();h.page.searchCustomers=()=>{};await h.load(visitId);h.input('退出前已保存');
 assert.ok(h.memory.has(h.page.draftKey));const before=structuredClone([...h.memory]),calls=h.calls.length;
 h.page.setData({values:{...h.page.data.values,contact_name:'退出后的迟到值'}});h.app.globalData.session=null;
 assert.doesNotThrow(()=>h.page.onHide());assert.doesNotThrow(()=>h.page.onUnload());
 assert.deepEqual([...h.memory],before);assert.equal(h.calls.length,calls);assert.equal(h.page.unloaded,true);
});
test('已有跟进输入即保存，关闭重开恢复；不同记录和账号不串稿',async()=>{
  const a=setup();await a.load();a.input('未写完的联系人');a.page.onUnload();
  const restored=setup({memory:a.memory});await restored.load();assert.equal(restored.page.data.values.contact_name,'未写完的联系人');assert.match(restored.page.data.draftNotice,/恢复/);
  for(const options of [{id:'v2'},{user:'another'}]){const b=setup({memory:a.memory,...options});await b.load(options.id);assert.equal(b.page.data.values.contact_name,'原联系人');}
});
test('服务器版本已变化时保留草稿原版本，禁止套新版本提交',async()=>{
  const a=setup();await a.load();a.input('草稿');const b=setup({memory:a.memory,version:3});await b.load();
  assert.equal(b.page.data.values.contact_name,'草稿');assert.equal(b.page.data.version,2);assert.equal(b.page.data.draftConflict,true);
  b.page.saveSupplement();await tick();assert.equal(b.calls.filter(x=>x.method==='PATCH').length,0);
});
test('提交失败保留，成功仅清当前记录，随后卸载不复活',async()=>{
  const a=setup({fail:true});await a.load();a.input('保留内容');a.page.saveSupplement();await tick();assert.ok(a.memory.has(a.page.draftKey));
  a.memory.set('visitSupplementV1:w:u:v2',{values:{contact_name:'别条记录'}});
  const b=setup({memory:a.memory});await b.load();b.page.saveSupplement();await tick();assert.equal(b.memory.has(b.page.draftKey),false);b.page.onUnload();
  assert.equal(b.memory.has(b.page.draftKey),false);assert.ok(b.memory.has('visitSupplementV1:w:u:v2'));
});
test('另页修改或已清除后旧确认页不覆盖、不自动提交',async()=>{
  const a=setup();await a.load();a.input('第一版');const b=setup({memory:a.memory});await b.load();b.input('另一页新内容');
  a.input('旧页迟到');assert.match(a.page.data.draftSaveError,/其他页面/);a.page.saveSupplement();await tick();assert.equal(a.calls.filter(x=>x.method==='PATCH').length,0);
  b.memory.delete(b.page.draftKey);b.page.onUnload();assert.equal(b.memory.has(b.page.draftKey),false);
});
test('主动放弃只清当前记录，失败或取消均不误清',async()=>{
  const a=setup();await a.load();a.input('本条草稿');a.page.discardSavedDraft();a.modals.pop().success({cancel:true});assert.ok(a.memory.has(a.page.draftKey));
  a.page.discardSavedDraft();a.modals.pop().success({confirm:true});await tick();assert.equal(a.memory.has(a.page.draftKey),false);assert.equal(a.page.data.values.contact_name,'原联系人');
});

 test('同账号同对象切换API服务不读写另一环境草稿',()=>{
  const drafts=require('../miniprogram/utils/visitDraft'),session={workspaceId:'snapshot-w',userId:'same-user'};
  const local=drafts.scope(session,{getBaseUrl:()=> 'http://127.0.0.1:18800/api/v1/'}),online=drafts.scope(session,{getBaseUrl:()=> 'https://www.ericepc.com/api/v1'});
  assert.notEqual(local,online);assert.equal(local,drafts.scope(session,{getBaseUrl:()=> 'http://127.0.0.1:18800/api/v1'}));
  assert.notEqual(drafts.key('visitConfirmV2',local,'customer:1'),drafts.key('visitConfirmV2',online,'customer:1'));
 });

 test('输入和协同人改回初值，重开恢复最终值而非中间草稿',async()=>{
  const a=setup();await a.load();a.input('中间内容');a.input('原联系人');a.page.setData({collaboratorIds:['colleague']});a.page.persist();a.page.setData({collaboratorIds:[]});a.page.persist();a.page.onUnload();
  const b=setup({memory:a.memory});await b.load();assert.equal(b.page.data.values.contact_name,'原联系人');assert.deepEqual(Array.from(b.page.data.collaboratorIds),[]);
 });
 test('其他页面只刷新保存时间不会阻止继续保存，实际内容变化仍拒绝',()=>{
  const drafts=require('../miniprogram/utils/visitDraft'),memory=new Map([['key',{values:{contact_name:'原值'},savedAt:1}]]);
  const storage={getStorageSync:k=>structuredClone(memory.get(k)),setStorageSync:(k,v)=>memory.set(k,structuredClone(v))};
  const current=drafts.open(storage,'key');memory.set('key',{values:{contact_name:'原值'},savedAt:2});
  current.write({values:{contact_name:'继续填写'},savedAt:3});assert.equal(memory.get('key').values.contact_name,'继续填写');
  memory.set('key',{values:{contact_name:'别人已改'},savedAt:4});assert.throws(()=>current.write({values:{contact_name:'过期页面'},savedAt:5}),error=>error.code==='DRAFT_CHANGED');
 });
