const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;});return {promise,resolve,reject}; };
const clone = v => v === undefined ? undefined : JSON.parse(JSON.stringify(v));
const registered=(key,id='20000000-0000-4000-8000-000000000001',filename='访谈.mp3')=>({upload_intent_id:key,state:'registered',id,status:'queued',filename});
function harness({storage=new Map(),api:overrides={},user='user-a',permissions,guard=false}={}) {
  const calls=[],modals=[],timers=new Map();let timer=0, definition, storageFails=false;
  const app={globalData:{session:{workspaceId:'workspace',userId:user,role:'sales',capabilities:{'visit.upload':true,'visit.read':true,'visit.retry_import':true},...(permissions?{permissions:{'access.mini_program':true,'visit.create':true,...permissions}}:{})}},ensureLogin:()=>true};
  const recorder={onStart(fn){this.startCallback=fn;},onStop(fn){this.stopCallback=fn;},onError(){},offStart(){},offStop(){},offError(){},stop(){this.stopCallback({tempFilePath:'temp-audio',duration:2000});}};
  const api={
    getCustomerReference:async id=>({id,name:'客户 '+id}),listCustomers:async()=>({items:[]}),
    submitVisitStage:async(stage,body)=>{calls.push({kind:'structure',body});return {run_id:'run-1'};},
    waitVisitRun:async()=>({run_id:'run-1',result:{fields:{follow_up_record:'示例内容'}}}),
    runAgent:async()=>null,
    uploadVisitFile:async(file,name,onProgress,key)=>{calls.push({kind:'upload',file,name,key});return registered(key,undefined,name);},
    getVisitUploadIntent:async key=>{calls.push({kind:'lookup',key});throw Object.assign(Error('未登记'),{statusCode:404,serverCode:'UPLOAD_INTENT_NOT_FOUND'});},
    retryVisitImport:async(id,key)=>{calls.push({kind:'retry-import',id,key});return {id,status:'queued'};},
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
  const accessModule={exports:{}};vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../miniprogram/utils/access.js'),'utf8'),{module:accessModule,wx});
  app.can=permission=>accessModule.exports.can(app.globalData.session,permission);
  if(guard)app.guardPage=(page,route)=>{accessModule.exports.protectActions(app,page,route);page.setData(accessModule.exports.flags(app.globalData.session,route));return accessModule.exports.pageAllowed(app.globalData.session,route);};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:value=>definition=value,require:name=>name.endsWith('apiClient')?api:name.endsWith('/access')?accessModule.exports:require(path.resolve(path.dirname(file),name)),wx,getApp:()=>app,
    setTimeout:fn=>{timers.set(++timer,fn);return timer;},clearTimeout:id=>timers.delete(id),setInterval:()=>0,clearInterval(){}});
  const page={...definition,data:clone(definition.data),setData(values){Object.assign(this.data,values);}};
  return {page,storage,api,app,wx,recorder,calls,modals,timers,setStorageFailure(value){storageFails=value;},async load(options={}){page.onLoad(options);page.onShow();await tick();return page;},async answer(save){await modals.at(-1).success(save?{confirm:true}:{cancel:true});await tick();}};
}
const key='visitEntryV2:workspace:user-a';
test('退出登录后入口离页不抛错、不覆盖原稿或写匿名草稿',async()=>{
 const h=harness();await h.load();h.page.inputTranscript({detail:{value:'已保存原文'}});
 const before=clone([...h.storage]),calls=h.calls.length;h.page.setData({transcript:'退出后的迟到内容'});h.app.globalData.session=null;
 assert.doesNotThrow(()=>h.page.onHide());assert.doesNotThrow(()=>h.page.onUnload());
 assert.deepEqual([...h.storage],before);assert.equal(h.calls.length,calls);assert.equal(h.page.unloaded,true);
});
const fill=page=>{page.confirmSelectedCustomer({id:'c1',name:'客户一'});page.inputTranscript({detail:{value:'已转写并人工修改的内容'}});page.toggleFirstVisit({detail:{value:['first']}});};

for(const message of ['内部错误','未收到完整上传回执，本地文件已保留，请稍后重试'])test(`上传失败保留原文和本地原文件，显式重试后按同一导入轮询：${message}`,async()=>{
 const h=harness({api:{uploadVisitFile:async()=>{throw Error(message);}}});await h.load();fill(h.page);
 await h.page.uploadFile({path:'original-temp',name:'访谈.mp3',size:123});
 assert.equal(h.page.data.importStatus,'failed');assert.equal(h.page.data.isProcessing,false);assert.match(h.page.data.errorText,new RegExp(message));
 assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.data.transcript,'已转写并人工修改的内容');
 assert.equal(h.storage.get(key).localFilePath,'saved-audio');assert.equal(h.calls.filter(x=>x.kind==='remove-file').length,0);
 h.api.uploadVisitFile=async(path,name,progress,key)=>{assert.equal(path,'saved-audio');assert.equal(name,'访谈.mp3');return registered(key);};
 await h.page.retryImport();assert.equal(h.page.data.uploadIntentState,'retryable_failure');assert.equal(h.page.data.importId,'');
 await h.page.retryImport();await tick();assert.equal(h.page.data.importId,'20000000-0000-4000-8000-000000000001');assert.equal(h.page.data.importStatus,'succeeded');
 assert.ok(h.calls.some(x=>x.path==='/visit-imports/20000000-0000-4000-8000-000000000001'));assert.equal(h.page.data.localFilePath,'');
 assert.equal(h.calls.filter(x=>x.kind==='remove-file').length,1);assert.match(h.page.data.transcript,/已转写并人工修改的内容/);
});

