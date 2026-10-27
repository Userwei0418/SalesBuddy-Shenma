import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

// Run the imported page and its real draft/signature/write guards; transport is synthetic.
const source = fileURLToPath(new URL('../source/miniprogram/', import.meta.url));
const clone = value => JSON.parse(JSON.stringify(value));
const fields = {follow_up_record:'合成跟进', next_action:'10月28日再次回访', interaction_at:'2026-10-21', created_date:'2026-10-21', contact_name:'合成联系人'};
const turn = () => new Promise(resolve => setImmediate(resolve));
function harness() {
  const storage = new Map(), calls = [], session = {workspaceId:'workspace',userId:'self',userName:'同名同事',role:'sales'};
  let definition;
  const api = {
    getBaseUrl:()=>'/api/v1',
    request:options=>{calls.push(options);return Promise.resolve({version_no:2,...fields});},
    submitVisitStage:async(stage,data)=>{calls.push({stage,data:clone(data)});return {run_id:'review'};},
    waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:{follow_up_score:85,next_action:{passed:true}}}}),
    createVisit:async(id,data)=>{calls.push({method:'POST',id,data:clone(data)});return {id:'visit',fields:data};},
  };
  const wx = {getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,clone(v)),removeStorageSync:k=>storage.delete(k),showToast(){},pageScrollTo(){},setNavigationBarTitle(){}};
  const app = {globalData:{session},ensureLogin:()=>true};
  const context = vm.createContext({getApp:()=>app,wx,Page:p=>{definition=p;},console,setTimeout,clearTimeout,setInterval:()=>1,clearInterval(){}});
  const modules = new Map();
  function load(relative) {
    const filename=path.resolve(source,relative.endsWith('.js')?relative:relative+'.js');
    if(filename===path.join(source,'utils/apiClient.js'))return api;
    if(modules.has(filename))return modules.get(filename).exports;
    const module={exports:{}};modules.set(filename,module);
    vm.runInContext(`(function(require,module,exports){${fs.readFileSync(filename,'utf8')}\n})`,context,{filename})(
      name=>load(path.relative(source,path.resolve(path.dirname(filename),name))),module,module.exports);
    return module.exports;
  }
  load('pages/visit-confirm/index');
  const page={...definition,data:clone(definition.data),setData(patch){Object.assign(this.data,patch);}};
  Object.assign(page.data,{values:clone(fields),customerId:'customer',customerName:'合成客户',customerConfirmed:true,sourceRunId:'source'});
  const draft=load('utils/visitDraft');
  page.userKey=draft.scope(session,api);page.draftKey=draft.key('visitConfirmV2',page.userKey);page.draftReady=true;
  page.draftStore=draft.open(wx,page.draftKey);page.loadAdvice=()=>{};
  return {page,api,calls,session,storage,load};
}

test('exclude recorder by ID; keep a same-name colleague and selections hidden by search',()=>{
  const {page}=harness();
  page.allColleagues=[{id:'self',display_name:'同名同事'},{id:'peer',display_name:'同名同事'},{id:'other',display_name:'另一同事'}];
  page.data.collaboratorIds=['self','other'];page.refresh();
  assert.deepEqual(clone(page.data.colleagues.map(c=>c.id)),['peer','other']);
  assert.deepEqual(clone(page.data.collaboratorIds),['other']);
  page.data.colleagueQuery='同名';page.refresh();page.selectColleagues({detail:{value:['peer','self']}});
  assert.deepEqual(clone(page.data.collaboratorIds),['other','peer']);
  assert.equal(page.data.collaboratorNames,'同名同事、另一同事');
});

test('a restored reviewed payload containing self is invalidated even before directory loads',async()=>{
  for(const selected of [['self','peer'],['peer']]) {
    const {page,calls,load,storage}=harness();
    Object.assign(page.data,{collaboratorIds:selected,reviewPayload:{collaborator_ids:['self','peer']},quality:{follow_up_score:85,next_action:{passed:true}},reviewRunId:'old-review',flowStep:'result',canSubmit:true});
    page.data.reviewedContent=load('utils/visitSnapshot').signature(page.data);
    await page.submitArchive();page.persist();
    assert.equal(calls.length,0);assert.equal(page.data.reviewPayload,null);assert.equal(page.data.reviewRunId,'');
    assert.equal(page.data.canSubmit,false);assert.equal(page.data.flowStep,'edit');assert.match(page.data.errorText,/重新进行 AI 质检/);
    assert.deepEqual(clone(storage.get(page.draftKey).collaboratorIds),['peer']);
  }
});

test('review and archive submit other collaborators only after the user explicitly re-reviews',async()=>{
  const {page,calls}=harness();page.data.collaboratorIds=['self','peer'];
  await page.review();
  assert.deepEqual(calls[0].data.collaborator_ids,['peer']);assert.equal(page.data.canSubmit,true);
  await page.submitArchive();await turn();
  assert.deepEqual(calls.find(c=>c.method==='POST').data.collaborator_ids,['peer']);assert.equal(page.data.archived,true);
});

test('editing uses the record recorder_id and cleans saved selections before PATCH',async()=>{
  const {page,api,calls}=harness();
  page.loadBusinessOptions=()=>{};
  api.request=options=>{
    calls.push(clone(options));
    if(options.path==='/directory/colleagues')return new Promise(()=>{});
    return Promise.resolve({id:'visit',customer_id:'customer',customer_name:'合成客户',recorder_id:'original-recorder',recorder_name:'同名同事',collaborator_ids:['original-recorder','self','peer'],version_no:1,...fields});
  };
  page.onLoad({visitId:'visit'});await turn();
  assert.equal(page.data.recorderId,'original-recorder');assert.deepEqual(clone(page.data.collaboratorIds),['self','peer']);
  page.saveSupplement();await turn();
  assert.deepEqual(calls.find(c=>c.method==='PATCH').data.collaborator_ids,['self','peer']);
  assert.equal(calls.find(c=>c.method==='PATCH').data.version_no,1);
});
