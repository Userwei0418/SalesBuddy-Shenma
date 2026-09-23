const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;});return {promise,resolve,reject}; };
const clone = v => v === undefined ? undefined : JSON.parse(JSON.stringify(v));
function harness({storage=new Map(),api:overrides={},user='user-a'}={}) {
  const calls=[],modals=[],timers=new Map();let timer=0, definition, storageFails=false;
  const app={globalData:{session:{workspaceId:'workspace',userId:user,role:'sales'}},ensureLogin:()=>true};
  const recorder={onStart(fn){this.startCallback=fn;},onStop(fn){this.stopCallback=fn;},onError(){},offStart(){},offStop(){},offError(){},stop(){this.stopCallback({tempFilePath:'temp-audio',duration:2000});}};
  const api={
    getCustomerReference:async id=>({id,name:'客户 '+id}),listCustomers:async()=>({items:[]}),
    submitVisitStage:async(stage,body)=>{calls.push({kind:'structure',body});return {run_id:'run-1'};},
    waitVisitRun:async()=>({run_id:'run-1',result:{fields:{follow_up_record:'示例内容'}}}),
    runAgent:async()=>null,
    uploadVisitFile:async(file,name)=>{calls.push({kind:'upload',file,name});return {id:'import-1',status:'queued'};},
    request:async({path})=>{calls.push({kind:'request',path});return {status:'succeeded',filename:'访谈.mp3',extracted_text:'录音转写内容'};},
    ...overrides,
  };
  const wx={getStorageSync:key=>clone(storage.get(key)),setStorageSync:(key,value)=>{if(storageFails)throw Error('quota');storage.set(key,clone(value));},removeStorageSync:key=>{if(storageFails)throw Error('quota');storage.delete(key);},getRecorderManager:()=>recorder,
    showModal:options=>modals.push(options),showToast:options=>calls.push({kind:'toast',...options}),
    navigateBack:()=>calls.push({kind:'back'}),switchTab:()=>calls.push({kind:'home'}),navigateTo:options=>calls.push({kind:'navigate',url:options.url}),
    getFileSystemManager:()=>({saveFile:options=>options.success({savedFilePath:'saved-audio'}),removeSavedFile:options=>calls.push({kind:'remove-file',path:options.filePath})}),
    getWindowInfo:()=>({statusBarHeight:24}),getMenuButtonBoundingClientRect:()=>({top:30,height:32}),
  };
  const file=path.resolve(__dirname,'../miniprogram/pages/visit-entry/index.js');
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:value=>definition=value,require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),name)),wx,getApp:()=>app,
    setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),setInterval:()=>0,clearInterval(){}});
  const page={...definition,data:clone(definition.data),setData(values){Object.assign(this.data,values);}};
  return {page,storage,api,app,wx,recorder,calls,modals,timers,setStorageFailure(value){storageFails=value;},async load(options={}){page.onLoad(options);page.onShow();await tick();return page;},async answer(save){await modals.at(-1).success(save?{confirm:true}:{cancel:true});await tick();}};
}
const key='visitEntryV2:workspace:user-a';
const fill=page=>{page.confirmSelectedCustomer({id:'c1',name:'客户一'});page.inputTranscript({detail:{value:'已转写并人工修改的内容'}});page.toggleFirstVisit({detail:{value:['first']}});};

