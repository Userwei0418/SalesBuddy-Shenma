import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const source = path.join(root, 'source/miniprogram');
const clone = value => JSON.parse(JSON.stringify(value));
const fields = {follow_up_record:'Synthetic customer update', next_action:'10月12日测试销售提交样本清单',
  interaction_at:'2026-10-09', created_date:'2026-10-10', contact_name:'Synthetic contact'};
const actorScope = 'w:u:service:%2Fapi%2Fv1';
const structureBody = {customer_id:'customer', text:'Synthetic customer update', source_import_id:'source-import'};

// Actual Web fetch/storage adapter and shared pages, with a receipt-aware synthetic server.
// No real HTTP, browser session, model or business write is involved.
function harness() {
  const session = new Map(), local = new Map(), calls = [], receipts = new Map();
  const control = {dropPost:false, readError:false, runStatus:'waiting_human'};
  const storage = map => ({getItem:key=>map.get(key) ?? null, setItem:(key,value)=>map.set(key,value), removeItem:key=>map.delete(key)});
  const fetchImpl = async (url, options) => {
    const route = url.pathname.replace('/api/v1', '');
    const key = options.headers.get('Idempotency-Key');
    calls.push({route, method:options.method, key, body:options.body && JSON.parse(options.body)});
    assert.equal(url.origin, 'https://sales.example.test');
    assert.equal(options.redirect, 'error');
    if (options.method === 'POST') {
      assert.ok(['/visit-flow/structure','/visit-flow/quality'].includes(route));
      assert.match(key, /^[a-f0-9-]{36}$/);
      if (!receipts.has(key)) receipts.set(key, {run_id:`run-${receipts.size + 1}`});
      if (control.dropPost) throw Error('accepted response lost');
      return new Response(JSON.stringify(receipts.get(key)), {status:202});
    }
    assert.match(route, /^\/agent\/runs\/run-/);
    if (control.readError === true) throw Error('read disconnected');
    if (typeof control.readError === 'number') return new Response('{"detail":"synthetic read failure"}', {status:control.readError});
    return new Response(JSON.stringify({id:route.split('/').at(-1), status:control.runStatus, error_code:'NETWORK_ERROR',
      result:{visit_stage:'quality', fields, quality_review:{follow_up_score:85, next_action:{passed:true}}}}));
  };
  function reload() {
    const app = {globalData:{session:{workspaceId:'w', userId:'u', role:'sales'}}, ensureLogin:()=>true};
    const window = {SALES_MODE:'live', location:{href:'https://sales.example.test/?mode=live', origin:'https://sales.example.test'}};
    const context = vm.createContext({window, sessionStorage:storage(session), localStorage:storage(local),
      URL, Headers, AbortController, queueMicrotask, setTimeout, clearTimeout, setInterval:()=>1, clearInterval(){},
      navigator:{locks:{request:async(name,_options,callback)=>callback({name})}},
      fetch:fetchImpl, getApp:()=>app, console});
    vm.runInContext(fs.readFileSync(path.join(root, 'browser-platform.js'), 'utf8'), context);
    context.wx = {pageScrollTo(){}, showToast(){}, setNavigationBarTitle(){}};
    window.SalesPlatform.install(context.wx);
    context.wx.setStorageSync('salesSession', app.globalData.session);
    const cache = new Map();
    function load(name) {
      const filename = path.resolve(source, name.endsWith('.js') ? name : name + '.js');
      assert.ok(filename.startsWith(source + path.sep));
      if (cache.has(filename)) return cache.get(filename).exports;
      const module = {exports:{}}; cache.set(filename, module);
      vm.runInContext(`(function(require,module,exports){${fs.readFileSync(filename, 'utf8')}\n})`, context, {filename})(
        relative => load(path.relative(source, path.resolve(path.dirname(filename), relative))), module, module.exports);
      return module.exports;
    }
    const api = load('utils/apiClient');
    if (!api.getAuth()) api.saveAuth({access_token:'synthetic-only', actor:{workspace_id:'w', user_id:'u'}});
    function page(stage) {
      let definition;
      context.Page = value => {definition = value;};
      load(`pages/${stage === 'structure' ? 'visit-entry' : 'visit-confirm'}/index`);
      const p = {...definition, data:clone(definition.data), userKey:actorScope, draftReady:true, draftId:'synthetic-draft', entryDraftId:'synthetic-draft',
        draftKey:stage === 'structure' ? 'visitEntryV2:'+actorScope : 'visitConfirmV2:'+actorScope, setData(value){Object.assign(this.data, value);}};
      if (stage === 'quality') Object.assign(p.data, {values:clone(fields), customerId:'customer', customerName:'Synthetic customer',
        customerConfirmed:true, sourceRunId:'source-run', collaboratorIds:[]},
        load('utils/visitFlow').restoreReview({}, context.wx.getStorageSync(p.draftKey) || {values:clone(fields)}));
      return p;
    }
    return {api, wx:context.wx, page, load};
  }
  return {reload, control, session, local, calls, receipts, posts:()=>calls.filter(call=>call.method === 'POST')};
}