test('整个录入动作从相同入口恢复，客户入口与通用草稿隔离',async()=>{
 const h=harness();await h.load();fill(h.page);h.page.setData({entryMode:'file',fileName:'访谈.mp3',importId:'import-done',importStatus:'succeeded'});h.page.appliedImportId='import-done';h.page.persist();
 // Kill without onHide/onUnload: every field change is already durable.
 const restored=harness({storage:h.storage});await restored.load();
 assert.equal(restored.page.data.transcript,'已转写并人工修改的内容');assert.equal(restored.page.data.customerId,'c1');assert.equal(restored.page.data.isFirstVisit,true);assert.equal(restored.page.data.importId,'import-done');assert.equal(restored.page.data.entryMode,'file');assert.match(restored.page.data.draftNotice,/已恢复/);assert.equal(restored.calls.filter(x=>x.kind==='upload').length,0);
 const other=harness({storage:h.storage});await other.load({customerId:'another-customer'});assert.equal(other.page.data.transcript,'');assert.equal(other.page.data.customerId,'another-customer');
 const id=restored.page.draftId;restored.page.changeCustomer();restored.page.confirmSelectedCustomer({id:'c2',name:'客户二'});assert.equal(restored.page.draftId,id);assert.equal(restored.page.data.transcript,'已转写并人工修改的内容');
});
test('尚未确认的客户搜索词也自动保存，不强制关联客户',async()=>{
 const h=harness();await h.load();h.page.inputCustomerQuery({detail:{value:'未选择的搜索词'}});h.page.inputTranscript({detail:{value:'暂时未选择客户的录入'}});h.page.onUnload();
 const restored=harness({storage:h.storage});await restored.load();assert.equal(restored.page.data.customerId,'');assert.equal(restored.page.data.customerQuery,'未选择的搜索词');assert.equal(restored.page.data.transcript,'暂时未选择客户的录入');
});
test('主动返回保存后再进恢复，空白页不弹窗',async()=>{
 const blank=harness();await blank.load();blank.page.requestBack();assert.equal(blank.modals.length,0);assert.equal(blank.calls.at(-1).kind,'back');
 const h=harness();await h.load();fill(h.page);h.page.requestBack();assert.equal(h.modals.length,1);assert.equal(h.modals[0].confirmText,'保存');assert.equal(h.modals[0].cancelText,'放弃');await h.answer(true);h.page.onHide();h.page.onUnload();
 const restored=harness({storage:h.storage});await restored.load();assert.equal(restored.page.data.transcript,'已转写并人工修改的内容');assert.equal(h.calls.at(-1).kind,'back');
});
test('放弃后生命周期不会把草稿重新写回，也不会清除另一个账号',async()=>{
 const h=harness();await h.load();fill(h.page);h.storage.set('visitEntryV2:workspace:user-b',{transcript:'他人的草稿'});h.storage.set('visitStructuredV2:workspace:user-a',{draftId:h.page.draftId,result:{}});
 h.page.requestBack();await h.answer(false);h.page.onHide();h.page.onUnload();assert.equal(h.storage.has(key),false);assert.equal(h.storage.has('visitStructuredV2:workspace:user-a'),false);assert.equal(h.storage.get('visitEntryV2:workspace:user-b').transcript,'他人的草稿');
 const restored=harness({storage:h.storage});await restored.load();assert.equal(restored.page.data.transcript,'');
});
test('结构化失败保留转写与修改，退出后可直接重提，不重新上传',async()=>{
 // The retry must run through the real action guard with an explicitly authorized actor.
 const permissions={'visit.structure':true,'visit.read':true};
 const h=harness({permissions,guard:true,api:{waitVisitRun:async()=>{throw Object.assign(Error('结构化失败'),{code:'RUN_FAILED',runStatus:'failed'});}}});
 await h.load();fill(h.page);
 h.page.setData({importId:'audio-done',fileName:'访谈.mp3',importStatus:'processing'});
 h.page.pollImport();await tick();
 h.page.inputTranscript({detail:{value:'已转写并人工修改的内容'}});
 assert.equal(h.page.data.hasTranscription,true);
 assert.equal(h.page.data.canSubmit,true);
 await h.page.submitTranscript();
 assert.equal(h.calls.filter(x=>x.kind==='structure').length,1);
 assert.match(h.page.data.errorText,/原文已保留/);
 assert.equal(h.page.data.canSubmit,true);
 assert.equal(h.page.data.transcript,'已转写并人工修改的内容');
 assert.equal(h.storage.has('visitStructureRun:workspace:user-a'),false);
 h.page.onUnload();
 const restored=harness({storage:h.storage,permissions,guard:true});
 await restored.load();
 assert.equal(restored.page.data.transcript,'已转写并人工修改的内容');
 assert.equal(restored.page.data.canSubmit,true);
 restored.page.inputTranscript({detail:{value:'修改后再次提交'}});
 await restored.page.submitTranscript();
 assert.equal(restored.calls.filter(x=>x.kind==='upload').length,0);
 assert.equal(restored.calls.filter(x=>x.kind==='request'&&x.path.startsWith('/visit-imports/')).length,0);
 const submissions=restored.calls.filter(x=>x.kind==='structure');
 assert.equal(submissions.length,1);
 assert.equal(submissions[0].body.text,'修改后再次提交');
 assert.equal(submissions[0].body.source_import_id,'audio-done');
 assert.ok(restored.calls.some(x=>x.kind==='navigate'));
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
 let tries=0;const h=harness({api:{uploadVisitFile:async(path,name,progress,key)=>{assert.equal(path,'temp');if(!tries++)throw Error('网络连接中断');return registered(key,undefined,name);}}});await h.load();
 h.wx.getFileSystemManager=()=>({saveFile:o=>o.fail({errMsg:'quota exceeded'}),removeSavedFile:()=>{}});
 await h.page.uploadFile({path:'temp',name:'audio.mp3'});await h.page.uploadTemporaryFile();
 assert.equal(h.page.data.localFilePath,'temp');assert.equal(h.page.data.localFileTemporary,true);assert.match(h.page.data.errorText,/网络/);
 await h.page.uploadTemporaryFile();await tick();assert.equal(tries,1);assert.equal(h.page.data.uploadIntentState,'retryable_failure');assert.equal(h.page.data.localFilePath,'temp');
 await h.page.retryImport();await tick();assert.equal(tries,2);assert.equal(h.page.data.localFilePath,'');assert.equal(h.page.data.localFileTemporary,false);
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

test('结构化及转写在切账号后完成，不回填、不清理新账号草稿也不跳转',async()=>{
 const result=deferred();const h=harness({api:{waitVisitRun:()=>result.promise}});await h.load();fill(h.page);
 const request=h.page.submitTranscript();await tick();
 h.app.globalData.session.userId='user-b';h.storage.set('visitEntryV2:workspace:user-b',{transcript:'新账号原文'});
 result.resolve({result:{fields:{follow_up_record:'旧账号结果'}}});await request;
 assert.equal(h.storage.has('visitStructuredV2:workspace:user-b'),false);
 assert.equal(h.storage.get('visitEntryV2:workspace:user-b').transcript,'新账号原文');
 assert.equal(h.calls.filter(x=>x.kind==='navigate').length,0);
 const transcription=deferred();const audio=harness({api:{request:()=>transcription.promise}});await audio.load();fill(audio.page);
 audio.page.setData({importId:'old-audio',importStatus:'processing'});audio.page.persist();audio.page.pollImport();
 audio.app.globalData.session.userId='user-b';transcription.resolve({status:'succeeded',extracted_text:'旧账号迟到录音'});await tick();
 assert.equal(audio.page.data.transcript,'已转写并人工修改的内容');
 assert.equal(audio.storage.has('visitEntryV2:workspace:user-b'),false);
});

// MINI002 intent protocol fixtures use complete UUID receipts from the fixed contract.
test('上传响应丢失但已登记：查回原import，不发第二份multipart',async()=>{
 const uploads=[],lookups=[];const h=harness({api:{uploadVisitFile:async(p,n,cb,key)=>{uploads.push(key);throw Error('timeout');},getVisitUploadIntent:async key=>{lookups.push(key);return registered(key);}}});await h.load();
 await h.page.uploadFile({path:'temp',name:'访谈.mp3'});const key=h.page.data.uploadIntentKey;assert.ok(key);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.storage.get(h.page.draftKey).uploadIntentKey,key);
 await h.page.retryImport();await tick();assert.deepEqual(lookups,[key]);assert.equal(uploads.length,1);assert.equal(h.page.data.importId,'20000000-0000-4000-8000-000000000001');assert.equal(h.page.data.localFilePath,'');
});
test('重载后404只解锁并保留原件，用户重试才以同key/同文件上传',async()=>{
 const first=[];const h=harness({api:{uploadVisitFile:async(p,n,cb,key)=>{first.push({p,n,key});throw Error('timeout');}}});await h.load();await h.page.uploadFile({path:'temp',name:'访谈.mp3'});h.page.onUnload();
 const resumed=harness({storage:h.storage});await resumed.load();await tick();assert.equal(resumed.calls.filter(x=>x.kind==='upload').length,0);assert.equal(resumed.page.data.uploadIntentState,'retryable_failure');assert.equal(resumed.page.data.localFilePath,first[0].p);assert.equal(resumed.page.pollTimer,null);
 resumed.page.onShow();await tick();assert.equal(resumed.calls.filter(x=>x.kind==='upload').length,0);
 await resumed.page.retryImport();await tick();const second=resumed.calls.find(x=>x.kind==='upload');assert.ok(second);assert.equal(second.key,first[0].key);assert.equal(second.file,first[0].p);assert.equal(second.name,first[0].n);assert.equal(resumed.calls.filter(x=>x.kind==='lookup').length,1);
});
for(const state of ['processing','uncertain'])test(`查回${state}只保留原件并稍后查询，不重新上传`,async()=>{
 let uploads=0,lookups=0;const h=harness({api:{uploadVisitFile:async()=>{uploads++;throw Error('timeout');},getVisitUploadIntent:async key=>{lookups++;return {upload_intent_id:key,state,id:null,status:null,filename:'访谈.mp3',retryable:false,retry_after_seconds:2};}}});await h.load();await h.page.uploadFile({path:'temp',name:'访谈.mp3'});const key=h.page.data.uploadIntentKey;
 await h.page.retryImport();assert.equal(uploads,1);assert.equal(lookups,1);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.data.uploadIntentKey,key);assert.equal(h.page.data.uploadIntentState,state);assert.equal(h.calls.filter(c=>c.kind==='remove-file').length,0);
 h.page.removeFile();h.page.clearTranscript();assert.equal(h.page.discardDraft(),false);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.data.uploadIntentKey,key);h.page.onUnload();
});
for(const error of [Error('查回网络中断'),Object.assign(Error('旧API无此路径'),{statusCode:404}),Object.assign(Error('权限已失效'),{statusCode:403})])test(`查回失败不得依据状态码猜未受理：${error.message}`,async()=>{
 let uploads=0;const h=harness({api:{uploadVisitFile:async()=>{uploads++;throw Error('timeout');},getVisitUploadIntent:async()=>{throw error;}}});await h.load();await h.page.uploadFile({path:'temp',name:'访谈.mp3'});await h.page.retryImport();assert.equal(uploads,1);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.data.uploadIntentState,'uncertain');
});
test('retryable_failure查回只解锁，用户再次确认才同key重传',async()=>{
 const sent=[];const h=harness({api:{uploadVisitFile:async(p,n,cb,key)=>{sent.push(key);if(sent.length===1)throw Error('timeout');return registered(key);},getVisitUploadIntent:async key=>({upload_intent_id:key,state:'retryable_failure',id:null,status:null,filename:'访谈.mp3',retryable:true})}});await h.load();await h.page.uploadFile({path:'temp',name:'访谈.mp3'});await h.page.retryImport();await tick();assert.equal(sent.length,1);assert.equal(h.page.data.uploadIntentState,'retryable_failure');assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.pollTimer,null);await h.page.retryImport();await tick();assert.equal(sent.length,2);assert.equal(sent[0],sent[1]);assert.equal(h.page.data.localFilePath,'');
});
for(const change of [r=>({...r,upload_intent_id:'10000000-0000-4000-8000-000000000009'}),r=>({...r,id:'not-uuid'}),r=>({...r,filename:''}),r=>({...r,state:undefined})])test('查回缺字段/错意图不删除原件、不进入import轮询',async()=>{
 const h=harness({api:{uploadVisitFile:async()=>{throw Error('timeout');},getVisitUploadIntent:async key=>change(registered(key))}});await h.load();await h.page.uploadFile({path:'temp',name:'访谈.mp3'});await h.page.retryImport();assert.equal(h.page.data.importId,'');assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.calls.filter(c=>c.kind==='request').length,0);assert.equal(h.calls.filter(c=>c.kind==='remove-file').length,0);
});
test('查回跨账号迟到不写草稿也不删除原件',async()=>{
 const pending=deferred();let key;const h=harness({api:{uploadVisitFile:async()=>{throw Error('timeout');},getVisitUploadIntent:k=>{key=k;return pending.promise;}}});await h.load();await h.page.uploadFile({path:'temp',name:'访谈.mp3'});const lookup=h.page.retryImport();await tick();h.app.globalData.session.userId='other';pending.resolve(registered(key));await lookup;assert.equal(h.page.data.importId,'');assert.equal(h.calls.filter(c=>c.kind==='remove-file').length,0);
});
test('发送前无法持久化意图时不发multipart，处理中不能放弃原件',async()=>{
 const h=harness();await h.load();h.page.setData({localFilePath:'saved-audio',fileName:'访谈.mp3'});h.setStorageFailure(true);await h.page.sendUploadIntent();assert.equal(h.calls.filter(c=>c.kind==='upload').length,0);assert.equal(h.page.data.localFilePath,'saved-audio');
 h.setStorageFailure(false);const pending=deferred();h.api.uploadVisitFile=()=>pending.promise;const send=h.page.sendUploadIntent();h.page.clearTranscript();assert.equal(h.page.data.isProcessing,true);assert.equal(h.page.discardDraft(),false);pending.reject(Error('timeout'));await send;
});
test('同key文件冲突不再查回其他内容的成功记录，不删除当前原件',async()=>{
 const h=harness({api:{uploadVisitFile:async()=>{throw Object.assign(Error('冲突'),{code:'UPLOAD_INTENT_CONFLICT'});}}});await h.load();await h.page.uploadFile({path:'temp',name:'访谈.mp3'});assert.equal(h.page.data.uploadIntentState,'conflict');await h.page.retryImport();assert.equal(h.calls.filter(c=>c.kind==='lookup').length,0);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.discardDraft(),false);
});
test('已登记failed识别重试另用稳定key，回放确认后新尝试才换key',async()=>{
 const attempts=[];const id='20000000-0000-4000-8000-000000000001';const h=harness({api:{request:async()=>({id,status:'failed',filename:'访谈.mp3',error_message:'识别失败'}),retryVisitImport:async(id,key)=>{attempts.push(key);if(attempts.length===1)throw Error('timeout');return {id,status:'failed'};}}});await h.load();h.page.setData({importId:id,importStatus:'failed',fileName:'访谈.mp3'});await h.page.retryImport();assert.ok(h.page.data.retryIntentKey);h.page.persist();await h.page.retryImport();await tick();assert.equal(attempts[0],attempts[1]);assert.equal(h.page.data.retryIntentKey,'');await h.page.retryImport();await tick();assert.notEqual(attempts[1],attempts[2]);assert.equal(h.calls.filter(c=>c.kind==='upload').length,0);
});
test('queued继续查看仅轮询，旧版未知上传不自动新建意图或重传',async()=>{
 const h=harness();await h.load();h.page.setData({importId:'known',importStatus:'queued'});await h.page.retryImport();await tick();assert.equal(h.calls.filter(c=>c.kind==='retry-import').length,0);
 const restored=harness({storage:new Map([[key,{draftId:'old',localFilePath:'saved-old',fileName:'旧录音.mp3',importStatus:'failed'}]])});await restored.load();await tick();assert.equal(restored.page.data.uploadIntentState,'legacy_unknown');assert.equal(restored.calls.filter(c=>c.kind==='upload').length,0);assert.equal(restored.page.data.localFilePath,'saved-old');
});