test('整个录入动作自动恢复；其它客户入口不能把原草稿重新关联',async()=>{
 const h=harness();await h.load();fill(h.page);h.page.setData({entryMode:'file',fileName:'访谈.mp3',importId:'import-done',importStatus:'succeeded'});h.page.appliedImportId='import-done';h.page.persist();
 // Kill without onHide/onUnload: every field change is already durable.
 const restored=harness({storage:h.storage});await restored.load({customerId:'another-customer'});
 assert.equal(restored.page.data.transcript,'已转写并人工修改的内容');assert.equal(restored.page.data.customerId,'c1');assert.equal(restored.page.data.isFirstVisit,true);assert.equal(restored.page.data.importId,'import-done');assert.equal(restored.page.data.entryMode,'file');assert.match(restored.page.data.draftNotice,/已恢复/);assert.equal(restored.calls.filter(x=>x.kind==='upload').length,0);
 const id=restored.page.draftId;restored.page.changeCustomer();restored.page.confirmSelectedCustomer({id:'c2',name:'客户二'});assert.equal(restored.page.draftId,id);assert.equal(restored.page.data.transcript,'已转写并人工修改的内容');
});
test('尚未确认的客户搜索词也自动保存，不强制关联客户',async()=>{
 const h=harness();await h.load();h.page.inputCustomerQuery({detail:{value:'未选择的搜索词'}});h.page.inputTranscript({detail:{value:'暂时未选择客户的录入'}});h.page.onUnload();
 const restored=harness({storage:h.storage});await restored.load({customerId:'route-customer'});assert.equal(restored.page.data.customerId,'');assert.equal(restored.page.data.customerQuery,'未选择的搜索词');assert.equal(restored.page.data.transcript,'暂时未选择客户的录入');
});
test('主动返回保存后再进恢复，空白页不弹窗',async()=>{
 const blank=harness();await blank.load();blank.page.requestBack();assert.equal(blank.modals.length,0);assert.equal(blank.calls.at(-1).kind,'back');
 const h=harness();await h.load();fill(h.page);h.page.requestBack();assert.equal(h.modals.length,1);assert.equal(h.modals[0].confirmText,'保存');assert.equal(h.modals[0].cancelText,'放弃');await h.answer(true);h.page.onHide();h.page.onUnload();
 const restored=harness({storage:h.storage});await restored.load();assert.equal(restored.page.data.transcript,'已转写并人工修改的内容');assert.equal(h.calls.at(-1).kind,'back');
});
test('放弃后生命周期不会把草稿重新写回，也不会清除另一个账号',async()=>{
 const h=harness();await h.load();fill(h.page);h.storage.set('visitEntryV2:workspace:user-b',{transcript:'他人的草稿'});h.storage.set('visitStructuredV2:workspace:user-a',{result:{}});
 h.page.requestBack();await h.answer(false);h.page.onHide();h.page.onUnload();assert.equal(h.storage.has(key),false);assert.equal(h.storage.has('visitStructuredV2:workspace:user-a'),false);assert.equal(h.storage.get('visitEntryV2:workspace:user-b').transcript,'他人的草稿');
 const restored=harness({storage:h.storage});await restored.load();assert.equal(restored.page.data.transcript,'');
});
test('结构化失败保留转写与修改，退出后可直接重提，不重新上传',async()=>{
 const h=harness({api:{waitVisitRun:async()=>{throw Object.assign(Error('结构化失败'),{code:'RUN_FAILED'});}}});await h.load();fill(h.page);h.page.setData({importId:'audio-done',fileName:'访谈.mp3',importStatus:'succeeded'});await h.page.submitTranscript();assert.match(h.page.data.errorText,/原文已保留/);assert.equal(h.page.data.canSubmit,true);assert.equal(h.storage.has('visitStructureRun:workspace:user-a'),false);h.page.onUnload();
 const restored=harness({storage:h.storage});await restored.load();restored.page.inputTranscript({detail:{value:'修改后再次提交'}});await restored.page.submitTranscript();assert.equal(restored.calls.filter(x=>x.kind==='upload').length,0);assert.equal(restored.calls.find(x=>x.kind==='structure').body.text,'修改后再次提交');assert.equal(restored.calls.find(x=>x.kind==='structure').body.source_import_id,'audio-done');assert.ok(restored.calls.some(x=>x.kind==='navigate'));
});
test('等待超时后重试继续已有任务，不重复创建结构化任务',async()=>{
 let waiting=0;const h=harness({api:{waitVisitRun:async()=>{if(!waiting++)throw Object.assign(Error('等待超时'),{code:'RUN_TIMEOUT'});return {run_id:'run-1',result:{fields:{}}};}}});await h.load();fill(h.page);await h.page.submitTranscript();await h.page.submitTranscript();assert.equal(h.calls.filter(x=>x.kind==='structure').length,1);
});
test('清空保留所选客户，附件与原文都清除，撤销恢复且不重新转写',async()=>{
 const h=harness();await h.load();fill(h.page);h.page.setData({importId:'done',fileName:'访谈.mp3',importStatus:'succeeded'});h.page.appliedImportId='done';h.page.persist();h.page.clearTranscript();assert.equal(h.page.data.transcript,'');assert.equal(h.page.data.importId,'');assert.equal(h.page.data.customerId,'c1');assert.equal(h.page.data.undoAvailable,true);assert.equal(h.storage.get(key).transcript,'');h.page.undoClear();assert.equal(h.page.data.transcript,'已转写并人工修改的内容');assert.equal(h.page.data.importId,'done');assert.equal(h.page.data.undoAvailable,false);assert.equal(h.calls.filter(x=>x.kind==='upload').length,0);
});
test('清空后迟到的转写和结构化结果均不能回填或跳转',async()=>{
 const pendingImport=deferred();const h=harness({api:{request:()=>pendingImport.promise}});await h.load();fill(h.page);h.page.setData({importId:'pending',fileName:'访谈.mp3',importStatus:'processing'});h.page.persist();h.page.pollImport();h.page.clearTranscript();pendingImport.resolve({status:'succeeded',filename:'访谈.mp3',extracted_text:'迟到的旧内容'});await tick();assert.equal(h.page.data.transcript,'');assert.equal(h.storage.get(key).transcript,'');
 const result=deferred();const ai=harness({api:{waitVisitRun:()=>result.promise}});await ai.load();fill(ai.page);const request=ai.page.submitTranscript();await tick();ai.page.clearTranscript();result.resolve({result:{fields:{follow_up_record:'旧结构化结果'}}});await request;assert.equal(ai.page.data.transcript,'');assert.equal(ai.calls.filter(x=>x.kind==='navigate').length,0);assert.equal(ai.storage.has('visitStructuredV2:workspace:user-a'),false);
});
test('放弃时结构化接收请求仍在路上，回来后也不能复活任务缓存',async()=>{
 const accepted=deferred();const h=harness({api:{submitVisitStage:()=>accepted.promise}});await h.load();fill(h.page);const request=h.page.submitTranscript();h.page.requestBack();await h.answer(false);accepted.resolve({run_id:'late'});await request;assert.equal(h.storage.has(key),false);assert.equal(h.storage.has('visitStructureRun:workspace:user-a'),false);
});
test('后台结构化完成只保留结果，不在其它页面突然跳转',async()=>{
 const result=deferred();const h=harness({api:{waitVisitRun:()=>result.promise}});await h.load();fill(h.page);const request=h.page.submitTranscript();h.page.onHide();result.resolve({run_id:'run-1',result:{fields:{}}});await request;assert.equal(h.calls.filter(x=>x.kind==='navigate').length,0);assert.ok(h.storage.get(key));
});
test('本地保存失败时不能伪报保存成功、退出或提交AI',async()=>{
 const h=harness();await h.load();fill(h.page);h.setStorageFailure(true);h.page.inputTranscript({detail:{value:'最新未保存内容'}});assert.match(h.page.data.draftSaveError,/未能保存/);h.page.requestBack();await h.answer(true);assert.equal(h.calls.filter(x=>x.kind==='back').length,0);await h.page.submitTranscript();assert.equal(h.calls.filter(x=>x.kind==='structure').length,0);
 h.page.clearTranscript();assert.equal(h.page.data.transcript,'最新未保存内容');
});
test('下一步正常前进不弹窗，归档清理后返回不能复活原文',async()=>{
 const h=harness();await h.load();fill(h.page);await h.page.submitTranscript();assert.equal(h.modals.length,0);h.page.onHide();h.storage.delete(key);h.page.onShow();await tick();assert.equal(h.page.data.transcript,'');h.page.onUnload();assert.notEqual(h.storage.get(key)?.transcript,'已转写并人工修改的内容');
});
test('录音已结束时主动返回保存，持久化音频后退出，下次可续传',async()=>{
 const h=harness();await h.load();h.page.setData({isRecording:true});h.page.requestBack();await h.answer(true);await tick();assert.equal(h.storage.get(key).localFilePath,'saved-audio');assert.equal(h.calls.filter(x=>x.kind==='upload').length,0);assert.ok(h.calls.some(x=>x.kind==='back'));h.page.onUnload();
 const restored=harness({storage:h.storage});await restored.load();await tick();assert.equal(restored.calls.filter(x=>x.kind==='upload').length,1);assert.equal(restored.page.data.transcript,'录音转写内容');
});
test('不同账号不恢复草稿，切换账号后旧页面不再写入',async()=>{
 const h=harness();await h.load();fill(h.page);const before=clone(h.storage.get(key));h.app.globalData.session.userId='user-b';h.page.inputTranscript({detail:{value:'旧页面迟到输入'}});assert.deepEqual(h.storage.get(key),before);const other=harness({storage:h.storage,user:'user-b'});await other.load();assert.equal(other.page.data.transcript,'');
});