for (const stage of ['structure','quality']) test(`${stage}: Web reload retains a lost POST receipt; changed content and identity remain separate`, async () => {
  const h = harness(), body = stage === 'structure' ? structureBody : {customer_id:'customer', source_run_id:'source-run', fields};
  let host = h.reload(); h.control.dropPost = true;
  await assert.rejects(host.api.submitVisitStage(stage, body), /accepted response lost/);
  const first = h.posts()[0].key;
  host = h.reload(); h.control.dropPost = false;
  assert.equal((await host.api.submitVisitStage(stage, body)).run_id, 'run-1');
  assert.equal(h.posts()[1].key, first); assert.equal(h.receipts.size, 1);
  h.control.dropPost = true;
  await assert.rejects(host.api.submitVisitStage(stage, body)); const pending = h.posts().at(-1).key;
  await assert.rejects(host.api.submitVisitStage(stage, {...body, summary:'Edited content'}));
  assert.notEqual(h.posts().at(-1).key, pending);
  await assert.rejects(host.api.submitVisitStage(stage, body)); assert.equal(h.posts().at(-1).key, pending);
  host.api.saveAuth({access_token:'synthetic-other', actor:{workspace_id:'other-workspace', user_id:'u'}});
  await assert.rejects(host.api.submitVisitStage(stage, body)); assert.notEqual(h.posts().at(-1).key, pending);
  assert.equal(h.local.size, 0, 'request identities must not move into persistent localStorage');
  const saved = host.wx.getStorageSync(host.load('utils/requestIdentity').STORAGE_KEY);
  assert.ok(!JSON.stringify(saved).includes('Synthetic'), 'identity store must not retain report content');
});

for (const stage of ['structure','quality']) test(`${stage}: Web page reload resumes a known run after transport/HTTP read failure; terminal failure allows retry`, async () => {
  for (const failure of [true, 403, 404, 500]) {
    const h = harness(); let host = h.reload(), p = host.page(stage);
    h.control.readError = failure;
    if (stage === 'structure') await assert.rejects(p.structureVisit(structureBody)); else await p.review();
    assert.equal(stage === 'structure' ? host.wx.getStorageSync('visitStructureRun:'+actorScope).id : host.wx.getStorageSync(p.draftKey).pendingReviewId, 'run-1');
    host = h.reload(); p = host.page(stage); h.control.readError = false;
    if (stage === 'structure') assert.equal((await p.structureVisit(structureBody)).id, 'run-1');
    else {await p.review(); assert.equal(p.data.reviewRunId, 'run-1'); assert.equal(p.data.canSubmit, true);}
    assert.equal(h.posts().length, 1, 'known run must be read, never re-submitted');
  }
  for (const terminal of ['failed','cancelled']) {
    const h = harness(), host = h.reload(), p = host.page(stage); h.control.runStatus = terminal;
    if (stage === 'structure') {
      await assert.rejects(p.structureVisit(structureBody), error=>error.runStatus === terminal);
      assert.equal(host.wx.getStorageSync('visitStructureRun:'+actorScope), '');
    } else {await p.review(); assert.equal(p.data.pendingReviewId, ''); assert.equal(p.data.canSubmit, false);}
    h.control.runStatus = 'waiting_human';
    if (stage === 'structure') await p.structureVisit(structureBody); else await p.review();
    assert.equal(h.posts().length, 2); assert.notEqual(h.posts()[0].key, h.posts()[1].key);
  }
});