test('临时原件已失效仍先查回已接收记录，不再次尝试本地保存',async()=>{
 const h=harness({api:{uploadVisitFile:async()=>{throw Error('timeout');},getVisitUploadIntent:async key=>registered(key)}});await h.load();
 await h.page.uploadFile({path:'temporary-audio',name:'访谈.mp3'},false,false,true);
 let saves=0;h.wx.getFileSystemManager=()=>({saveFile:o=>{saves++;o.fail({errMsg:'temporary file gone'});},removeSavedFile:()=>{}});
 await h.page.retryImport();await tick();assert.equal(saves,0);assert.equal(h.page.data.importId,'20000000-0000-4000-8000-000000000001');assert.equal(h.page.data.localFilePath,'');
});
test('旧页面切换公司或账号后不能发查回请求',async()=>{
 for(const field of ['workspaceId','userId']){
  const h=harness({api:{uploadVisitFile:async()=>{throw Error('timeout');}}});await h.load();await h.page.uploadFile({path:'temp',name:'访谈.mp3'});
  h.app.globalData.session[field]='another';await h.page.retryImport();assert.equal(h.calls.filter(c=>c.kind==='lookup').length,0);
 }
});
test('同一未确认上传不能换文件；确认并移除后新文件有新key',async()=>{
 const sent=[];const h=harness({api:{uploadVisitFile:async(p,n,cb,key)=>{sent.push(key);throw Error('timeout');},getVisitUploadIntent:async key=>registered(key)}});await h.load();
 await h.page.uploadFile({path:'first',name:'访谈.mp3'});const first=sent[0];await h.page.uploadFile({path:'second',name:'另一份.mp3'});assert.equal(sent.length,1);assert.equal(h.page.data.fileName,'访谈.mp3');assert.equal(h.page.data.uploadIntentKey,first);
 await h.page.retryImport();await tick();h.page.removeFile();assert.equal(h.page.data.uploadIntentKey,'');
 await h.page.uploadFile({path:'second',name:'另一份.mp3'});assert.equal(sent.length,2);assert.notEqual(sent[1],first);
});
test('识别重试响应丢失后重载，保留独立retry-key及原import',async()=>{
 const sent=[];const id='20000000-0000-4000-8000-000000000001';const h=harness({api:{retryVisitImport:async(id,key)=>{sent.push({id,key});throw Error('timeout');}}});await h.load();h.page.setData({importId:id,importStatus:'failed',fileName:'访谈.mp3'});await h.page.retryImport();h.page.onUnload();
 const restored=harness({storage:h.storage});await restored.load();await restored.page.retryImport();await tick();const actual=restored.calls.find(c=>c.kind==='retry-import');assert.equal(actual.key,sent[0].key);assert.equal(actual.id,id);assert.equal(restored.calls.filter(c=>c.kind==='upload').length,0);
});
test('完整接收回执无法持久化时仍保留本地原件',async()=>{
 const pending=deferred();let uploadKey;const h=harness({api:{uploadVisitFile:(p,n,cb,key)=>{uploadKey=key;return pending.promise;}}});await h.load();const sending=h.page.uploadFile({path:'temp',name:'访谈.mp3'});await tick();h.setStorageFailure(true);pending.resolve(registered(uploadKey));await sending;
 assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.calls.filter(c=>c.kind==='remove-file').length,0);assert.equal(h.storage.get(key).uploadIntentKey,uploadKey);assert.equal(h.storage.get(key).uploadIntentState,'sending');
});