test('任务返回时本地存储读取异常不会抛出未处理错误或覆盖原文',async()=>{
 const result=deferred();const h=harness({api:{waitVisitRun:()=>result.promise}});await h.load();fill(h.page);const request=h.page.submitTranscript();await tick();h.wx.getStorageSync=()=>{throw Error('storage unreadable');};result.resolve({result:{fields:{}}});await assert.doesNotReject(request);assert.equal(h.page.data.transcript,'已转写并人工修改的内容');assert.match(h.page.data.draftSaveError,/不可读/);
});
test('录音停止异常后仍可再次主动返回，保存文件失败也不能误退出',async()=>{
 const h=harness();await h.load();h.page.exitAfterRecording=true;h.page.fail('录音失败');assert.equal(h.page.exitAfterRecording,false);h.page.inputTranscript({detail:{value:'保留手工文字'}});h.page.fileSavePromise=Promise.resolve('');h.page.requestBack();await h.answer(true);assert.equal(h.calls.filter(x=>x.kind==='back').length,0);
});
test('附件移除保存失败时保留附件与持久文件',async()=>{
 const h=harness();await h.load();h.page.setData({localFilePath:'saved-audio',fileName:'待续传.mp3',importStatus:'failed'});h.page.persist();h.setStorageFailure(true);h.page.removeFile();assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.calls.filter(x=>x.kind==='remove-file').length,0);
});


