const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {firstPreview}=require('../miniprogram/utils/visitAudioPreview');
const playerModule=require('../miniprogram/utils/visitAudioPlayer');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const visitId='10000000-0000-4000-8000-000000000001',audioId='20000000-0000-4000-8000-000000000001';
const audio={id:audioId,playback_path:`/visits/${visitId}/audio`,mime_type:'audio/mpeg',size_bytes:12};
function apiHarness(){
  const storage=new Map(),uploads=[],requests=[],downloads=[],removed=[];
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    uploadFile:o=>{uploads.push(o);return {};},request:o=>{requests.push(o);return {};},
    downloadFile:o=>{downloads.push(o);return {abort(){},onProgressUpdate(){}};},getFileSystemManager:()=>({unlink:o=>removed.push(o.filePath)}),showModal:o=>o.success({confirm:true})};
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];
  const api=require('../miniprogram/utils/apiClient');api.saveAuth({access_token:'test-only',actor:{workspace_id:'workspace',user_id:'owner'}});
  return {api,uploads,requests,downloads,removed,storage};
}
test('preview stops at a complete frame after 15 seconds and skips invalid/short audio',()=>{
  const frame=new Uint8Array(216);frame.set([255,243,104,0]); // MPEG2 LayerIII, 48kbit, 16kHz: 36ms
  const bytes=new Uint8Array(frame.length*600);for(let i=0;i<600;i++)bytes.set(frame,i*frame.length);
  const result=firstPreview([bytes.buffer]);assert.ok(result.seconds>=15&&result.seconds<15.04);assert.equal(result.data.byteLength%216,0);assert.ok(result.data.byteLength<bytes.length);
  assert.equal(firstPreview([bytes.buffer.slice(0,216*400)]),null);assert.equal(firstPreview([new ArrayBuffer(128)]),null);
});
test('unknown upload queries receipt and a manual repeat only reads the same operation',async()=>{
  const h=apiHarness(),first=h.api.transcribeAudio('/temp/recording','customer_create');
  const operation=h.uploads[0].formData;h.uploads[0].fail({errMsg:'timeout'});await tick();
  assert.equal(h.requests.length,1);h.requests[0].success({statusCode:200,data:{status:'unknown',operation_id:operation.operation_id,session_id:operation.session_id,purpose:'customer_create'}});
  await assert.rejects(first,e=>e.operationId===operation.operation_id&&e.recoveryAction==='check_status');
  const second=h.api.transcribeAudio('/temp/recording','customer_create');assert.equal(h.uploads.length,1);
  h.requests[1].success({statusCode:200,data:{status:'completed',operation_id:operation.operation_id,session_id:operation.session_id,purpose:'customer_create',text:'已识别'}});
  assert.equal((await second).text,'已识别');assert.equal(h.uploads.length,1);
});
test('explicit failed retry starts a new generation only after confirmation',async()=>{
  const h=apiHarness(),one=h.api.transcribeAudio('/temp/a','chatbi');const before=h.uploads[0].formData;
  h.uploads[0].success({statusCode:502,data:JSON.stringify({code:'ASR_UPSTREAM_FAILED'})});await assert.rejects(one);
  const two=h.api.transcribeAudio('/temp/a','chatbi');h.requests[0].success({statusCode:200,data:{status:'failed',operation_id:before.operation_id,session_id:before.session_id,purpose:'chatbi',error_code:'ASR_UPSTREAM_FAILED'}});await tick();
  assert.equal(h.uploads.length,2);assert.notEqual(h.uploads[1].formData.operation_id,before.operation_id);assert.notEqual(h.uploads[1].formData.session_id,before.session_id);
  h.uploads[1].success({statusCode:200,data:'{"text":"已完成"}'});await two;
});
test('preview request shares explicit final session and duplicate clicks reuse one flight',async()=>{
  const h=apiHarness(),options={sessionId:visitId,operationId:audioId,stage:'preview'};
  const a=h.api.transcribeAudio('/temp/preview','visit_entry',options),b=h.api.transcribeAudio('/temp/preview','visit_entry',options);
  assert.equal(a,b);assert.equal(h.uploads.length,1);assert.equal(h.uploads[0].formData.stage,'preview');assert.equal(h.uploads[0].formData.session_id,visitId);
  h.uploads[0].success({statusCode:200,data:'{"text":"预览"}'});await a;
});
test('playback uses a single authenticated transfer and removes late files after logout',async()=>{
  const h=apiHarness(),read=h.api.readVisitAudio(visitId);assert.equal(h.downloads.length,1);assert.equal(h.requests.length,0);
  assert.equal(h.downloads[0].header.Authorization,'Bearer test-only');assert.doesNotMatch(h.downloads[0].url,/token|Bearer/);assert.equal(h.downloads[0].header.Range,undefined);
  h.api.saveAuth(null);h.downloads[0].success({statusCode:200,tempFilePath:'/temporary/audio'});
  await assert.rejects(read,{code:'SESSION_CHANGED'});assert.deepEqual(h.removed,['/temporary/audio']);
});
test('owner player controls are lazy, seek locally and remove temporary audio on disposal',async()=>{
  let owner='a',calls=0,played=0,paused=0,seeked=0;const removed=[];
  const player={duration:12,currentTime:0,play(){played++;this.start();},pause(){paused++;this.paused();},stop(){},destroy(){},seek(v){seeked=v;},
    onPlay(fn){this.start=fn;},onPause(fn){this.paused=fn;},onEnded(){},onError(){},onTimeUpdate(fn){this.update=fn;},onCanplay(fn){this.ready=fn;}};
  const control=playerModule.create({api:{readVisitAudio:async()=>{calls++;return {tempFilePath:'/tmp/internal-audio'};}},platform:{getFileSystemManager:()=>({getFileInfo:o=>o.success({size:12}),unlink:o=>removed.push(o.filePath)}),createInnerAudioContext:()=>player},visitId,audio,identity:()=>owner,onChange(){}});
  assert.equal(calls,0);await control.toggle();assert.equal(played,1);player.ready();control.seek(50);assert.equal(seeked,6);await control.toggle();assert.equal(paused,1);
  owner='b';await control.toggle();assert.deepEqual(removed,['/tmp/internal-audio']);assert.equal(calls,1);control.dispose();
  assert.equal(playerModule.metadata({...audio,playback_path:'/visits/other/audio'},visitId),null);
});
test('home final recording does not wait for preview, keeps source metadata and restores its own draft',async()=>{
  const storage=new Map(),navigations=[],session={workspaceId:'w',userId:'u',role:'sales'};
  const app={globalData:{session,role:'sales'}};let definition;
  const wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
    getFileSystemManager:()=>({saveFile:o=>o.success({savedFilePath:'/saved/final.mp3'})}),navigateTo:o=>{navigations.push(o.url);o.success();}};
  const filename=path.resolve(__dirname,'../miniprogram/pages/index/index.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{require:name=>name.endsWith('apiClient')?{getBaseUrl:()=>'/api/v1',transcribeAudio(){throw Error('final must use import');}}:require(path.resolve(path.dirname(filename),name)),Page:p=>{definition=p;},wx,getApp:()=>app,clearTimeout});
  const page={...definition,data:{...definition.data},homeVisible:true,asrSessionId:visitId,liveTranscriptionPromise:new Promise(()=>{}),recordingOwner:JSON.stringify(['w:u','sales','',[],'','']),setData(p){Object.assign(this.data,p);}};
  await page.processVisitRecording({tempFilePath:'/temp/final.mp3'});assert.equal(navigations.length,1);
  const draft=[...storage].find(([key])=>key.startsWith('visitEntryV2:'))[1];assert.equal(draft.asrSessionId,visitId);assert.equal(draft.audioSourceKind,'recording');assert.equal(draft.localFilePath,'/saved/final.mp3');assert.equal(draft.transcript,'');
  page.restoreVisitRecording();assert.equal(page.data.visitTranscriptVisible,true);assert.equal(page.pendingRecordingContext,`recording:${visitId}`);
});