const pendingIntent=page=>{page.setData({uploadIntentKey:'10000000-0000-4000-8000-000000000001',uploadIntentState:'uncertain',localFilePath:'saved-audio',fileName:'访谈.mp3',importStatus:'failed'});page.persist();};
const transportCalls=h=>h.calls.filter(c=>['upload','lookup','request','retry-import'].includes(c.kind));
test('实际动作guard：只有upload也能查回原任务，但没有read不自动读取处理结果',async()=>{
 const h=harness({guard:true,permissions:{'visit.upload':true}});await h.load();pendingIntent(h.page);
 h.api.getVisitUploadIntent=async key=>{h.calls.push({kind:'lookup',key});return registered(key);};
 await h.page.retryImport();await tick();assert.deepEqual(transportCalls(h).map(c=>c.kind),['lookup']);assert.ok(h.page.data.importId);assert.match(h.page.data.errorText,/没有查看材料处理结果/);assert.equal(h.page.data.isProcessing,false);assert.equal(h.page.data.canReadVisit,false);
});
test('实际动作guard：只有retry不能查回无id上传，原件/key保留',async()=>{
 const h=harness({guard:true,permissions:{'visit.retry_import':true}});await h.load();pendingIntent(h.page);await h.page.retryImport();assert.equal(transportCalls(h).length,0);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.data.uploadIntentState,'uncertain');
});
for(const status of ['queued','processing','succeeded'])test(`实际动作guard：只有read可继续查看已登记${status}，不上传或重跑`,async()=>{
 const h=harness({guard:true,permissions:{'visit.read':true}});await h.load();h.page.setData({importId:'known-import',importStatus:status});await h.page.retryImport();await tick();assert.deepEqual(transportCalls(h).map(c=>c.kind),['request']);assert.equal(h.page.data.canReadVisit,true);assert.equal(h.page.data.canRetryImport,false);
});
for(const permission of ['visit.upload','visit.read'])test(`实际动作guard：只有${permission}不能重跑已登记failed`,async()=>{
 const h=harness({guard:true,permissions:{[permission]:true}});await h.load();h.page.setData({importId:'known-import',importStatus:'failed'});await h.page.retryImport();assert.equal(transportCalls(h).length,0);
});
test('实际动作guard：只有retry可发失败重跑POST，但不因此获得read',async()=>{
 const h=harness({guard:true,permissions:{'visit.retry_import':true}});await h.load();h.page.setData({importId:'known-import',importStatus:'failed'});await h.page.retryImport();await tick();assert.deepEqual(transportCalls(h).map(c=>c.kind),['retry-import']);assert.match(h.page.data.errorText,/没有查看材料处理结果/);assert.equal(h.page.data.isProcessing,false);
});
for(const state of ['legacy_unknown','conflict'])test(`实际动作guard：${state}只查看本地原因，不需要任何上传/识别/读取权限`,async()=>{
 const h=harness({guard:true,permissions:{}});await h.load();pendingIntent(h.page);h.page.setData({uploadIntentState:state});await h.page.retryImport();h.page.onShow();await tick();assert.equal(transportCalls(h).length,0);assert.match(h.page.data.errorText,/管理员核对/);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.calls.filter(c=>c.kind==='toast').length,0);
});
test('点击时用当前权限，撤权后自动恢复/直调查回和上传也不再请求',async()=>{
 const h=harness({guard:true,permissions:{'visit.upload':true}});await h.load();pendingIntent(h.page);h.app.globalData.session.permissions['visit.upload']=false;
 await h.page.retryImport();h.page.onShow();await h.page.recoverUploadIntent();await h.page.sendUploadIntent();assert.equal(transportCalls(h).length,0);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.data.canUploadVisit,false);assert.match(h.page.data.errorText,/没有上传或查回/);
 h.app.globalData.session.permissions['visit.upload']=true;h.api.getVisitUploadIntent=async key=>{h.calls.push({kind:'lookup',key});throw Error('网络未恢复');};await h.page.retryImport();assert.equal(transportCalls(h).length,1);
});
test('读取权撤销后自动poll停止且解除忙碌；直调识别重试不能绕过retry权',async()=>{
 const h=harness({guard:true,permissions:{'visit.read':true}});await h.load();h.page.setData({importId:'known-import',importStatus:'queued',isProcessing:true});h.app.globalData.session.permissions['visit.read']=false;h.page.pollImport();assert.equal(transportCalls(h).length,0);assert.equal(h.page.data.isProcessing,false);assert.match(h.page.data.errorText,/没有查看材料处理结果/);
 h.page.setData({importStatus:'failed'});await h.page.retryRegisteredImport();assert.equal(transportCalls(h).length,0);assert.match(h.page.data.errorText,/没有重新识别/);
});

 test('同一客户的不同商机草稿隔离，重开只恢复该入口',async()=>{
  const a=harness();await a.load({customerId:'c1',opportunityId:'o1'});a.page.inputTranscript({detail:{value:'商机一未写完'}});
  const b=harness({storage:a.storage});await b.load({customerId:'c1',opportunityId:'o2'});assert.equal(b.page.data.transcript,'');b.page.inputTranscript({detail:{value:'商机二未写完'}});
  const restored=harness({storage:a.storage});await restored.load({customerId:'c1',opportunityId:'o1'});assert.equal(restored.page.data.transcript,'商机一未写完');
 });
 test('同动作另页更新或删除后，旧页生命周期不得覆盖或复活草稿',async()=>{
  const a=harness();await a.load();fill(a.page);const b=harness({storage:a.storage});await b.load();
  b.page.inputTranscript({detail:{value:'B页最新'}});a.page.inputTranscript({detail:{value:'A页迟到'}});assert.equal(a.storage.get(key).transcript,'B页最新');assert.match(a.page.data.draftSaveError,/其他页面/);
  a.storage.delete(key);a.page.onUnload();assert.equal(a.storage.has(key),false);
 });

 test('旧无环境草稿仅同账号发现，人工恢复文字不继承远端引用或改写旧键',async()=>{
  const old={draftId:'old',transcript:'旧环境未完成原文',customerId:'old-c',opportunityId:'old-o',importId:'old-import',localFilePath:'expired'};
  const memory=new Map([[key,old],['visitConfirmV2:workspace:user-a',{draftId:'old',values:{follow_up_record:'确认页手动修订',contact_name:'原对接人'},reviewRunId:'old-review'}]]);
  const h=harness({storage:memory,api:{getBaseUrl:()=> 'https://current.invalid/api/v1'}});await h.load();assert.equal(h.page.data.legacyDraftAvailable,true);assert.equal(h.page.data.transcript,'');
  h.page.restoreLegacyDraft();await h.answer(false);assert.equal(h.page.data.transcript,'');
  h.page.restoreLegacyDraft();await h.answer(true);assert.match(h.page.data.transcript,/旧环境未完成原文/);assert.match(h.page.data.transcript,/确认页手动修订/);assert.equal(h.page.data.customerId,'');assert.equal(h.page.data.opportunityId,'');assert.equal(h.page.data.importId,'');assert.equal(h.page.data.localFilePath,'');
  assert.deepEqual(memory.get(key),old);assert.equal(h.calls.filter(x=>['structure','upload'].includes(x.kind)).length,0);
  const other=harness({storage:memory,user:'user-b',api:{getBaseUrl:()=> 'https://current.invalid/api/v1'}});await other.load();assert.equal(other.page.data.legacyDraftAvailable,false);
 });
 test('当前环境已有文字时不覆盖，旧稿恢复保存失败时原稿仍保留',async()=>{
  const memory=new Map([[key,{transcript:'旧稿'}]]),h=harness({storage:memory,api:{getBaseUrl:()=> 'http://current.invalid/api/v1'}});await h.load();h.page.inputTranscript({detail:{value:'当前稿'}});h.page.restoreLegacyDraft();assert.equal(h.modals.length,0);
  h.page.inputTranscript({detail:{value:''}});h.setStorageFailure(true);h.page.restoreLegacyDraft();await h.answer(true);assert.equal(h.page.data.transcript,'');assert.match(h.page.data.draftSaveError,/未能保存/);assert.equal(memory.get(key).transcript,'旧稿');
 });