test('转写来源标记只在文字成功提取后启用，手工输入和处理中保持未转写',async()=>{
 const pending=deferred();const h=harness({api:{request:()=>pending.promise}});await h.load();
 assert.equal(h.page.data.hasTranscription,false);h.page.inputTranscript({detail:{value:'手工输入'}});assert.equal(h.page.data.hasTranscription,false);
 h.page.setData({importId:'pending',importStatus:'processing'});h.page.persist();h.page.pollImport();assert.equal(h.page.data.hasTranscription,false);
 pending.resolve({status:'succeeded',filename:'录音.mp3',extracted_text:'识别成功的文字'});await tick();
 assert.equal(h.page.data.hasTranscription,true);assert.match(h.page.data.transcript,/识别成功的文字/);assert.equal(h.storage.get(key).hasTranscription,true);
 h.page.removeFile();assert.equal(h.page.data.hasTranscription,true);h.page.inputTranscript({detail:{value:'识别后修改的文字'}});h.page.onUnload();
 const restored=harness({storage:h.storage});await restored.load();assert.equal(restored.page.data.hasTranscription,true);assert.equal(restored.page.data.transcript,'识别后修改的文字');
});
test('转写失败或未提取到文字，不把已有手工输入标记为转写成功',async()=>{
 for(const response of [{status:'failed',error_message:'识别失败'},{status:'succeeded',extracted_text:''}]){
  const h=harness({api:{request:async()=>response}});await h.load();h.page.inputTranscript({detail:{value:'保留手工文字'}});h.page.setData({importId:'empty'});h.page.persist();h.page.pollImport();await tick();
  assert.equal(h.page.data.hasTranscription,false);assert.equal(h.page.data.transcript,'保留手工文字');
  const restored=harness({storage:h.storage});await restored.load();assert.equal(restored.page.data.hasTranscription,false);
 }
});
test('旧版草稿只有成功转写的记录才恢复转写来源标记',async()=>{
 for(const draft of [{transcript:'旧手工文字'},{transcript:'旧转写文字',appliedImportId:'done'},{transcript:'旧转写文字',importStatus:'succeeded'}]){
  const h=harness({storage:new Map([[key,draft]])});await h.load();assert.equal(h.page.data.hasTranscription,Boolean(draft.appliedImportId||draft.importStatus==='succeeded'));
 }
});
test('清空重置转写标记，撤销恢复，正式归档后下一次录入不继承',async()=>{
 const h=harness();await h.load();h.page.setData({importId:'done'});h.page.persist();h.page.pollImport();await tick();assert.equal(h.page.data.hasTranscription,true);
 h.page.clearTranscript();assert.equal(h.page.data.hasTranscription,false);assert.equal(h.storage.get(key).hasTranscription,false);
 h.page.undoClear();assert.equal(h.page.data.hasTranscription,true);assert.equal(h.storage.get(key).hasTranscription,true);
 h.page.forwarded=true;h.storage.delete(key);h.page.onShow();await tick();assert.equal(h.page.data.hasTranscription,false);assert.equal(h.page.data.transcript,'');
});

