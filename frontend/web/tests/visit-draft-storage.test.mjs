import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source = await readFile(new URL('../browser-platform.js', import.meta.url), 'utf8');
const key = 'visitEntryV2:workspace:user:service:%2Fapi%2Fv1';
const storage = (map = new Map()) => ({getItem:k=>map.get(k)??null,setItem:(k,v)=>map.set(k,v),removeItem:k=>map.delete(k)});
const turn = () => new Promise(resolve=>setImmediate(resolve));
function environment() {
  const disk = new Map(), held = new Set();
  const locks = {async request(name, _options, callback) {
    await turn(); if (held.has(name)) return callback(null);
    held.add(name); try {await callback({name});} finally {held.delete(name);}
  }};
  return {disk, tab({mode='live',actor={workspaceId:'workspace',userId:'user'},local=storage(disk),session=storage()}={}) {
    const window={SALES_MODE:mode,location:{href:'http://localhost:5277/',origin:'http://localhost:5277'}};
    const context={window,sessionStorage:session,localStorage:local,navigator:{locks},URL,Blob,File,FormData,Headers,AbortController,setTimeout,clearTimeout,queueMicrotask,requestAnimationFrame:cb=>cb(),screen:{}};
    vm.runInNewContext(source,context);const wx={};window.SalesPlatform.install(wx);
    wx.setStorageSync('salesSession',actor);
    return {wx,session,local,close:()=>wx.__webVisitDrafts.release()};
  }};
}
async function ready(tab, k=key) {tab.wx.getStorageSync(k);await turn();}
test('draft survives a new tab session; credentials and generic state do not persist',async()=>{
  const env=environment(),a=env.tab();await ready(a);
  a.wx.setStorageSync(key,{transcript:'本机业务草稿',draftId:'a'});
  a.wx.setStorageSync('salesApiAuth',{access_token:'synthetic'});a.wx.setStorageSync('unreviewedDraft','ephemeral');a.close();await turn();
  const b=env.tab();assert.equal(b.wx.getStorageSync(key).transcript,'本机业务草稿');
  assert.equal(b.wx.getStorageSync('salesApiAuth'),'');assert.equal(b.wx.getStorageSync('unreviewedDraft'),'');
  assert.equal([...env.disk.keys()].length,1);b.close();
});
test('locks reject a concurrent tab and stale content after it gains ownership',async()=>{
  const env=environment(),a=env.tab(),b=env.tab();await ready(a);
  a.wx.setStorageSync(key,{text:'first'});await ready(b);
  assert.throws(()=>b.wx.setStorageSync(key,{text:'second'}),{code:'DRAFT_STORAGE_CONFLICT'});
  a.wx.setStorageSync(key,{text:'newer'});a.close();await turn();b.wx.__webVisitDrafts.retry();await turn();
  assert.throws(()=>b.wx.setStorageSync(key,{text:'stale'}),{code:'DRAFT_STORAGE_CONFLICT'});
  assert.equal(b.wx.getStorageSync(key).text,'newer');b.close();
});
test('another tab can save after owner leaves if stored content has not changed',async()=>{
  const env=environment(),a=env.tab(),b=env.tab();await ready(a);a.wx.setStorageSync(key,{text:'same'});await ready(b);
  a.close();await turn();b.wx.__webVisitDrafts.retry();await turn();b.wx.setStorageSync(key,{text:'now owned'});
  assert.equal(b.wx.getStorageSync(key).text,'now owned');b.close();
});
test('no cross-account, service, mode or business-object restore',async()=>{
  const env=environment(),a=env.tab();await ready(a);a.wx.setStorageSync(key,{text:'private'});
  assert.throws(()=>a.wx.getStorageSync(key.replace('workspace:user','other:user')),{code:'DRAFT_STORAGE_SCOPE'});
  assert.throws(()=>a.wx.getStorageSync(key.replace('%2Fapi%2Fv1','https%3A%2F%2Fother.invalid')),{code:'DRAFT_STORAGE_SCOPE'});
  assert.throws(()=>a.wx.getStorageSync('visitEntryV2:workspace:user'),{code:'DRAFT_STORAGE_SCOPE'});
  assert.equal(a.wx.readLegacyVisitDraftSync('visitEntryV2:workspace:user'),'');
  assert.throws(()=>a.wx.setStorageSync('visitEntryV2:workspace:user',{}),{code:'DRAFT_STORAGE_SCOPE'});
  assert.equal(a.wx.getStorageSync(key+':customer:B'),'');
  const preview=env.tab({mode:'preview'});assert.equal(preview.wx.getStorageSync(key),'');
  a.wx.setStorageSync('salesSession',{workspaceId:'other',userId:'user'});
  assert.throws(()=>a.wx.setStorageSync(key,{text:'wrong actor'}),{code:'DRAFT_STORAGE_SCOPE'});a.close();preview.close();
});
test('failed local save retains prior draft and deleting one key preserves another',async()=>{
  const env=environment(),a=env.tab();await ready(a);a.wx.setStorageSync(key,{text:'prior'});
  const second=key+':customer:B';await ready(a,second);a.wx.setStorageSync(second,{text:'other object'});
  const original=a.local.setItem;a.local.setItem=()=>{throw Error('quota');};
  assert.throws(()=>a.wx.setStorageSync(key,{text:'not saved'}),{code:'DRAFT_STORAGE_UNAVAILABLE'});
  assert.equal(a.wx.getStorageSync(key).text,'prior');a.local.setItem=original;
  a.wx.removeStorageSync(key);assert.equal(a.wx.getStorageSync(key),'');assert.equal(a.wx.getStorageSync(second).text,'other object');a.close();
});
test('a tab cannot resurrect a removed draft after a concurrent owner submits',async()=>{
  const env=environment(),a=env.tab(),b=env.tab();await ready(a);a.wx.setStorageSync(key,{draftId:'first'});await ready(b);
  a.wx.removeStorageSync(key);a.close();await turn();b.wx.__webVisitDrafts.retry();await turn();
  assert.throws(()=>b.wx.setStorageSync(key,{draftId:'first'}),{code:'DRAFT_STORAGE_CONFLICT'});assert.equal(b.wx.getStorageSync(key),'');b.close();
});