test('422明确拒绝后保留原文与文件并解锁，重进页面不自动上传',async()=>{
 const sent=[];const h=harness({api:{uploadVisitFile:async(p,n,cb,key)=>{sent.push({p,key});throw Object.assign(Error('请求内容不符合要求，请检查后重试，本地文件已保留'),{statusCode:422,serverCode:'VALIDATION_ERROR',outcomeUnknown:false,recoveryAction:'correct_request'});}}});await h.load();fill(h.page);
 await h.page.uploadFile({path:'temp',name:'录音.webm'});const uploadKey=h.page.data.uploadIntentKey;
 assert.equal(h.page.data.uploadIntentState,'retryable_failure');assert.equal(h.page.data.isProcessing,false);assert.equal(h.page.unresolvedUpload(),false);assert.equal(h.page.pollTimer,null);assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.page.data.transcript,'已转写并人工修改的内容');assert.doesNotMatch(h.page.data.errorText,/继续查回/);assert.equal(h.calls.filter(c=>c.kind==='remove-file').length,0);
 h.page.onShow();await tick();assert.equal(sent.length,1);await h.page.retryImport();assert.equal(sent.length,2);assert.deepEqual(sent[1],sent[0]);assert.equal(h.page.data.uploadIntentKey,uploadKey);
 h.page.onUnload();const restored=harness({storage:h.storage});await restored.load();assert.equal(transportCalls(restored).length,0);assert.equal(restored.page.data.localFilePath,'saved-audio');
 restored.page.removeFile();assert.equal(restored.page.data.localFilePath,'');assert.equal(restored.page.data.transcript,'已转写并人工修改的内容');assert.equal(restored.calls.filter(c=>c.kind==='remove-file').length,1);
});
test('带结果未知或check_status的失败仍只查回，不能误解锁删除原件',async()=>{
 for(const detail of [{statusCode:500,outcomeUnknown:true},{statusCode:422,outcomeUnknown:true},{statusCode:422,recoveryAction:'check_status'}]){
  const h=harness({api:{uploadVisitFile:async()=>{throw Object.assign(Error('请核对原提交结果'),detail);}}});await h.load();await h.page.uploadFile({path:'temp',name:'录音.webm'});
  assert.equal(h.page.data.uploadIntentState,'uncertain');assert.equal(h.page.unresolvedUpload(),true);h.page.removeFile();assert.equal(h.page.data.localFilePath,'saved-audio');assert.equal(h.calls.filter(c=>c.kind==='remove-file').length,0);h.page.onUnload();
 }
});