test('本地保存失败保留临时音频和原文，重试原文件后上传一次',async()=>{
 const h=harness();await h.load();fill(h.page);let saves=0;
 h.wx.getFileSystemManager=()=>({saveFile:o=>{saves++;if(saves===1)o.fail({errCode:1300202,errMsg:'saveFile:fail storage quota exceeded'});else o.success({savedFilePath:'recovered-audio'});},removeSavedFile:()=>{}});
 await h.page.uploadFile({path:'original-temp',name:'audio.mp3'});
 assert.equal(h.page.data.localFilePath,'original-temp');assert.equal(h.page.data.localFileTemporary,true);assert.match(h.page.data.errorText,/空间不足.*1300202/);assert.equal(h.page.data.isProcessing,false);
 assert.equal(h.calls.filter(c=>c.kind==='upload').length,0);assert.equal(h.storage.get(key).localFileTemporary,true);
 await h.page.retryImport();await tick();assert.equal(saves,2);assert.equal(h.calls.filter(c=>c.kind==='upload').length,1);assert.equal(h.calls.find(c=>c.kind==='upload').file,'recovered-audio');assert.match(h.page.data.transcript,/已转写并人工修改的内容/);
});
test('保存失败可直接上传临时文件，网络失败仍可继续而不丢音频',async()=>{
 let tries=0;const h=harness({api:{uploadVisitFile:async(path)=>{assert.equal(path,'temp');if(!tries++)throw Error('网络连接中断');return {id:'recovered',status:'queued'};}}});await h.load();
 h.wx.getFileSystemManager=()=>({saveFile:o=>o.fail({errMsg:'quota exceeded'}),removeSavedFile:()=>{}});
 await h.page.uploadFile({path:'temp',name:'audio.mp3'});await h.page.uploadTemporaryFile();
 assert.equal(h.page.data.localFilePath,'temp');assert.equal(h.page.data.localFileTemporary,true);assert.match(h.page.data.errorText,/网络/);
 await h.page.uploadTemporaryFile();await tick();assert.equal(tries,2);assert.equal(h.page.data.localFilePath,'');assert.equal(h.page.data.localFileTemporary,false);
});
test('同步异常和空保存回执都解除忙碌状态，禁止保存失败后静默退出',async()=>{
 for(const method of [()=>{throw Error('denied');},o=>o.success({})]){
  const h=harness();await h.load();h.wx.getFileSystemManager=()=>({saveFile:method});
  await h.page.uploadFile({path:'temp',name:'audio.mp3'},true);assert.equal(h.page.data.isProcessing,false);assert.equal(h.page.data.localFileTemporary,true);assert.equal(h.calls.filter(c=>c.kind==='back').length,0);
  h.page.requestBack();await h.answer(true);assert.equal(h.calls.filter(c=>c.kind==='back').length,0);
 }
});
test('恢复的临时路径不冒充已保存文件自动上传；新录音及提交不覆盖待处理文件',async()=>{
 const h=harness();await h.load();fill(h.page);h.wx.getFileSystemManager=()=>({saveFile:o=>o.fail({errMsg:'no such file'})});
 await h.page.uploadFile({path:'temp',name:'audio.mp3'});const restored=harness({storage:h.storage});await restored.load();
 assert.equal(restored.calls.filter(c=>c.kind==='upload').length,0);restored.page.toggleRecording();restored.page.chooseMaterial();await restored.page.submitTranscript();assert.equal(restored.calls.filter(c=>c.kind==='structure').length,0);
 assert.equal(restored.page.data.localFilePath,'temp');assert.equal(restored.calls.filter(c=>c.kind==='toast').length,3);
});
test('草稿存储失败时也保留本次临时录音引用供恢复存储后重试',async()=>{
 const h=harness();await h.load();h.setStorageFailure(true);await h.page.uploadFile({path:'temp',name:'audio.mp3'});
 assert.equal(h.page.data.localFilePath,'temp');assert.equal(h.page.data.localFileTemporary,true);assert.equal(h.page.data.isProcessing,false);
 h.setStorageFailure(false);await h.page.retryImport();await tick();assert.equal(h.calls.filter(c=>c.kind==='upload').length,1);
});
