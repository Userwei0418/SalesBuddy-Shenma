const intent=require('./visitUploadIntent');
const EMPTY={uploadIntentKey:'',uploadIntentState:'',retryIntentKey:''};
const draftFields=Object.keys(EMPTY);
function unresolvedMessage(error){
 const message=String(error&&error.message||'');
 if(!message||error.code==='NETWORK_ERROR'||/^(?:uploadFile|request):fail\b/.test(message))return '上传结果尚未确认，原文件已保留。请继续查回。';
 return message+'；原文件已保留，请稍后继续查回。';
}
function create(api){return {
 unresolvedUpload(){return !this.data.importId&&['sending','processing','uncertain','legacy_unknown','conflict'].includes(this.data.uploadIntentState);},
 keepUnresolvedUpload(){if(!this.unresolvedUpload())return false;this.setData({errorText:'上传结果尚未确认，本地文件已保留，请先继续查回。'});return true;},
 async continueFileUpload(){
  if(this.data.isProcessing)return;
  if(this.data.uploadIntentState==='legacy_unknown'){this.fail('旧版上传结果无法按标识查回，请联系管理员核对。本地文件已保留，暂不重复上传。');return;}
  if(this.data.uploadIntentState==='conflict'){this.fail('文件与本次上传记录不一致，请联系管理员核对。本地文件已保留。');return;}
  if(!this.data.localFilePath||this.data.importId)return;
  if(this.data.uploadIntentKey&&this.data.uploadIntentState!=='new')return this.recoverUploadIntent();
  return this.sendUploadIntent();
 },
 async sendUploadIntent(){
  if(this.data.isProcessing||!this.data.localFilePath||this.data.importId)return;
  if(!this.requireImportPermission('visit.upload'))return;
  const token=this.operationToken(),key=this.data.uploadIntentKey||intent.newKey();
  this.setData({uploadIntentKey:key,uploadIntentState:'sending',importStatus:'uploading',isProcessing:true,errorText:'',statusText:'正在上传文件',uploadProgress:0});
  if(!this.persist()){this.setData({isProcessing:false,uploadIntentState:'new'});return;}
  try{
   const receipt=await api.uploadVisitFile(this.data.localFilePath,this.data.fileName,progress=>{if(this.operationCurrent(token)&&!this.hidden)this.setData({uploadProgress:progress.progress});},key);
   if(!this.operationCurrent(token)||this.data.uploadIntentKey!==key)return;
   return this.acceptUploadIntent(receipt,key);
  }catch(error){
   if(!this.operationCurrent(token)||this.data.uploadIntentKey!==key)return;
   if(error.code==='UPLOAD_INTENT_CONFLICT'||error.serverCode==='UPLOAD_INTENT_CONFLICT'){this.setData({uploadIntentState:'conflict',importStatus:'failed',isProcessing:false});this.fail('文件与本次上传记录不一致，请联系管理员核对。本地文件已保留。');this.persist();return;}
   this.setData({uploadIntentState:'uncertain',importStatus:'failed',isProcessing:false});
   this.fail(unresolvedMessage(error));this.persist();
  }
 },
 acceptUploadIntent(receipt,key){
  intent.validate(receipt,key);
  if(receipt.state==='registered'){
   const path=this.data.localFilePath;
   this.setData({uploadIntentState:'registered',importId:receipt.id,importStatus:receipt.status,fileName:receipt.filename,isProcessing:false,errorText:'',statusText:'文件已接收，正在读取处理结果…'});
   if(!this.persist()){this.setData({isProcessing:false});return;}
   this.removeLocalFile(path);this.setData({localFilePath:'',localFileTemporary:false});this.persist();this.pollImport();return;
  }
  const pending=receipt.state==='processing'||receipt.state==='uncertain';
  this.setData({uploadIntentState:receipt.state,isProcessing:false,importStatus:'failed',errorText:pending?'上传结果正在确认，本地文件已保留，请稍后继续查回。':'上次上传未登记，可用同一标识重试。',statusText:pending?'正在确认上传结果':'待重试上传'});this.persist();
  if(pending&&!this.hidden){clearTimeout(this.pollTimer);const token=this.operationToken();const seconds=Number(receipt.retry_after_seconds);this.pollTimer=setTimeout(()=>{if(this.operationCurrent(token)&&this.data.uploadIntentKey===key&&!this.hidden)this.recoverUploadIntent();},Math.max(2,Number.isFinite(seconds)?seconds:2)*1000);}
 },
 async recoverUploadIntent(){
  if(this.data.isProcessing||!this.data.uploadIntentKey||this.data.importId)return;
  const token=this.operationToken(),key=this.data.uploadIntentKey;
  if(!this.operationCurrent(token))return;
  if(!this.requireImportPermission('visit.upload'))return;
  this.setData({isProcessing:true,errorText:'',statusText:'正在查回本次上传…'});
  try{
   const receipt=await api.getVisitUploadIntent(key);
   if(!this.operationCurrent(token)||this.data.uploadIntentKey!==key)return;
   intent.validate(receipt,key);this.setData({isProcessing:false});
   if(receipt.state==='retryable_failure'){this.setData({uploadIntentState:'retryable_failure'});if(!this.persist())return;return this.sendUploadIntent();}
   return this.acceptUploadIntent(receipt,key);
  }catch(error){
   if(!this.operationCurrent(token)||this.data.uploadIntentKey!==key)return;
   this.setData({isProcessing:false});
   if(error.statusCode===404&&error.serverCode==='UPLOAD_INTENT_NOT_FOUND'){this.setData({uploadIntentState:'retryable_failure'});if(!this.persist())return;return this.sendUploadIntent();}
   this.setData({uploadIntentState:'uncertain',importStatus:'failed'});this.fail(unresolvedMessage(error));this.persist();
  }
 },
 async retryRegisteredImport(){
  if(this.data.isProcessing||!this.data.importId)return;
  if(this.data.importStatus!=='failed'){this.pollImport();return;}
  if(!this.requireImportPermission('visit.retry_import'))return;
  const token=this.operationToken(),id=this.data.importId,key=this.data.retryIntentKey||intent.newKey();
  this.setData({retryIntentKey:key,isProcessing:true});if(!this.persist()){this.setData({isProcessing:false});return;}
  try{
   const receipt=await api.retryVisitImport(id,key);
   if(!this.operationCurrent(token)||this.data.importId!==id)return;
   if(!receipt||receipt.id!==id||!intent.STATUSES.includes(receipt.status))throw Error('未收到完整重试回执，请继续查看');
   this.setData({retryIntentKey:'',importStatus:receipt.status,isProcessing:false});this.persist();this.pollImport();
  }catch(error){if(this.operationCurrent(token)&&this.data.importId===id){this.fail(error.message);this.persist();}}
 }
};}
module.exports={EMPTY,draftFields,create};