const followCustomer={id:'10000000-1111-4111-8111-111111111111',name:'公司客户'};
const followOpportunity={opportunity_id:'20000000-1111-4111-8111-111111111111',name:'客户商机',customer_reference:followCustomer,can_follow_up:true};
const optionPage=items=>({items,total:items.length,limit:20,offset:0});
function inlineFollowUpHarness({api={},storage,complete=false}={}) {
 const calls=[],h=harness({storage,permissions:{'visit.create':complete,'customer.read':false,'opportunity.read':false},api:{
  listCompanyCustomerFollowUpOptions:async options=>{calls.push({kind:'customers',...options});return optionPage([followCustomer]);},
  listCompanyOpportunityFollowUpOptions:async options=>{calls.push({kind:'opportunities',...options});return optionPage([]);},
  getCustomerFollowUpAccess:async id=>{calls.push({kind:'customer-access',id});return {customer_reference:followCustomer,can_follow_up:true};},
  getOpportunityFollowUpAccess:async id=>{calls.push({kind:'opportunity-access',id});return followOpportunity;},
  getCustomerReference:async()=>{throw Error('不得改用客户详情读取权限');},getOpportunityDetailOverview:async()=>{throw Error('不得改用商机详情读取权限');},
  ...api,
 }});
 Object.assign(h.app.globalData.session.capabilities,{company_customer_follow_up:true,company_opportunity_follow_up:true});
 return {...h,followCalls:calls};
}
const selectInlineCustomer=page=>page.chooseInlineFollowUpCustomer({currentTarget:{dataset:{id:followCustomer.id}}});
test('小程序内嵌客户卡仅用跟进目录及核权；无商机也可提交客户跟进',async()=>{
 const h=inlineFollowUpHarness();await h.load();h.page.inputTranscript({detail:{value:'客户跟进原文'}});
 await selectInlineCustomer(h.page);
 assert.equal(h.page.data.customerConfirmed,true);assert.equal(h.page.data.followUpResolved,true);assert.equal(h.page.data.targetKind,'customer');assert.equal(h.page.data.opportunityId,'');assert.equal(h.page.data.canSubmit,true);
 assert.equal(h.page.data.followUpPickerMode,'opportunities');assert.equal(h.page.data.followUpItems.length,0);
 assert.equal(h.followCalls.find(row=>row.kind==='opportunities').customer_id,followCustomer.id);
 assert.equal(h.page.data.canCompleteVisitEntry,false);
});
test('小程序内嵌商机搜索作废旧关键词回包，选择仍重新核权',async()=>{
 const old=deferred();const h=inlineFollowUpHarness({api:{listCompanyOpportunityFollowUpOptions:async options=>options.q==='旧词'?old.promise:optionPage(options.q?[{...followOpportunity,name:options.q}]:[]),getOpportunityFollowUpAccess:async()=>{throw Error('商机当前不可跟进');}}});
 await h.load();await selectInlineCustomer(h.page);
 h.page.inputFollowUpSearch({detail:{value:'旧词'}});const pending=h.page.searchInlineFollowUp();
 h.page.inputFollowUpSearch({detail:{value:'新词'}});await h.page.searchInlineFollowUp();old.resolve(optionPage([{...followOpportunity,name:'旧词'}]));await pending;
 assert.equal(h.page.data.followUpItems[0].name,'新词');assert.equal(h.page.data.followUpLoading,false);
 h.page.inputTranscript({detail:{value:'原文保留'}});await h.page.chooseInlineFollowUpOpportunity({currentTarget:{dataset:{id:followOpportunity.opportunity_id}}});
 assert.equal(h.page.data.followUpResolved,false);assert.equal(h.page.data.canSubmit,false);assert.match(h.page.data.opportunityResolveError,/不可跟进/);assert.equal(h.page.data.transcript,'原文保留');
});
test('小程序清关联、更换与完整拜访切换保留草稿，录音启动和停止期间锁住目标',async()=>{
 const h=inlineFollowUpHarness({complete:true,api:{listCompanyOpportunityFollowUpOptions:async()=>optionPage([followOpportunity])}});await h.load();await selectInlineCustomer(h.page);
 h.page.inputTranscript({detail:{value:'人工修改原文'}});h.page.setData({importId:'already-uploaded',importStatus:'succeeded',localFilePath:'saved-audio',fileName:'录音.mp3',homeAudioHandoff:true});h.page.persist();const draftId=h.page.draftId;
 await h.page.chooseInlineFollowUpOpportunity({currentTarget:{dataset:{id:followOpportunity.opportunity_id}}});assert.equal(h.page.data.opportunityId,followOpportunity.opportunity_id);
 await h.page.clearInlineFollowUpOpportunity();assert.equal(h.page.data.opportunityId,'');assert.equal(h.page.data.followUpResolved,true);assert.equal(h.page.data.followUpPickerMode,'opportunities');
 for(const flag of ['isStarting','isStopping']){h.page.setData({[flag]:true});await h.page.changeCustomer();await h.page.useCompleteVisitEntry();assert.equal(h.page.data.customerId,followCustomer.id);assert.equal(h.page.data.followUpOnly,true);h.page.setData({[flag]:false});}
 await h.page.useCompleteVisitEntry();assert.equal(h.page.data.followUpOnly,false);assert.equal(h.page.data.customerId,'');
 await h.page.useCompanyFollowUpEntry();await selectInlineCustomer(h.page);await h.page.changeCustomer();assert.equal(h.page.data.customerId,'');assert.equal(h.page.data.opportunityId,'');
 for(const [field,value] of Object.entries({transcript:'人工修改原文',importId:'already-uploaded',localFilePath:'saved-audio',homeAudioHandoff:true}))assert.equal(h.page.data[field],value);
 assert.equal(h.page.draftId,draftId);h.page.onUnload();
 const restored=inlineFollowUpHarness({storage:h.storage});await restored.load();assert.equal(restored.page.data.transcript,'人工修改原文');assert.equal(restored.page.data.importId,'already-uploaded');assert.equal(restored.page.data.localFilePath,'saved-audio');assert.equal(restored.page.draftId,draftId);
});
test('小程序旧草稿核权失败仍能重试或更换，旧核权回包不复活目标',async()=>{
 const pending=deferred();let attempts=0;
 const h=inlineFollowUpHarness({api:{getCustomerFollowUpAccess:async()=>{if(++attempts===1)throw Error('旧客户已不可跟进');return pending.promise;}}});
 h.storage.set(key,{draftId:'existing',followUpOnly:true,customerId:followCustomer.id,customerName:'旧客户',transcript:'原文',importId:'old-file',importStatus:'succeeded'});
 h.page.onLoad();await tick();assert.equal(h.page.data.followUpResolved,false);assert.match(h.page.data.opportunityResolveError,/不可跟进/);
 const retry=h.page.retryInlineFollowUpTarget();h.page.onUnload();pending.resolve({customer_reference:followCustomer,can_follow_up:true});await retry;assert.equal(h.page.data.followUpResolved,false);
 const recovered=inlineFollowUpHarness({storage:h.storage,api:{getCustomerFollowUpAccess:async()=>{throw Error('旧客户已不可跟进');}}});await recovered.load();await recovered.page.changeCustomer();
 assert.equal(recovered.page.data.customerId,'');assert.equal(recovered.page.data.followUpPickerOpen,true);assert.equal(recovered.page.data.followUpPickerMode,'customers');assert.equal(recovered.page.data.followUpItems[0].id,followCustomer.id);
 assert.equal(recovered.page.data.transcript,'原文');assert.equal(recovered.page.data.importId,'old-file');assert.equal(recovered.page.draftId,'existing');
});
